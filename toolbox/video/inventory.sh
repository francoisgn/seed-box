#!/usr/bin/env bash
# One line per video: duration, codec, resolution, audio and subtitle tracks.
# Meant for bulk scans (a whole library, a NAS): TSV on stdout, progress and
# problems on stderr. Needs ffprobe (ffmpeg) or, failing that, mediainfo.
#
# Usage: inventory.sh [--csv] [--no-header] PATH...
#   PATH   a file or a folder (scanned recursively: mp4 m4v mkv avi mov ts wmv)
# Columns: file, duration_s, size_bytes, video_codec, resolution,
#          audio (lang:codec:channels, ...), subtitles (lang:codec, ...)
# Codec names follow the backend (ffprobe: h264/hevc, mediainfo: AVC/HEVC).
# Env: PROBE=ffprobe|mediainfo forces the backend; on a NAS without either,
#      run it where one exists, e.g. through a container that ships mediainfo.
set -eu
export LC_ALL=C
here="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=/dev/null
. "$here/../lib/ui.sh"

SEP='	' HEADER=1
while [ $# -gt 0 ]; do
  case "$1" in
    --csv) SEP=';' ; shift ;;
    --no-header) HEADER=0; shift ;;
    -h | --help) sed -n '2,/^set -eu/p' "$0" | sed '$d;s/^# \{0,1\}//'; exit 0 ;;
    -*) die "unknown option: $1" ;;
    *) break ;;
  esac
done
[ $# -ge 1 ] || die "usage: inventory.sh [--csv] [--no-header] PATH... (--help)"

probe="${PROBE:-}"
if [ -z "$probe" ]; then
  if command -v ffprobe >/dev/null 2>&1; then probe=ffprobe
  elif command -v mediainfo >/dev/null 2>&1; then probe=mediainfo
  else die "neither ffprobe nor mediainfo found (see toolbox/README.md, Install)"; fi
fi
need "$probe"

# Each backend prints: duration|vcodec|resolution|audio|subs
by_ffprobe() {
  dur="$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$1" 2>/dev/null || true)"
  ffprobe -v error -show_streams "$1" 2>/dev/null | awk -F= -v dur="${dur:-}" '
    function add(s, v) { return s == "" ? v : s "," v }
    /^\[STREAM\]/ { t = c = ch = w = h = l = ""; att = 0 }
    $1 == "codec_type" { t = $2 } $1 == "codec_name" { c = $2 } $1 == "channels" { ch = $2 }
    $1 == "width" { w = $2 } $1 == "height" { h = $2 } $1 == "TAG:language" { l = $2 }
    $1 == "DISPOSITION:attached_pic" { att = $2 }
    /^\[\/STREAM\]/ {
      if (l == "") l = "und"
      if (t == "video" && att != 1 && vc == "") { vc = c; res = w "x" h }
      else if (t == "audio") a = add(a, l ":" c ":" ch)
      else if (t == "subtitle") s = add(s, l ":" c)
    }
    END { if (vc == "" && a == "") exit 1; printf "%d|%s|%s|%s|%s\n", dur, vc, res, a, s }'
}
by_mediainfo() {
  m() { mediainfo --Inform="$1" "$2" 2>/dev/null; }
  dur="$(m 'General;%Duration%' "$1")"
  v="$(m 'Video;%Format%|%Width%x%Height%\n' "$1" | head -n 1)"
  a="$(m 'Audio;%Language%:%Format%:%Channel(s)%,' "$1" | sed 's/^:/und:/; s/,:/,und:/g; s/,$//')"
  s="$(m 'Text;%Language%:%Format%,' "$1" | sed 's/^:/und:/; s/,:/,und:/g; s/,$//')"
  [ -n "$v$a" ] || return 1
  printf '%d|%s|%s|%s\n' "$(( ${dur:-0} / 1000 ))" "$v" "$a" "$s"
}

[ "$HEADER" = 1 ] && printf 'file%s\n' "${SEP}duration_s${SEP}size_bytes${SEP}video${SEP}resolution${SEP}audio${SEP}subtitles"

scan() {
  find "$@" -type f \( -iname '*.mp4' -o -iname '*.m4v' -o -iname '*.mkv' -o -iname '*.avi' \
    -o -iname '*.mov' -o -iname '*.ts' -o -iname '*.wmv' \) -print | sort
}
scan "$@" | while IFS= read -r f; do
  row="$(by_"$probe" "$f")" || row=""
  case "$row" in *'|'*) ;; *) warn "unreadable: $f"; continue ;; esac
  size="$(wc -c <"$f" | tr -d ' ')"
  # row = duration|vcodec|res|audio|subs
  printf '%s\n' "$row" | awk -F'|' -v OFS="$SEP" -v f="$f" -v size="$size" \
    '{ print f, $1, size, $2, $3, $4, $5 }'
done
