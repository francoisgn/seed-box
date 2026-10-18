#!/usr/bin/env bash
# Name for a re-encoded copy of a release: drop the origin team and container tag,
# turn YY.MM.DD into YYYYMMDD, put the real resolution before the source tag and
# finish with video codec, audio codec and our own tag.
#   <base>.<res>p.[<source>.]<vcodec>.<acodec>-<tag>.<ext>
# A DOLBY tag is kept only when the new audio is Dolby (AC3, EAC3, TRUEHD).
# Usage: retag-name.sh OLD_NAME VCODEC ACODEC HEIGHT [TAG]   (or RELEASE_TAG=tag retag-name.sh ...)
#   OLD_NAME  file name with extension; VCODEC x265|x264|...; ACODEC AAC|AC3|EAC3|...
#   HEIGHT    pixel height of the new file (1080 -> 1080p); TAG is your own release tag (or set RELEASE_TAG)
# Output extension is .mp4 unless OLD_NAME ends in .mkv and EXT is set in the environment.
set -eu
die() { echo "ko $*" >&2; exit 1; }
case "${1:-}" in -h | --help) sed -n '2,/^set -eu/p' "$0" | sed '$d;s/^# \{0,1\}//'; exit 0 ;; esac
[ $# -ge 4 ] || die "usage: retag-name.sh OLD_NAME VCODEC ACODEC HEIGHT [TAG]"
old="$1" v="$2" a="$3" h="$4" tag="${5:-${RELEASE_TAG:-}}"
[ -n "$tag" ] || die "no release tag: pass it as 5th argument or set RELEASE_TAG"
ext="${EXT:-mp4}"
stem="${old%.*}"

# 1. origin team: "-GROUP" at the end, only when the part after the last hyphen has no lowercase letter
last="${stem##*-}"
if [ "$last" != "$stem" ] && ! printf '%s' "$last" | grep -q '[a-z]'; then stem="${stem%-*}"; fi
# 2. container tags, old resolution and codec tokens
stem="$(printf '%s' "$stem" | sed -E 's/\.(MP4|WMV|AVI|MKV|MOV)(\.|$)/\2/g; s/\.[0-9]{3,4}p(\.|$)/\1/g; s/\.(x264|x265|H\.?264|H\.?265|HEVC|AVC|XviD|DivX)(\.|$)/\2/gI')"
# 3. DOLBY tag only for Dolby audio
case "$a" in AC3 | EAC3 | TRUEHD) ;; *) stem="$(printf '%s' "$stem" | sed -E 's/\.DOLBY(\.[A-Z])*(\.|$)/\2/')" ;; esac
# 4. YY.MM.DD -> YYYYMMDD
stem="$(printf '%s' "$stem" | sed -E 's/\.([0-9]{2})\.([0-9]{2})\.([0-9]{2})(\.|$)/.20\1\2\3\4/')"
# 5. resolution before the source tag, else at the end
if printf '%s' "$stem" | grep -qE '\.(BluRay|BDRip|WEB-DL|WEBRip|DVDRip|HDTV|BRRip)(\.|$)'; then
  stem="$(printf '%s' "$stem" | sed -E "s/\.(BluRay|BDRip|WEB-DL|WEBRip|DVDRip|HDTV|BRRip)(\.|$)/.${h}p.\1\2/")"
else
  stem="$stem.${h}p"
fi
stem="$(printf '%s' "$stem" | sed -E 's/\.{2,}/./g; s/\.$//')"
printf '%s.%s.%s-%s.%s\n' "$stem" "$v" "$a" "$tag" "$ext"
