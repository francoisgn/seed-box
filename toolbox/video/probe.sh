#!/usr/bin/env bash
# Summarise the streams of one or more video files: what is in there and
# what a re-encode could save. Usage: probe.sh FILE...
set -eu
export LC_ALL=C
here="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=/dev/null
. "$here/../lib/ui.sh"
need ffprobe

[ $# -ge 1 ] || die "usage: probe.sh FILE..."

for f in "$@"; do
  [ -f "$f" ] || { ko "not a file: $f"; continue; }
  size="$(wc -c <"$f" | tr -d ' ')"
  dur="$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$f")"
  printf '\n%s%s%s\n' "$C_BLUE" "$(basename "$f")" "$C_RESET"
  awk -v s="$size" -v d="${dur:-0}" 'BEGIN {
    printf "  %.1f GB, %d min, %.1f Mb/s average\n", s/1e9, d/60, (d > 0 ? s*8/d/1e6 : 0) }'
  ffprobe -v error -show_streams "$f" | awk -F= '
    function flush() {
      if (idx == "") return
      if (type == "video")
        printf "  #%s video     %s %s %sx%s %s%s\n", idx, codec, prof, w, h, pix, (trc ~ /smpte2084|arib/ ? " HDR" : "")
      else if (type == "audio")
        printf "  #%s audio     %-4s %-8s %sch %s %s\n", idx, lang, codec, ch, prof, title
      else if (type == "subtitle")
        printf "  #%s subtitle  %-4s %s %s%s\n", idx, lang, codec, title, (forced == 1 ? " forced" : "")
    }
    /^\[STREAM\]/ { idx = type = codec = prof = w = h = pix = trc = ch = lang = title = ""; forced = 0 }
    $1 == "index" { idx = $2 }
    $1 == "codec_type" { type = $2 }
    $1 == "codec_name" { codec = $2 }
    $1 == "profile" && $2 != "unknown" { prof = $2 }
    $1 == "width" { w = $2 }
    $1 == "height" { h = $2 }
    $1 == "pix_fmt" { pix = $2 }
    $1 == "color_transfer" { trc = $2 }
    $1 == "channels" { ch = $2 }
    $1 == "DISPOSITION:forced" { forced = $2 }
    $1 == "TAG:language" { lang = $2 }
    $1 == "TAG:title" { title = $2 }
    /^\[\/STREAM\]/ { flush() }'
done
