#!/usr/bin/env bash
# Desktop notification each time a queue.sh job finishes or fails, without any
# agent or session watching: run it in the background next to queue.sh.
# Only job ids and statuses are shown, never file names.
#
# Usage: notify-queue.sh [--remote HOST] --home DIR [--every SECONDS]
#   --home DIR   the same folder as queue.sh (state.tsv is read there)
# Notifications: macOS (osascript) or Linux (notify-send); otherwise it prints.
# Example: nohup ./notify-queue.sh --remote nas --home /path/to/home >/dev/null 2>&1 &
set -eu
here="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=/dev/null
. "$here/../lib/ui.sh"

REMOTE='' HOME_DIR='' EVERY=60
while [ $# -gt 0 ]; do
  case "$1" in
    --remote) REMOTE="$2"; shift 2 ;;
    --home) HOME_DIR="$2"; shift 2 ;;
    --every) EVERY="$2"; shift 2 ;;
    -h | --help) sed -n '2,/^set -eu/p' "$0" | sed '$d;s/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done
[ -n "$HOME_DIR" ] || die "usage: notify-queue.sh [--remote HOST] --home DIR (--help)"

read_state() {
  if [ -n "$REMOTE" ]; then ssh -n -o BatchMode=yes -o ConnectTimeout=10 "$REMOTE" "$(printf 'cat %q' "$HOME_DIR/state.tsv")" 2>/dev/null
  else cat "$HOME_DIR/state.tsv" 2>/dev/null; fi
}

notify() { # title message
  if command -v osascript >/dev/null 2>&1; then osascript -e "display notification \"$2\" with title \"$1\"" >/dev/null 2>&1 || true
  elif command -v notify-send >/dev/null 2>&1; then notify-send "$1" "$2" || true
  else printf '%s: %s\n' "$1" "$2"; fi
}

seen="$(read_state | wc -l | tr -d ' ')" # existing lines are history
while true; do
  sleep "$EVERY"
  state="$(read_state)" || continue
  total="$(printf '%s\n' "$state" | grep -c . || true)"
  if [ "$total" -gt "$seen" ]; then
    printf '%s\n' "$state" | tail -n +$((seen + 1)) | awk -F'\t' '$2 == "done" || $2 == "failed" { print $1 " " $2 }' |
      while read -r id st; do notify "Queue job $st" "$id"; done
    seen="$total"
  fi
done
