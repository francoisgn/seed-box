#!/usr/bin/env bash
# shellcheck disable=SC2086,SC2029,SC2015 # SSH_OPTS is a word list; remote commands are quoted with printf %q
# Encode pipeline that survives being offline: sources are fetched from a NAS into an
# "in" folder, encoded locally one at a time, and the results wait in an "out" folder
# until the NAS is reachable again. Each stage is resumable and independent, and the
# state lives on this machine, so a trip away from the NAS loses nothing.
#
# Usage: pipeline.sh COMMAND [options]
#   sync     read queue.tsv and the done jobs from the NAS (needs the NAS)
#   fetch    copy the sources of queued jobs into in/ while there is room (needs the NAS)
#   encode   encode the fetched jobs, in queue order, into out/ (local only)
#   push     send the VALIDATED encodes (the ones in out/OK/) to the NAS (needs the NAS)
#   approve ID...   validate encodes: moves their files from out/ to out/OK/
#   run      sync, then start fetch, encode and push as background loops
#   status   what waits where (also written to STATUS.txt in the work folder)
# Options:
#   --remote HOST   ssh host of the NAS (omit when the files are local)
#   --home DIR      folder on the NAS with queue.tsv (id, source, destination, reencode.sh options)
#   --work DIR      work folder here (default ~/Desktop/Encode): in/ out/ jobs.tsv state.tsv logs/
#   --loop          repeat the stage until nothing is left (what `run` uses)
#   --reserve GB    keep this much disk free when fetching (default 100)
#   --in-cap GB     most the in/ cache may hold (default 150)
# Job stages in state.tsv: queued, fetched, encoded, pushed, failed. Only ids are logged.
# A source is deleted from in/ as soon as its encode succeeds (it still exists on the NAS). Nothing is
# sent to the NAS before you validated it (out/ to out/OK/); when sent, the NAS gets a line in
# replace-pending.tsv (old source, new file, size ratio, codecs, height) so the big source can be
# replaced later in a controlled step.
# Notifications (macOS or Linux) are sent when an encode finishes or fails.
set -eu
export LC_ALL=C
here="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=/dev/null
. "$here/../lib/ui.sh"

CMD="${1:-}"; [ $# -eq 0 ] || shift
REMOTE='' HOME_DIR='' WORK="$HOME/Desktop/Encode" LOOP=0 RESERVE=100 INCAP=150 IDS=()
SSH_OPTS="-o BatchMode=yes -c chacha20-poly1305@openssh.com -o Compression=no"
while [ $# -gt 0 ]; do
  case "$1" in
    --remote) REMOTE="$2"; shift 2 ;;
    --home) HOME_DIR="$2"; shift 2 ;;
    --work) WORK="$2"; shift 2 ;;
    --loop) LOOP=1; shift ;;
    --reserve) RESERVE="$2"; shift 2 ;;
    --in-cap) INCAP="$2"; shift 2 ;;
    -*) die "unknown option: $1" ;;
    *) IDS+=("$1"); shift ;;
  esac
done
[ "$CMD" = approve ] || [ ${#IDS[@]} -eq 0 ] || die "unexpected argument: ${IDS[0]}"
case "$CMD" in sync | fetch | encode | push | approve | run | status) ;; -h | --help | '') sed -n '3,/^set -eu/p' "$0" | sed '$d;s/^# \{0,1\}//'; exit 0 ;; *) die "unknown command: $CMD" ;; esac
need ffmpeg ffprobe rsync
mkdir -p "$WORK/in" "$WORK/out/OK" "$WORK/logs"
JOBS="$WORK/jobs.tsv" STATE="$WORK/state.tsv"
[ -f "$STATE" ] || : >"$STATE"
[ -f "$JOBS" ] || : >"$JOBS"

# ---- helpers
rq() { printf '%q' "$1"; }
reachable() { [ -z "$REMOTE" ] || ssh -n $SSH_OPTS -o ConnectTimeout=8 "$REMOTE" true 2>/dev/null; }
rrun() { if [ -n "$REMOTE" ]; then ssh -n $SSH_OPTS "$REMOTE" "$(printf '%q ' "$@")"; else "$@" </dev/null; fi; }
rsize() { rrun stat -c %s "$1" 2>/dev/null || rrun stat -f %z "$1" 2>/dev/null; }
rcopy_from() { # remote path -> local file (resumable)
  if [ -n "$REMOTE" ]; then rsync -a --partial --inplace -e "ssh $SSH_OPTS" "$REMOTE:$(rq "$1")" "$2"; else cp "$1" "$2"; fi
}
rcopy_to() { # local file -> remote path (resumable)
  if [ -n "$REMOTE" ]; then rsync -a --partial --inplace -e "ssh $SSH_OPTS" "$1" "$REMOTE:$(rq "$2")"; else cp "$1" "$2"; fi
}
note() { printf '%s\t%s\t%s\t%s\n' "$1" "$2" "$(date '+%F %T')" "${3:-}" >>"$STATE"; }
stage_of() { awk -F'\t' -v k="$1" '$1 == k { s = $2 } END { print s }' "$STATE"; }
notify() {
  if command -v osascript >/dev/null 2>&1; then osascript -e "display notification \"$2\" with title \"$1\"" >/dev/null 2>&1 || true
  elif command -v notify-send >/dev/null 2>&1; then notify-send "$1" "$2" || true; fi
}
each_job() { # prints "id<TAB>src<TAB>dst<TAB>opts" for the jobs in the given stage
  awk -F'\t' -v want="$1" 'NR == FNR { s[$1] = $2; next } $1 !~ /^#/ && $1 != "" && s[$1] == want' "$STATE" "$JOBS"
}
remaining() { awk -F'\t' 'NR == FNR { s[$1] = $2; next } $1 !~ /^#/ && $1 != "" && s[$1] != "pushed" && s[$1] != "failed"' "$STATE" "$JOBS" | grep -c . || true; }
free_gb() { df -k "$WORK" | awk 'NR == 2 { printf "%d", $4 / 1048576 }'; }

# ---- sync: queue and done jobs from the NAS
do_sync() {
  reachable || { warn "NAS unreachable, keeping the local queue"; return 1; }
  rrun cat "$HOME_DIR/queue.tsv" >"$JOBS.new" && mv "$JOBS.new" "$JOBS"
  rrun cat "$HOME_DIR/state.tsv" 2>/dev/null | awk -F'\t' '{ s[$1] = $2 } END { for (k in s) if (s[k] == "done") print k }' >"$WORK/.remote-done" || true
  while IFS="$(printf '\t')" read -r id _; do
    case "$id" in '' | '#'*) continue ;; esac
    [ -z "$(stage_of "$id")" ] || continue
    if grep -qx "$id" "$WORK/.remote-done"; then note "$id" pushed "done before the pipeline"; else note "$id" queued; fi
  done <"$JOBS"
  ok "queue synced: $(remaining) job(s) to go"
}

# ---- fetch
fetch_pass() {
  reachable || { warn "NAS unreachable, fetch waits"; return 1; }
  each_job queued | while IFS="$(printf '\t')" read -r id src _ _; do
    base="$(basename "$src")"; f="$WORK/in/${id}__$base"
    size="$(rsize "$src" || echo 0)"
    if [ "${size:-0}" -le 0 ]; then note "$id" failed "source not found"; ko "$id source not found"; continue; fi
    need=$((size / 1073741824 + 1)); used="$(du -sk "$WORK/in" | awk '{ printf "%d", $1 / 1048576 }')"
    if [ $((used + need)) -gt "$INCAP" ] || [ "$(free_gb)" -lt $((need + RESERVE)) ]; then info "$id waits: in/ cache full or low disk"; continue; fi
    if rcopy_from "$src" "$f.part" && [ "$(wc -c <"$f.part" | tr -d ' ')" = "$size" ]; then
      mv "$f.part" "$f"; note "$id" fetched "$size"; ok "$id fetched"
    else warn "$id fetch interrupted, will resume"; break; fi
  done
}

# ---- encode
encode_pass() {
  each_job fetched | while IFS="$(printf '\t')" read -r id src dst opts; do
    base="$(basename "$src")"; outname="$(basename "$dst")"
    f="$WORK/in/${id}__$base"; o="$WORK/out/${id}__$outname"
    [ -f "$f" ] || { note "$id" queued "input missing"; continue; }
    note "$id" encoding
    # shellcheck disable=SC2086 # options are a word list on purpose
    ssize="$(wc -c <"$f" | tr -d ' ')"
    if "$here/reencode.sh" $opts "$f" "$o" >"$WORK/logs/$id.log" 2>&1; then
      rm -f "$f"
      osize="$(wc -c <"$o" | tr -d ' ')"
      vc="$(ffprobe -v error -select_streams v:0 -show_entries stream=codec_name,height -of csv=p=0 "$o" | head -n 1)"
      ac="$(ffprobe -v error -select_streams a:0 -show_entries stream=codec_name -of csv=p=0 "$o" | head -n 1)"
      ac="${ac%%,*}"
      case "${vc%%,*}" in hevc) vn=x265 ;; h264) vn=x264 ;; *) vn="${vc%%,*}" ;; esac
      case "$ac" in aac) an=AAC ;; ac3) an=AC3 ;; eac3) an=EAC3 ;; mp3) an=MP3 ;; opus) an=OPUS ;; '') an=none ;; *) an="$(printf '%s' "$ac" | tr '[:lower:]' '[:upper:]')" ;; esac
      ratio="$(awk -v a="$osize" -v b="$ssize" 'BEGIN { printf "%.2f", (b > 0 ? a / b : 0) }')"
      note "$id" encoded "$osize ratio=$ratio v=$vn a=$an h=${vc##*,} src=$ssize"; ok "$id encoded"; notify "Encode done" "$id"
    else rm -f "$o" "$o.part"; note "$id" failed "encode (logs/$id.log)"; ko "$id encode failed"; notify "Encode failed" "$id"; fi
  done
}

# ---- push
push_pass() {
  reachable || { warn "NAS unreachable, push waits"; return 1; }
  each_job encoded | while IFS="$(printf '\t')" read -r id src dst opts; do
    outname="$(basename "$dst")"; o="$WORK/out/OK/${id}__$outname"
    [ -f "$o" ] || continue # not validated yet: it stays in out/ until you move it to out/OK/
    # safety net: no old release title inside the file (a player would show it instead of the file name)
    if [ -n "$(ffprobe -v error -show_entries format_tags=title -of csv=p=0 "$o" 2>/dev/null)" ]; then
      ffmpeg -v error -nostdin -y -i "$o" -map 0 -c copy -map_chapters 0 -metadata title= -movflags +faststart -f mp4 "$o.fix" && mv "$o.fix" "$o" || { rm -f "$o.fix"; continue; }
    fi
    osize="$(wc -c <"$o" | tr -d ' ')"
    if rcopy_to "$o" "$dst.part" && [ "$(rsize "$dst.part")" = "$osize" ] && rrun mv "$dst.part" "$dst"; then
      meta="$(awk -F'\t' -v k="$id" '$1 == k && $2 == "encoded" { m = $4 } END { print m }' "$STATE")"
      rm -f "$o"; note "$id" pushed "$osize"; ok "$id pushed"
      printf '%s\t%s\t%s\t%s\t%s\n' "$id" "$src" "$dst" "$(date '+%F %T')" "$meta" | { [ -n "$REMOTE" ] && ssh $SSH_OPTS "$REMOTE" "$(printf 'cat >> %q' "$HOME_DIR/replace-pending.tsv")" || cat >>"$HOME_DIR/replace-pending.tsv"; }
      printf '%s\tdone\t%s\t%s\n' "$id" "$(date '+%F %T')" "pipeline" | { [ -n "$REMOTE" ] && ssh $SSH_OPTS "$REMOTE" "$(printf 'cat >> %q' "$HOME_DIR/state.tsv")" || cat >>"$HOME_DIR/state.tsv"; }
    else warn "$id push interrupted, will resume"; break; fi
  done
}

status() {
  for s in queued fetched encoding encoded pushed failed; do printf '%-9s %s\n' "$s" "$(each_job "$s" | grep -c . || true)"; done
  printf 'free disk %s GB\n' "$(free_gb)"
  { echo "in/ (waiting for encode):"; find "$WORK/in" -maxdepth 1 -type f ! -name '*.part' -exec du -h {} + 2>/dev/null | sed "s#$WORK/in/##; s/^/  /"
    echo "out/ (encoded, waiting for YOUR validation; move to out/OK/ or: pipeline.sh approve ID):"; find "$WORK/out" -maxdepth 1 -type f -exec du -h {} + 2>/dev/null | sed "s#$WORK/out/##; s/^/  /"
    echo "out/OK/ (validated, sent to the NAS when it is reachable):"; find "$WORK/out/OK" -maxdepth 1 -type f -exec du -h {} + 2>/dev/null | sed "s#$WORK/out/OK/##; s/^/  /"; } | tee "$WORK/STATUS.txt"
}

stage_loop() { # name function pause
  local name="$1" fn="$2" pause="$3" pidf="$WORK/.$1.pid"
  if [ -f "$pidf" ] && kill -0 "$(cat "$pidf")" 2>/dev/null; then die "$name already running (pid $(cat "$pidf"))"; fi
  echo $$ >"$pidf"
  while true; do
    "$fn" || true
    [ "$LOOP" = 1 ] || break
    case "$name" in
      fetch) [ "$(each_job queued | grep -c . || true)" -gt 0 ] || break ;;
      encode) [ "$(( $(each_job queued | grep -c . || true) + $(each_job fetched | grep -c . || true) ))" -gt 0 ] || break ;;
      push) [ "$(remaining)" -gt 0 ] || break ;;
    esac
    sleep "$pause"
  done
  rm -f "$pidf"
}

case "$CMD" in
  sync) do_sync ;;
  fetch) stage_loop fetch fetch_pass 120 ;;
  encode) stage_loop encode encode_pass 60 ;;
  push) stage_loop push push_pass 300 ;;
  status) status ;;
  approve)
    [ ${#IDS[@]} -gt 0 ] || die "usage: pipeline.sh approve ID..."
    for id in "${IDS[@]}"; do
      moved=0
      for f in "$WORK/out/${id}__"*; do [ -f "$f" ] && mv "$f" "$WORK/out/OK/" && moved=1; done
      if [ "$moved" = 1 ]; then ok "$id validated"; else warn "$id: nothing to validate in out/"; fi
    done ;;
  run)
    do_sync || true
    self="$here/pipeline.sh"; common="--remote ${REMOTE:-} --home $HOME_DIR --work $WORK --reserve $RESERVE --in-cap $INCAP --loop"
    # shellcheck disable=SC2086
    { nohup "$self" fetch $common >/dev/null 2>&1 &
      nohup caffeinate -i "$self" encode $common >/dev/null 2>&1 &
      nohup "$self" push $common >/dev/null 2>&1 &
    }
    ok "fetch, encode and push loops started; check with: pipeline.sh status --work $WORK" ;;
esac
