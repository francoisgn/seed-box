#!/usr/bin/env bash
# Re-encode or slim down a video: smaller codec (x265), lower resolution,
# fewer audio/subtitle tracks. Always writes a new file, never touches the source.
#
# Usage: reencode.sh [options] INPUT OUTPUT.mkv|OUTPUT.mp4
#   --height N        cap the height (2160 -> 1080); never upscales
#   --codec C         x265 | videotoolbox | copy (copy = remux only: drop tracks, no re-encode)
#   --crf N           x265 quality (default 20)
#   --preset P        x265 preset (default medium)
#   --audio-langs L   comma list of languages to keep, e.g. fre,eng ("all" keeps everything)
#   --sub-langs L     same for subtitles
#   --audio MODE      copy | eac3 | compat (see video.conf.example)
#                     compat: keep AAC/AC-3/E-AC-3/MP3/Opus, else AAC 256k (stereo) or AC-3 640k (5.1)
#   --dedupe-audio    same language and channel count twice: keep one (AAC preferred)
#   --start S         test excerpt: start at S seconds ...
#   --duration N      ... and keep N seconds (skips the duration check)
#   --tonemap         HDR -> SDR (needs an ffmpeg built with zimg/zscale)
#   --dry-run         print the ffmpeg command and stop
# Defaults come from video.conf (see toolbox/video.conf.example).
set -eu
export LC_ALL=C
here="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=/dev/null
. "$here/../lib/ui.sh"
need ffmpeg ffprobe

ORIG=("$@")
HEIGHT=1080 CODEC=x265 CRF=20 PRESET=medium
AUDIO_LANGS="fre,eng" SUB_LANGS="fre,eng" AUDIO=copy EAC3_BITRATE=640k
conf="${TOOLBOX_CONF:-${XDG_CONFIG_HOME:-$HOME/.config}/toolbox/video.conf}"
# shellcheck source=/dev/null
[ -f "$conf" ] && . "$conf"
TONEMAP=0 DRY=0 DEDUPE=0 START='' LIMIT=''

while [ $# -gt 0 ]; do
  case "$1" in
    --height) HEIGHT="$2"; shift 2 ;;
    --codec) CODEC="$2"; shift 2 ;;
    --crf) CRF="$2"; shift 2 ;;
    --preset) PRESET="$2"; shift 2 ;;
    --audio-langs) AUDIO_LANGS="$2"; shift 2 ;;
    --sub-langs) SUB_LANGS="$2"; shift 2 ;;
    --audio) AUDIO="$2"; shift 2 ;;
    --tonemap) TONEMAP=1; shift ;;
    --dedupe-audio) DEDUPE=1; shift ;;
    --start) START="$2"; shift 2 ;;
    --duration) LIMIT="$2"; shift 2 ;;
    --dry-run) DRY=1; shift ;;
    -h | --help) sed -n '2,/^set -eu/p' "$0" | sed '$d;s/^# \{0,1\}//'; exit 0 ;;
    --) shift; break ;;
    -*) die "unknown option: $1" ;;
    *) break ;;
  esac
done
[ -z "${AUDIO_LANGS_FORCE:-}" ] || AUDIO_LANGS="$AUDIO_LANGS_FORCE"
[ -z "${TOOLBOX_FORCE_COPY:-}" ] || CODEC=copy
[ $# -eq 2 ] || die "usage: reencode.sh [options] INPUT OUTPUT.mkv (--help)"
in="$1" out="$2"
[ -f "$in" ] || die "not a file: $in"
case "$out" in
  *.mkv) fmt=matroska fmtargs="" ;;
  *.mp4) fmt=mp4 fmtargs="-movflags +faststart" ;;
  *) die "output must be .mkv or .mp4" ;;
esac
[ ! -e "$out" ] || die "output exists: $out"
[ "$in" -ef "$out" ] 2>/dev/null && die "output is the input"
case "$CODEC" in x265 | videotoolbox | copy) ;; *) die "unknown codec: $CODEC" ;; esac
case "$AUDIO" in copy | eac3 | compat) ;; *) die "unknown audio mode: $AUDIO" ;; esac

# lang_match <wanted comma list> <stream language>: ISO 639-1 and 639-2 spellings.
lang_match() {
  [ "$1" = none ] && return 1
  [ "$1" = all ] || [ -z "$1" ] && return 0
  case "$2" in
    fre | fra) l="fr|fre|fra" ;;
    eng) l="en|eng" ;;
    jpn) l="ja|jpn" ;;
    ger | deu) l="de|ger|deu" ;;
    spa) l="es|spa" ;;
    ita) l="it|ita" ;;
    *) l="$2" ;;
  esac
  case ",$1," in
    *,"${l%%|*}",* | *,"$(echo "$l" | cut -d'|' -f2)",* | *,"$(echo "$l" | cut -d'|' -f3)",*) return 0 ;;
  esac
  return 1
}

# Stream table: index|type|codec|language|channels|height|transfer
streams="$(ffprobe -v error -show_streams "$in" | awk -F= '
  /^\[STREAM\]/ { i = t = c = l = ch = h = trc = ""; att = 0 }
  $1 == "index" { i = $2 } $1 == "codec_type" { t = $2 } $1 == "codec_name" { c = $2 }
  $1 == "channels" { ch = $2 } $1 == "height" { h = $2 } $1 == "color_transfer" { trc = $2 }
  $1 == "TAG:language" { l = $2 } $1 == "DISPOSITION:attached_pic" { att = $2 }
  /^\[\/STREAM\]/ { if (!(t == "video" && att == 1)) print i "|" t "|" c "|" l "|" ch "|" h "|" trc }')"

maps="" nA=0 nS=0 keepA=0 keepS=0 hdr=0 src_h=0
audio_args=""
# audio tracks to drop with --dedupe-audio: per language+channels keep the AAC one, else the first
drop=" "
if [ "$DEDUPE" = 1 ]; then
  drop=" $(echo "$streams" | awk -F'|' '$2 == "audio" {
      k = $4 "/" $5
      if (!(k in win)) { win[k] = $1; wc[k] = $3 }
      else if ($3 == "aac" && wc[k] != "aac") { lose[++n] = win[k]; win[k] = $1; wc[k] = $3 }
      else lose[++n] = $1 }
    END { for (i = 1; i <= n; i++) printf "%s ", lose[i] }') "
fi
while IFS='|' read -r idx type codec lang ch h trc; do
  [ -n "$idx" ] || continue
  case "$type" in
    video)
      if [ -z "$maps" ] || [ "$src_h" = 0 ]; then
        maps="$maps -map 0:$idx"; src_h="${h:-0}"
        case "$trc" in smpte2084 | arib-std-b67) hdr=1 ;; esac
      fi ;;
    audio)
      nA=$((nA + 1))
      case "$drop" in *" $idx "*) continue ;; esac
      if lang_match "$AUDIO_LANGS" "${lang:-und}"; then
        maps="$maps -map 0:$idx"
        if [ "$AUDIO" = eac3 ]; then
          case "$codec" in
            truehd | mlp | dts | flac | pcm_*)
              # DTS core is lossy but the profile is unknown here: DTS-HD MA is the common case.
              br="$EAC3_BITRATE"; [ "${ch:-6}" -le 2 ] && br=224k
              audio_args="$audio_args -c:a:$keepA eac3 -b:a:$keepA $br" ;;
          esac
        fi
        if [ "$AUDIO" = compat ]; then
          case "$codec" in
            aac | ac3 | eac3 | mp3 | opus) ;;
            *) if [ "${ch:-2}" -le 2 ]; then audio_args="$audio_args -c:a:$keepA aac -b:a:$keepA 256k"
               else audio_args="$audio_args -c:a:$keepA ac3 -b:a:$keepA 640k"; fi ;;
          esac
        fi
        keepA=$((keepA + 1))
      fi ;;
    subtitle)
      nS=$((nS + 1))
      if lang_match "$SUB_LANGS" "${lang:-und}"; then maps="$maps -map 0:$idx"; keepS=$((keepS + 1)); fi ;;
  esac
done <<EOF2
$streams
EOF2

if [ "$nA" -gt 0 ] && [ "$keepA" -eq 0 ]; then
  [ -z "${TOOLBOX_ALL_AUDIO:-}" ] || die "no audio track kept"
  warn "no audio track matches '$AUDIO_LANGS': keeping all $nA"
  TOOLBOX_ALL_AUDIO=1 AUDIO_LANGS_FORCE=all exec "$0" "${ORIG[@]}"
fi

# Video arguments
vf="" vargs=""
if [ "$CODEC" = copy ]; then
  vargs="-c:v copy"
else
  if [ -n "$HEIGHT" ] && [ "$src_h" -gt "$HEIGHT" ]; then vf="scale=-2:$HEIGHT:flags=lanczos"; fi
  if [ "$hdr" = 1 ] && [ "$TONEMAP" = 1 ]; then
    ffmpeg -hide_banner -filters 2>/dev/null | grep -q zscale || die "--tonemap needs an ffmpeg with zscale (zimg); see toolbox/video/README.md"
    tm="zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p10le"
    vf="${vf:+$vf,}$tm"; hdr=0
  fi
  case "$CODEC" in
    x265)
      xp="log-level=error"
      [ "$hdr" = 1 ] && xp="$xp:colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc:hdr10-opt=1"
      vargs="-c:v libx265 -preset $PRESET -crf $CRF -pix_fmt yuv420p10le -x265-params $xp -tag:v hvc1" ;;
    videotoolbox)
      # Hardware encoder: no CRF, quality 0-100 (about 60 is close to CRF 20), larger files.
      vargs="-c:v hevc_videotoolbox -q:v 60 -pix_fmt p010le -tag:v hvc1" ;;
  esac
  [ "$hdr" = 1 ] && warn "HDR source kept as HDR10 (Dolby Vision layer and static metadata are not carried over)"
fi

dur="$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$in")"
info "$(basename "$in"): ${src_h}p, audio $keepA/$nA, subtitles $keepS/$nS, codec $CODEC${vf:+, filter on}"

tmp="$out.part"
# shellcheck disable=SC2086 # the argument strings are built above on purpose
set -- ffmpeg -nostdin -hide_banner -loglevel error -y ${START:+-ss "$START"} -i "$in" ${LIMIT:+-t "$LIMIT"} $maps \
  ${vf:+-vf "$vf"} $vargs -c:a copy $audio_args -c:s copy \
  -map_chapters 0 -map_metadata 0 -progress pipe:1 -nostats $fmtargs -f $fmt "$tmp"

if [ "$DRY" = 1 ]; then
  printf '%s ' "$@"; printf '\n'
  exit 0
fi

trap 'rm -f "$tmp" "$tmp.log"' INT TERM
start="$(date +%s)"
"$@" 2>"$tmp.log" | awk -F= -v d="$dur" -v tty="$([ -t 2 ] && echo 1 || echo 0)" '
  $1 == "out_time_us" && d > 0 && tty == 1 && $2 > 0 { printf "\r  %3d%%", $2 / 1e6 / d * 100 > "/dev/stderr" }
  END { if (tty == 1) printf "\r      \r" > "/dev/stderr" }' || true

# the pipeline hides ffmpeg status: verify the result instead
if [ ! -s "$tmp" ]; then
  ko "ffmpeg failed"; sed 's/^/        /' "$tmp.log" >&2; rm -f "$tmp" "$tmp.log"; exit 1
fi
odur="$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$tmp" 2>/dev/null || echo 0)"
[ -n "$LIMIT" ] && dur="$LIMIT"
if ! awk -v a="$dur" -v b="${odur:-0}" 'BEGIN { exit !(b > 0 && (a - b < 2 && b - a < 2)) }'; then
  ko "output duration ${odur}s differs from source ${dur}s (kept as $tmp)"
  sed 's/^/        /' "$tmp.log" >&2; exit 1
fi
# size guard: an encode above 90 % of its source gains nothing, remux the source instead
if [ "$CODEC" != copy ] && [ -z "$LIMIT" ]; then
  s_in="$(wc -c <"$in" | tr -d ' ')"; s_tmp="$(wc -c <"$tmp" | tr -d ' ')"
  if awk -v a="$s_in" -v b="$s_tmp" 'BEGIN { exit !(b > a * 0.9) }'; then
    warn "encode is over 90% of the source size: remuxing instead"
    rm -f "$tmp" "$tmp.log"
    TOOLBOX_FORCE_COPY=1 exec "$0" "${ORIG[@]}"
  fi
fi
mv "$tmp" "$out"; rm -f "$tmp.log"
sz_in="$(wc -c <"$in" | tr -d ' ')"; sz_out="$(wc -c <"$out" | tr -d ' ')"
ok "$(awk -v a="$sz_in" -v b="$sz_out" -v t="$(($(date +%s) - start))" \
  'BEGIN { printf "%.2f GB -> %.2f GB (%.0f%%) in %dm%02ds", a/1e9, b/1e9, b/a*100, t/60, t%60 }') $(basename "$out")"
