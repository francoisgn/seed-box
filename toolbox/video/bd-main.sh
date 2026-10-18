#!/usr/bin/env bash
# Find the main title stream of a Blu-ray folder: the largest .m2ts under BDMV/STREAM
# (extras and menus are much smaller). Prints "path<TAB>bytes" for the top N streams
# so you can check there is no close second (a feature split over several streams).
# Usage: bd-main.sh [--top N] DISC_FOLDER
set -eu
die() { echo "ko $*" >&2; exit 1; }

TOP=3
while [ $# -gt 0 ]; do
  case "$1" in
    --top) TOP="$2"; shift 2 ;;
    -h | --help) sed -n '2,/^set -eu/p' "$0" | sed '$d;s/^# \{0,1\}//'; exit 0 ;;
    -*) die "unknown option: $1" ;;
    *) break ;;
  esac
done
[ $# -eq 1 ] || die "usage: bd-main.sh [--top N] DISC_FOLDER"
[ -d "$1/BDMV/STREAM" ] || die "no BDMV/STREAM in $1"
find "$1/BDMV/STREAM" -type f -iname '*.m2ts' | while IFS= read -r f; do
  s="$(stat -c %s "$f" 2>/dev/null || stat -f %z "$f")"
  printf '%s\t%s\n' "$f" "$s"
done | sort -t"$(printf '\t')" -k2,2nr | head -n "$TOP"
