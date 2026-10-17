#!/usr/bin/env bash
# One line per video: mtime, duration, size, container, codec, resolution, fps,
# video bitrate, bits per pixel, HDR flag, scan type, audio and subtitle tracks.
# Meant for bulk scans (a whole library, a NAS): TSV on stdout, progress and
# problems on stderr. Needs ffprobe (ffmpeg) or, failing that, mediainfo.
#
# Usage: inventory.sh [--csv] [--no-header] [--cache FILE] [--exclude NAME]... PATH...
#   PATH       a file or a folder (scanned recursively: mp4 m4v mkv avi mov ts
#              m2ts wmv); files ending in .!qB (incomplete) never match
#   --exclude  skip every file below a folder (or a file) with this exact name;
#              repeatable, e.g. --exclude .tmp
#   --cache F  keep the TSV in F: a file whose path, size and mtime are
#              unchanged keeps its row (no probe), the others are probed again,
#              files that vanished are dropped. F is rewritten at the end and
#              the rows still go to stdout. Not combinable with --csv.
# Columns: file, mtime (epoch s), duration_s, size_bytes, container,
#          video_codec, resolution, fps, vbitrate_mbps, bpp (bits per pixel:
#          video bit/s over width*height*fps), hdr (hdr|empty),
#          scan (progressive|interlaced|empty),
#          audio (lang:codec:channels, ...), subtitles (lang:codec[:forced], ...)
# Codec and container names follow the backend (ffprobe: h264/hevc,
# matroska,webm; mediainfo: AVC/HEVC, Matroska).
# Env: PROBE=ffprobe|mediainfo forces the backend; on a NAS without either,
#      run it where one exists, e.g. through a container that ships mediainfo.
set -eu
export LC_ALL=C
here="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=/dev/null
. "$here/../lib/ui.sh"

SEP='	' HEADER=1 CACHE='' EXCLUDES='/'
while [ $# -gt 0 ]; do
  case "$1" in
    --csv) SEP=';' ; shift ;;
    --no-header) HEADER=0; shift ;;
    --cache) [ $# -ge 2 ] || die "--cache needs a file"; CACHE="$2"; shift 2 ;;
    --exclude) [ $# -ge 2 ] || die "--exclude needs a name"; EXCLUDES="$EXCLUDES$2/"; shift 2 ;;
    -h | --help) sed -n '2,/^set -eu/p' "$0" | sed '$d;s/^# \{0,1\}//'; exit 0 ;;
    -*) die "unknown option: $1" ;;
    *) break ;;
  esac
done
[ $# -ge 1 ] || die "usage: inventory.sh [--csv] [--no-header] [--cache FILE] [--exclude NAME]... PATH... (--help)"
[ -z "$CACHE" ] || [ "$SEP" = "	" ] || die "--cache needs the TSV format (no --csv)"

probe="${PROBE:-}"
if [ -z "$probe" ]; then
  if command -v ffprobe >/dev/null 2>&1; then probe=ffprobe
  elif command -v mediainfo >/dev/null 2>&1; then probe=mediainfo
  else die "neither ffprobe nor mediainfo found (see toolbox/README.md, Install)"; fi
fi
need "$probe"

# Each backend prints: duration|container|vcodec|WxH|fps|vbitrate_bps|hdr|scan|audio|subs
by_ffprobe() {
  ffprobe -v error -show_entries format=duration,format_name,size,bit_rate -show_streams "$1" 2>/dev/null | awk -F= '
    function add(s, v) { return s == "" ? v : s "," v }
    function frac(x,  p) { if (split(x, p, "/") == 2 && p[2] > 0) return p[1] / p[2]; return x + 0 }
    /^\[STREAM\]/ { sec = "s"; t = c = ch = w = h = l = fr = br = tb = ct = fo = fd = pf = ""; att = 0 }
    /^\[FORMAT\]/ { sec = "f" }
    sec == "f" && $1 == "duration" { dur = $2 }
    sec == "f" && $1 == "format_name" { cont = $2 }
    sec == "f" && $1 == "bit_rate" && $2 + 0 > 0 { fbr = $2 }
    sec == "f" && $1 == "size" { fsz = $2 }
    sec == "s" {
      if ($1 == "codec_type") t = $2
      else if ($1 == "codec_name") c = $2
      else if ($1 == "channels") ch = $2
      else if ($1 == "width") w = $2
      else if ($1 == "height") h = $2
      else if ($1 == "avg_frame_rate") fr = $2
      else if ($1 == "r_frame_rate") rfr = $2
      else if ($1 == "bit_rate") br = $2
      else if ($1 == "TAG:BPS") tb = $2
      else if ($1 == "TAG:language") l = $2
      else if ($1 == "color_transfer") ct = $2
      else if ($1 == "field_order") fo = $2
      else if ($1 == "DISPOSITION:forced") fd = $2
      else if ($1 == "DISPOSITION:attached_pic") att = $2
    }
    /^\[\/STREAM\]/ {
      sec = ""
      if (l == "") l = "und"
      if (t == "video" && att != 1 && vc == "") {
        vc = c; res = w "x" h
        f = frac(fr); if (f == 0) f = frac(rfr)
        fps = f > 0 ? sprintf("%.3f", f) : ""
        if (fps ~ /\./) { sub(/0+$/, "", fps); sub(/\.$/, "", fps) }
        vbr = (br != "" && br != "N/A") ? br : (tb != "" ? tb : "")
        if (ct == "smpte2084" || ct == "arib-std-b67") hdr = "hdr"
        if (fo == "progressive") scan = "progressive"
        else if (fo != "" && fo != "unknown") scan = "interlaced"
      } else if (t == "audio") {
        a = add(a, l ":" c ":" ch)
        abr += (br + 0 > 0) ? br : (tb + 0 > 0 ? tb : 128000)   # unknown track: assume 128 kb/s
      }
      else if (t == "subtitle") s = add(s, l ":" c (fd == 1 ? ":forced" : ""))
    }
    END { if (vc == "" && a == "") exit 1
          # no per-stream video bitrate (mkv): overall minus audio, approximate
          if (fbr == "" && fsz + 0 > 0 && dur + 0 > 0) fbr = fsz * 8 / dur
          if (vbr == "" && fbr + 0 > 0 && fbr - abr > 0) vbr = fbr - abr
          printf "%d|%s|%s|%s|%s|%s|%s|%s|%s|%s\n", dur, cont, vc, res, fps, vbr, hdr, scan, a, s }'
}
by_mediainfo() {
  m() { mediainfo --Inform="$1" "$2" 2>/dev/null; }
  g="$(m 'General;%Duration%|%Format%|%OverallBitRate%' "$1")"
  v="$(m 'Video;%Format%|%Width%x%Height%|%FrameRate%|%BitRate%|%StreamSize%|%HDR_Format%/%transfer_characteristics%|%ScanType%|%FrameRate_Nominal%|%FrameRate_Original%\n' "$1" | head -n 1)"
  a="$(m 'Audio;%Language%:%Format%:%Channel(s)%:%BitRate%,' "$1")"
  s="$(m 'Text;%Language%:%Format%:%Forced%,' "$1" | sed 's/^:/und:/; s/,:/,und:/g; s/,$//; s/:No//g; s/:Yes/:forced/g')"
  [ -n "$v$a" ] || return 1
  printf '%s\n%s\n%s\n%s\n' "$g" "$v" "$a" "$s" | awk -F'|' '
    NR == 1 { dur = $1; cont = $2; obr = $3 }
    NR == 2 && $0 != "" {
      vc = $1; res = $2; fps = $3; br = $4; ss = $5; hd = $6
      sc = tolower($7); if (sc ~ /^prog/) sc = "progressive"; else if (sc != "") sc = "interlaced"
      if (fps == "") fps = $8
      if (fps == "") fps = $9
      sub(/\.0*$/, "", fps)
    }
    NR == 3 {
      n = split($0, t, ","); abr = 0
      for (i = 1; i <= n; i++) {
        if (t[i] == "") continue
        k = split(t[i], q, ":")
        if (q[1] == "") q[1] = "und"
        a = a (a == "" ? "" : ",") q[1] ":" q[2] ":" q[3]
        abr += (q[4] + 0 > 0) ? q[4] : 128000   # unknown track: assume 128 kb/s
      }
    }
    NR == 4 { s = $0 }
    END {
      # no video bitrate (mkv): from the stream size, else overall minus audio (approximate)
      if (br == "" && ss != "" && dur > 0) br = sprintf("%d", ss * 8 / (dur / 1000))
      if (br == "" && obr + 0 > 0 && obr - abr > 0) br = obr - abr
      hdr = (hd ~ /HDR|Dolby Vision|PQ|HLG|2084|B67/) ? "hdr" : ""
      printf "%d|%s|%s|%s|%s|%s|%s|%s|%s|%s\n", dur / 1000, cont, vc, res, fps, br, hdr, sc, a, s
    }'
}

# file_mtime FILE: epoch seconds (GNU stat, then BSD stat)
file_mtime() { stat -c %Y "$1" 2>/dev/null || stat -f %m "$1"; }

HDR_LINE="file${SEP}mtime${SEP}duration_s${SEP}size_bytes${SEP}container${SEP}video${SEP}resolution${SEP}fps${SEP}vbitrate_mbps${SEP}bpp${SEP}hdr${SEP}scan${SEP}audio${SEP}subtitles"

# Candidate files, minus excluded names
list="$(mktemp)" out="$(mktemp)"
trap 'rm -f "$list" "$out"' EXIT
find "$@" -type f \( -iname '*.mp4' -o -iname '*.m4v' -o -iname '*.mkv' -o -iname '*.avi' \
  -o -iname '*.mov' -o -iname '*.ts' -o -iname '*.m2ts' -o -iname '*.wmv' \) -print | sort |
  awk -v ex="$EXCLUDES" '{ n = split($0, p, "/"); for (i = 1; i <= n; i++) if (p[i] != "" && index(ex, "/" p[i] "/")) next; print }' >"$list"

use_cache=0
if [ -n "$CACHE" ] && [ -f "$CACHE" ] && [ "$(head -n 1 "$CACHE")" = "$HDR_LINE" ]; then use_cache=1; fi
[ -z "$CACHE" ] || [ "$use_cache" = 1 ] || [ ! -s "$CACHE" ] || warn "cache header differs, rescanning everything"

hits=0 probed=0
while IFS= read -r f; do
  size="$(wc -c <"$f" | tr -d ' ')"
  mtime="$(file_mtime "$f")"
  if [ "$use_cache" = 1 ]; then
    line="$(awk -F'\t' -v p="$f" -v m="$mtime" -v s="$size" 'NR > 1 && $1 == p && $2 == m && $4 == s { print; exit }' "$CACHE")"
    if [ -n "$line" ]; then printf '%s\n' "$line" >>"$out"; hits=$((hits + 1)); continue; fi
  fi
  row="$(by_"$probe" "$f")" || row=""
  case "$row" in *'|'*) ;; *) warn "unreadable: $f"; continue ;; esac
  probed=$((probed + 1))
  # row = duration|container|vcodec|WxH|fps|vbitrate_bps|hdr|scan|audio|subs
  printf '%s\n' "$row" | awk -F'|' -v OFS="$SEP" -v f="$f" -v size="$size" -v mtime="$mtime" '{
    mbps = ""; bpp = ""
    if ($6 + 0 > 0) {
      mbps = sprintf("%.2f", $6 / 1000000)
      split($4, wh, "x")
      if (wh[1] * wh[2] * $5 > 0) bpp = sprintf("%.3f", $6 / (wh[1] * wh[2] * $5))
    }
    print f, mtime, $1, size, $2, $3, $4, $5, mbps, bpp, $7, $8, $9, $10 }' >>"$out"
done <"$list"

[ "$HEADER" = 1 ] && printf '%s\n' "$HDR_LINE"
cat "$out"
if [ -n "$CACHE" ]; then
  { printf '%s\n' "$HDR_LINE"; cat "$out"; } >"$CACHE.new" && mv "$CACHE.new" "$CACHE"
  info "cache: $hits unchanged, $probed probed" >&2
fi
