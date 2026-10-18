#!/usr/bin/env bash
# Plain-text record of a full Blu-ray folder, to find and check it later: what it is, how
# big, its stream list with durations, the MediaInfo report of the main title and, on
# request, a SHA-256 manifest. Prints to stdout (the disc folder may be read-only);
# redirect it next to the disc, e.g.  disc-record.sh --title T --year Y DISC > DISC.nfo
# Needs mediainfo (or run it where it exists, e.g. inside a container that ships it).
# Usage: disc-record.sh [--title T] [--studio S] [--year Y] [--manifest] DISC_FOLDER
#   --manifest   print only the SHA-256 manifest (slow on a big disc), for DISC.sha256
set -eu
die() { echo "ko $*" >&2; exit 1; }
TITLE='' STUDIO='' YEAR='' MANIFEST=0
while [ $# -gt 0 ]; do
  case "$1" in
    --title) TITLE="$2"; shift 2 ;;
    --studio) STUDIO="$2"; shift 2 ;;
    --year) YEAR="$2"; shift 2 ;;
    --manifest) MANIFEST=1; shift ;;
    -h | --help) sed -n '2,/^set -eu/p' "$0" | sed '$d;s/^# \{0,1\}//'; exit 0 ;;
    -*) die "unknown option: $1" ;;
    *) break ;;
  esac
done
[ $# -eq 1 ] && [ -d "$1/BDMV/STREAM" ] || die "usage: disc-record.sh [options] DISC_FOLDER (a folder with BDMV/STREAM)"
disc="$1"
sha() { sha256sum "$1" 2>/dev/null || shasum -a 256 "$1"; }
if [ "$MANIFEST" = 1 ]; then
  (cd "$disc" && find . -type f | sort | while IFS= read -r f; do sha "$f"; done)
  exit 0
fi
command -v mediainfo >/dev/null 2>&1 || die "mediainfo not found"
size() { stat -c %s "$1" 2>/dev/null || stat -f %z "$1"; }
main="$(find "$disc/BDMV/STREAM" -type f -iname '*.m2ts' | while IFS= read -r f; do printf '%s\t%s\n' "$(size "$f")" "$f"; done | sort -nr | head -n 1 | cut -f2)"
total="$(find "$disc" -type f | while IFS= read -r f; do size "$f"; done | awk '{ s += $1 } END { printf "%.2f", s / 1e9 }')"
echo "DISC RECORD"
echo "==========="
echo "Title ........: ${TITLE:-?}"
echo "Studio .......: ${STUDIO:-?}"
echo "Year .........: ${YEAR:-?}"
echo "Source .......: Blu-ray, full disc (BDMV structure, not re-encoded)"
echo "Folder name ..: $(basename "$disc")"
echo "Total size ...: $total GB in $(find "$disc" -type f | wc -l | tr -d ' ') files"
echo "Main title ...: $(basename "$main")"
echo
echo "STREAMS (BDMV/STREAM)"
echo "---------------------"
find "$disc/BDMV/STREAM" -type f -iname '*.m2ts' | sort | while IFS= read -r f; do
  d="$(mediainfo --Inform='General;%Duration/String3%' "$f" 2>/dev/null || true)"
  printf '%-14s %8.2f GB  %s\n' "$(basename "$f")" "$(size "$f" | awk '{ printf "%.2f", $1 / 1e9 }')" "${d:-unreadable}"
done
echo
echo "MEDIAINFO, main title"
echo "---------------------"
mediainfo "$main" 2>/dev/null | sed "s#$disc/##"
echo
echo "Integrity: SHA-256 manifest in the .sha256 file next to this record (if present)."
