#!/usr/bin/env bash
# Find videos that are probably the same content in different versions
# (other resolution, other encode, other container). Candidates are grouped by
# duration, then confirmed by what you see (small perceptual hashes of a few
# frames, resolution independent) and what you hear (loudness curve of the first
# minutes). Read-only, prints a TSV; run it when the CPU is free, it decodes.
#
# Usage: dupes.sh [options] PATH...
#   --tol SEC       duration tolerance between versions (default 3)
#   --frames N      frames compared per file (default 6)
#   --audio-secs N  seconds of audio compared (default 240)
#   --cache FILE    keep signatures between runs (re-computed when size/mtime change)
#   --all           also print pairs judged different
# Output columns: verdict, score_visual (0 identical .. 64), audio_corr (-1..1 or n/a),
#   duration_a, duration_b, res_a, res_b, mbps_a, mbps_b, file_a, file_b
# Verdicts: SAME (visual close and audio matches or both silent), LIKELY (one
# of the two signals is close), DIFFERENT. Judge before deleting anything.
set -eu
export LC_ALL=C
here="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=/dev/null
. "$here/../lib/ui.sh"

TOL=3 FRAMES=6 ASECS=240 CACHE='' ALL=0
while [ $# -gt 0 ]; do
  case "$1" in
    --tol) TOL="$2"; shift 2 ;;
    --frames) FRAMES="$2"; shift 2 ;;
    --audio-secs) ASECS="$2"; shift 2 ;;
    --cache) CACHE="$2"; shift 2 ;;
    --all) ALL=1; shift ;;
    -h | --help) sed -n '2,/^set -eu/p' "$0" | sed '$d;s/^# \{0,1\}//'; exit 0 ;;
    -*) die "unknown option: $1" ;;
    *) break ;;
  esac
done
[ $# -ge 1 ] || die "usage: dupes.sh [options] PATH... (--help)"
need ffmpeg ffprobe od awk

TAB="$(printf '\t')"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
: >"$tmp/cache.new"
[ -z "$CACHE" ] || [ -f "$CACHE" ] || : >"$CACHE"

stat_of() { stat -c '%s %Y' "$1" 2>/dev/null || stat -f '%z %m' "$1"; }

# frame hash: 9x8 gray thumbnail -> 64 bits (pixel brighter than its right neighbour)
vsig() {
  local f="$1" dur="$2" i t out=""
  for i in $(seq 1 "$FRAMES"); do
    t="$(awk -v d="$dur" -v i="$i" -v n="$FRAMES" 'BEGIN { printf "%.2f", d * i / (n + 1) }')"
    ffmpeg -v error -nostdin -ss "$t" -i "$f" -frames:v 1 -vf "scale=9:8:flags=area,format=gray" -f rawvideo -pix_fmt gray - 2>/dev/null |
      od -An -v -tu1 | tr -s ' \n' ' ' |
      awk '{ s = ""; for (r = 0; r < 8; r++) for (x = 0; x < 8; x++) s = s (($(r * 9 + x + 1) > $(r * 9 + x + 2)) ? "1" : "0"); printf "%s", s }' >"$tmp/h"
    out="$out${out:+,}$(cat "$tmp/h")"
  done
  printf '%s' "$out"
}

# loudness curve: RMS level of each second (dB), commas
asig() {
  ffmpeg -v error -nostdin -t "$ASECS" -i "$1" -vn -ac 1 \
    -af "aresample=8000,asetnsamples=n=8000:p=0,astats=metadata=1:reset=1,ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-" \
    -f null - 2>/dev/null |
    awk -F= '/RMS_level/ { v = $2; if (v == "-inf" || v == "inf" || v == "nan") v = -90; printf "%s%s", (n++ ? "," : ""), v }'
}

# visual distance: mean Hamming distance over the frames
vdist() {
  awk -v a="$1" -v b="$2" 'BEGIN {
    na = split(a, x, ","); nb = split(b, y, ","); n = (na < nb ? na : nb)
    if (n == 0) { print 64; exit }
    for (i = 1; i <= n; i++) { d = 0; for (k = 1; k <= 64; k++) if (substr(x[i], k, 1) != substr(y[i], k, 1)) d++; tot += d }
    printf "%.1f", tot / n }'
}

# audio similarity: best Pearson correlation over shifts of +-3 s; n/a when one side is flat
acorr() {
  awk -v a="$1" -v b="$2" 'BEGIN {
    na = split(a, x, ","); nb = split(b, y, ",")
    if (na < 20 || nb < 20) { print "n/a"; exit }
    best = -2
    for (s = -3; s <= 3; s++) {
      n = 0; sx = sy = sxx = syy = sxy = 0
      for (i = 1; i <= na; i++) { j = i + s; if (j < 1 || j > nb) continue
        n++; sx += x[i]; sy += y[j]; sxx += x[i] * x[i]; syy += y[j] * y[j]; sxy += x[i] * y[j] }
      if (n < 20) continue
      vx = sxx - sx * sx / n; vy = syy - sy * sy / n
      if (vx < 1 || vy < 1) { flat = 1; continue }
      r = (sxy - sx * sy / n) / sqrt(vx * vy)
      if (r > best) best = r
    }
    if (best < -1) print (flat ? "flat" : "n/a"); else printf "%.2f", best }'
}

# --- probe every file: path, size, mtime, duration, res, mbps
find "$@" -type f \( -iname '*.mp4' -o -iname '*.m4v' -o -iname '*.mkv' -o -iname '*.avi' -o -iname '*.mov' -o -iname '*.ts' -o -iname '*.wmv' \) -print | sort >"$tmp/files"
[ -s "$tmp/files" ] || die "no video found"
: >"$tmp/meta"
while IFS= read -r f; do
  d="$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$f" 2>/dev/null || true)"
  [ -n "$d" ] || { warn "unreadable: $f"; continue; }
  r="$(ffprobe -v error -select_streams v:0 -show_entries stream=width,height -of csv=s=x:p=0 "$f" 2>/dev/null | head -n 1)"
  # shellcheck disable=SC2046 # "size mtime" split on purpose
  set -- $(stat_of "$f")
  mb="$(awk -v s="$1" -v d="$d" 'BEGIN { printf "%.1f", (d > 0 ? s * 8 / d / 1e6 : 0) }')"
  printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$f" "$1" "$2" "$d" "${r:-?}" "$mb" >>"$tmp/meta"
done <"$tmp/files"

# --- cluster by duration (sorted, neighbours within TOL)
sort -t"$TAB" -k4,4n "$tmp/meta" | awk -F'\t' -v tol="$TOL" '
  { if (NR > 1 && $4 - last > tol) g++; last = $4; print g "\t" $0 }' >"$tmp/clusters"
awk -F'\t' '{ c[$1]++ } END { for (k in c) if (c[k] > 1) print k }' "$tmp/clusters" | sort -n >"$tmp/multi"
nclu="$(wc -l <"$tmp/multi" | tr -d ' ')"
info "$(wc -l <"$tmp/meta" | tr -d ' ') videos, $nclu duration cluster(s) with more than one file"

sig_of() { # file -> prints "vsig<TAB>asig" (cached)
  local f="$1" dur="$2" hit v a
  if [ -n "$CACHE" ]; then
    hit="$(awk -F'\t' -v k="$f" -v s="$(stat_of "$f")" '$1 == k && $2 " " $3 == s { print $4 "\t" $5; exit }' "$CACHE" 2>/dev/null || true)"
    [ -z "$hit" ] || { printf '%s' "$hit"; return 0; }
  fi
  v="$(vsig "$f" "$dur")"; a="$(asig "$f")"
  if [ -n "$CACHE" ]; then
    # shellcheck disable=SC2046
    set -- $(stat_of "$f")
    printf '%s\t%s\t%s\t%s\t%s\n' "$f" "$1" "$2" "$v" "$a" >>"$CACHE"
  fi
  printf '%s\t%s' "$v" "$a"
}

printf 'verdict\tscore_visual\taudio_corr\tduration_a\tduration_b\tres_a\tres_b\tmbps_a\tmbps_b\tfile_a\tfile_b\n'
while read -r g; do
  awk -F'\t' -v g="$g" '$1 == g' "$tmp/clusters" | cut -f2- >"$tmp/grp"
  n="$(wc -l <"$tmp/grp" | tr -d ' ')"
  [ "$n" -le 12 ] || { warn "cluster of $n files skipped (raise --tol carefully or split)"; continue; }
  : >"$tmp/sigs"
  while IFS="$TAB" read -r f _ _ d _ _; do
    printf '%s\t%s\n' "$f" "$(sig_of "$f" "$d")" >>"$tmp/sigs"
  done <"$tmp/grp"
  i=1
  while [ "$i" -lt "$n" ]; do
    j=$((i + 1))
    while [ "$j" -le "$n" ]; do
      A="$(sed -n "${i}p" "$tmp/grp")"; B="$(sed -n "${j}p" "$tmp/grp")"
      SA="$(sed -n "${i}p" "$tmp/sigs")"; SB="$(sed -n "${j}p" "$tmp/sigs")"
      va="$(echo "$SA" | cut -f2)"; aa="$(echo "$SA" | cut -f3)"
      vb="$(echo "$SB" | cut -f2)"; ab="$(echo "$SB" | cut -f3)"
      vd="$(vdist "$va" "$vb")"; ac="$(acorr "$aa" "$ab")"
      verdict="$(awk -v v="$vd" -v a="$ac" 'BEGIN {
        vis = (v <= 12); aud = (a != "n/a" && a != "flat" && a + 0 >= 0.85); sil = (a == "flat" || a == "n/a")
        if (vis && (aud || sil)) print "SAME"; else if (vis || aud) print "LIKELY"; else print "DIFFERENT" }')"
      if [ "$verdict" != DIFFERENT ] || [ "$ALL" = 1 ]; then
        printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$verdict" "$vd" "$ac" \
          "$(echo "$A" | cut -f4)" "$(echo "$B" | cut -f4)" "$(echo "$A" | cut -f5)" "$(echo "$B" | cut -f5)" \
          "$(echo "$A" | cut -f6)" "$(echo "$B" | cut -f6)" "$(echo "$A" | cut -f1)" "$(echo "$B" | cut -f1)"
      fi
      j=$((j + 1))
    done
    i=$((i + 1))
  done
done <"$tmp/multi"
