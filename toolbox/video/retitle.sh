#!/usr/bin/env bash
# Make the container "title" tag of a video match its release name (the file name
# without extension), so a player's title bar shows the same name as the file.
# Stream copy only (no re-encode): the file is rewritten next to itself, checked
# (duration, number of streams) and then swapped in, keeping its modification time.
# Usage: retitle.sh [--fix] [--bin DIR] PATH...
#   PATH   files or folders (scanned recursively: mp4 m4v mov mkv)
#   --fix  rewrite the files whose title differs; without it the script only reports
#   --bin  folder with ffmpeg and ffprobe when they are not in PATH (a NAS)
# Needs ffmpeg and ffprobe. Folders named like .tmp or .claude are skipped.
set -eu
export LC_ALL=C
here="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=/dev/null
. "$here/../lib/ui.sh"

FIX=0 BIN="${FFBIN:-}"
while [ $# -gt 0 ]; do
  case "$1" in
    --fix) FIX=1; shift ;;
    --bin) BIN="$2"; shift 2 ;;
    -h | --help) sed -n '2,/^set -eu/p' "$0" | sed '$d;s/^# \{0,1\}//'; exit 0 ;;
    -*) die "unknown option: $1" ;;
    *) break ;;
  esac
done
[ $# -ge 1 ] || die "usage: retitle.sh [--fix] [--bin DIR] PATH... (--help)"
[ -z "$BIN" ] || PATH="$BIN:$PATH"
need ffmpeg ffprobe

dur() { ffprobe -v error -show_entries format=duration -of csv=p=0 "$1" 2>/dev/null; }
nstreams() { ffprobe -v error -show_entries stream=index -of csv=p=0 "$1" 2>/dev/null | grep -c . || true; }
mtime_copy() { touch -r "$1" "$2" 2>/dev/null || true; }

find "$@" -type f \( -iname '*.mp4' -o -iname '*.m4v' -o -iname '*.mov' -o -iname '*.mkv' \) -not -path '*/.tmp/*' -not -path '*/.claude/*' -not -name '.*' | sort |
  while IFS= read -r f; do
    base="$(basename "$f")"; want="${base%.*}"
    have="$(ffprobe -v error -show_entries format_tags=title -of csv=p=0 "$f" 2>/dev/null || true)"
    [ "$have" != "$want" ] || continue
    if [ "$FIX" = 0 ]; then warn "title differs: $base  [${have:-empty}]"; continue; fi
    case "$base" in *.mkv) fmt="-f matroska" ;; *) fmt="-f mp4 -movflags +faststart" ;; esac
    tmp="$(dirname "$f")/.retitle.$$.${base##*.}"
    # shellcheck disable=SC2086 # format flags are a word list
    if ffmpeg -v error -nostdin -y -i "$f" -map 0 -c copy -map_chapters 0 -map_metadata 0 -metadata "title=$want" $fmt "$tmp" 2>/dev/null &&
      [ "$(nstreams "$tmp")" = "$(nstreams "$f")" ] &&
      awk -v a="$(dur "$f")" -v b="$(dur "$tmp")" 'BEGIN { exit !(a - b < 1 && b - a < 1) }'; then
      mtime_copy "$f" "$tmp"; mv -f "$tmp" "$f"; ok "title set: $base"
    else
      rm -f "$tmp"; ko "could not retitle: $base"
    fi
  done
