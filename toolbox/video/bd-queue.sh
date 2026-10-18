#!/usr/bin/env bash
# Queue lines (for queue.sh / pipeline.sh) for the streams of a Blu-ray folder: the main
# title and, by default, every other stream that lasts long enough to be a trailer or a
# bonus, so the whole disc can be transcoded to the same format. Prints queue.tsv lines;
# append them to your queue yourself.
# Usage: bd-queue.sh [options] DISC_FOLDER NAME
#   NAME            base of the output names: <NAME>.x265.mp4 and <NAME>.Extra<stream>.x265.mp4
#   --dest DIR      output folder written in the queue lines (default: the disc's parent folder)
#   --prefix P      job id prefix (default j)   --start N   first job number (default 100)
#   --min-secs N    skip streams shorter than N seconds (default 20)
#   --extras-only   do not print the main title (already queued)
#   --opts STR      reencode.sh options (default: --audio compat --audio-langs all --sub-langs none --crf 21 --preset slow)
#   --bin DIR       folder with ffprobe when it is not in PATH
# Needs ffprobe. Source paths are those seen by this machine (run it on the host of the files).
set -eu
export LC_ALL=C
die() { echo "ko $*" >&2; exit 1; }
DEST='' PREFIX=j START=100 MINS=20 EXTRAS_ONLY=0 BIN="${FFBIN:-}"
OPTS='--audio compat --audio-langs all --sub-langs none --crf 21 --preset slow'
while [ $# -gt 0 ]; do
  case "$1" in
    --dest) DEST="$2"; shift 2 ;;
    --prefix) PREFIX="$2"; shift 2 ;;
    --start) START="$2"; shift 2 ;;
    --min-secs) MINS="$2"; shift 2 ;;
    --extras-only) EXTRAS_ONLY=1; shift ;;
    --opts) OPTS="$2"; shift 2 ;;
    --bin) BIN="$2"; shift 2 ;;
    -h | --help) sed -n '2,/^set -eu/p' "$0" | sed '$d;s/^# \{0,1\}//'; exit 0 ;;
    -*) die "unknown option: $1" ;;
    *) break ;;
  esac
done
[ $# -eq 2 ] || die "usage: bd-queue.sh [options] DISC_FOLDER NAME (--help)"
disc="$1" name="$2"
[ -d "$disc/BDMV/STREAM" ] || die "no BDMV/STREAM in $disc"
[ -z "$BIN" ] || PATH="$BIN:$PATH"
command -v ffprobe >/dev/null 2>&1 || die "ffprobe not found"
[ -n "$DEST" ] || DEST="$(cd "$(dirname "$disc")" && pwd)"
disc="$(cd "$disc" && pwd)"

size() { stat -c %s "$1" 2>/dev/null || stat -f %z "$1"; }
main="$(find "$disc/BDMV/STREAM" -type f -iname '*.m2ts' | while IFS= read -r f; do printf '%s\t%s\n' "$(size "$f")" "$f"; done | sort -nr | head -n 1 | cut -f2)"
n="$START"
find "$disc/BDMV/STREAM" -type f -iname '*.m2ts' | sort | while IFS= read -r f; do
  id="$(basename "$f" | sed 's/\.[^.]*$//')"
  if [ "$f" = "$main" ]; then
    [ "$EXTRAS_ONLY" = 0 ] || continue
    out="$DEST/$name.x265.mp4"
  else
    d="$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$f" 2>/dev/null || true)"
    awk -v d="${d:-0}" -v m="$MINS" 'BEGIN { exit !(d >= m) }' || continue
    out="$DEST/$name.Extra$id.x265.mp4"
  fi
  printf '%s%s\t%s\t%s\t%s\n' "$PREFIX" "$n" "$f" "$out" "$OPTS"
  n=$((n + 1))
done
