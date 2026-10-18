#!/usr/bin/env bash
# shellcheck disable=SC2086,SC2029 # SSH_OPTS is a word list; remote commands are quoted with printf %q
# Run a queue of reencode.sh jobs on this machine for files that live on another
# one (a NAS): fetch, encode, push back, record the state. Sequential,
# resumable, meant to be started in the background and checked later.
#
# Usage: queue.sh [--remote HOST] --home DIR [--work DIR]
#   --remote HOST   ssh host that holds the files and DIR (omit for local files)
#   --home DIR      folder (on HOST) with queue.tsv; state.tsv, STATUS and logs/ are written there
#   --work DIR      scratch space on this machine (default: $TMPDIR/toolbox-queue)
#   --ssh-opts STR  ssh options (default: BatchMode, chacha20-poly1305 cipher, no compression)
# While a job encodes, the next source is already being copied (copying is bound by
# the NAS disks, encoding by the CPU). Parallel copies do not help on spinning disks.
# queue.tsv, tab separated, "#" lines ignored:
#   id <TAB> source path <TAB> destination path <TAB> reencode.sh options
# The destination is written next to the final name as .part, then renamed. The
# source is never modified. A job whose id is already "done" in state.tsv is
# skipped, so rerunning the command resumes. Only ids are printed, never names.
# STATUS holds "running <date>" while the queue runs, then "finished <date> done=N failed=M".
# A Mac: run it under caffeinate -i so it does not sleep, e.g.
#   nohup caffeinate -i queue.sh --remote nas --home /path/home >/dev/null 2>&1 &
set -eu
export LC_ALL=C
here="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=/dev/null
. "$here/../lib/ui.sh"

REMOTE='' HOME_DIR='' WORK="${TMPDIR:-/tmp}/toolbox-queue"
# chacha20 is much lighter than AES on a small NAS CPU; compression is useless on video
SSH_OPTS="-o BatchMode=yes -c chacha20-poly1305@openssh.com -o Compression=no"
while [ $# -gt 0 ]; do
  case "$1" in
    --remote) REMOTE="$2"; shift 2 ;;
    --home) HOME_DIR="$2"; shift 2 ;;
    --work) WORK="$2"; shift 2 ;;
    --ssh-opts) SSH_OPTS="$2"; shift 2 ;;
    -h | --help) sed -n '3,/^set -eu/p' "$0" | sed '$d;s/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done
[ -n "$HOME_DIR" ] || die "usage: queue.sh [--remote HOST] --home DIR [--work DIR] (--help)"
need ffmpeg ffprobe
[ -z "$REMOTE" ] || need ssh

# rrun CMD ARGS...: run on the remote host (or here), arguments safely quoted.
rrun() {
  if [ -n "$REMOTE" ]; then ssh -n $SSH_OPTS "$REMOTE" "$(printf '%q ' "$@")"; else "$@" </dev/null; fi
}
rsize() { rrun stat -c %s "$1" 2>/dev/null || rrun stat -f %z "$1"; }
fetch() { rrun cat "$1" >"$2"; }
push() { if [ -n "$REMOTE" ]; then ssh $SSH_OPTS "$REMOTE" "$(printf 'cat > %q' "$2")" <"$1"; else cat "$1" >"$2"; fi; }
note() { printf '%s\t%s\t%s\t%s\n' "$1" "$2" "$(date '+%Y-%m-%d %H:%M:%S')" "${3:-}" | push_append; }
push_append() {
  if [ -n "$REMOTE" ]; then ssh $SSH_OPTS "$REMOTE" "$(printf 'cat >> %q' "$HOME_DIR/state.tsv")"; else cat >>"$HOME_DIR/state.tsv"; fi
}
status() { printf '%s\n' "$1" | push "/dev/stdin" "$HOME_DIR/STATUS"; }

rrun test -f "$HOME_DIR/queue.tsv" || die "no queue.tsv in $HOME_DIR"
rrun mkdir -p "$HOME_DIR/logs"
mkdir -p "$WORK/in" "$WORK/out" "$WORK/log"
queue="$WORK/queue.tsv"; fetch "$HOME_DIR/queue.tsv" "$queue"
state="$WORK/state.tsv"; : >"$state"
rrun test -f "$HOME_DIR/state.tsv" && fetch "$HOME_DIR/state.tsv" "$state"
done_ids=" $(awk -F'\t' '{ s[$1] = $2 } END { for (k in s) if (s[k] == "done") printf "%s ", k }' "$state") "

status "running $(date '+%Y-%m-%d %H:%M:%S')"
ndone=0 nfail=0
trap 'kill $(jobs -p) 2>/dev/null; exit 1' INT TERM

# pending jobs into parallel arrays (bash 3.2: no mapfile)
J_id=() J_src=() J_dst=() J_opts=() J_pid=()
while IFS="$(printf '\t')" read -r id src dst opts; do
  case "$id" in '' | '#'*) continue ;; esac
  case "$done_ids" in *" $id "*) info "$id already done"; continue ;; esac
  J_id+=("$id") J_src+=("$src") J_dst+=("$dst") J_opts+=("$opts") J_pid+=("")
done <"$queue"
total=${#J_id[@]}

local_of() { printf '%s/in/%s.%s' "$WORK" "${J_id[$1]}" "${J_src[$1]##*.}"; }

# prefetch N: copy the source of job N in the background when there is room
prefetch() {
  local n="$1" size free_kb f
  [ "$n" -lt "$total" ] || return 0
  [ -z "${J_pid[$n]}" ] || return 0
  f="$(local_of "$n")"
  size="$(rsize "${J_src[$n]}" 2>/dev/null || echo 0)"
  [ "$size" -gt 0 ] || return 0
  free_kb="$(df -k "$WORK" | awk 'NR == 2 { print $4 }')"
  [ "$free_kb" -gt $((size * 5 / 2 / 1024 + 1048576)) ] || return 0
  ( fetch "${J_src[$n]}" "$f.part" && mv "$f.part" "$f" ) >/dev/null 2>&1 &
  J_pid[n]=$!
}

n=0
while [ "$n" -lt "$total" ]; do
  id="${J_id[$n]}" src="${J_src[$n]}" dst="${J_dst[$n]}" opts="${J_opts[$n]}"
  note "$id" running
  outname="$(basename "$dst")"; lf="$WORK/log/$id.log"; f="$(local_of "$n")"
  fail() { note "$id" failed "$1"; ko "$id $1"; nfail=$((nfail + 1)); rm -f "$f" "$f.part" "$WORK/out/$outname" "$WORK/out/$outname.part"; }
  size="$(rsize "$src" 2>/dev/null || echo 0)"
  if [ "$size" -le 0 ]; then fail "source not found"; n=$((n + 1)); continue; fi
  prefetch "$n"
  if [ -z "${J_pid[$n]}" ]; then fail "not enough local space"; n=$((n + 1)); continue; fi
  wait "${J_pid[$n]}" || true
  if [ "$(wc -c <"$f" 2>/dev/null | tr -d ' ')" != "$size" ]; then fail "fetch failed or size differs"; n=$((n + 1)); continue; fi
  prefetch $((n + 1))
  # shellcheck disable=SC2086 # options are a word list on purpose
  if ! "$here/reencode.sh" $opts "$f" "$WORK/out/$outname" >"$lf" 2>&1; then
    push "$lf" "$HOME_DIR/logs/$id.log"; fail "encode failed (see logs/$id.log)"; n=$((n + 1)); continue
  fi
  rm -f "$f"
  osize="$(wc -c <"$WORK/out/$outname" | tr -d ' ')"
  if ! push "$WORK/out/$outname" "$dst.part"; then fail "push failed"; n=$((n + 1)); continue; fi
  if [ "$(rsize "$dst.part")" != "$osize" ]; then fail "pushed size differs"; rrun rm -f "$dst.part"; n=$((n + 1)); continue; fi
  rrun mv "$dst.part" "$dst"
  push "$lf" "$HOME_DIR/logs/$id.log"
  note "$id" "done" "$size -> $osize bytes"
  ok "$id done"; ndone=$((ndone + 1))
  rm -f "$WORK/out/$outname" "$lf"
  n=$((n + 1))
done
status "finished $(date '+%Y-%m-%d %H:%M:%S') done=$ndone failed=$nfail"
ok "queue finished: done=$ndone failed=$nfail"
