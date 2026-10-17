# shellcheck shell=bash
# Terminal helpers shared by the toolbox scripts: coloured statuses and a
# progress line. Colours are off when stdout is not a terminal or NO_COLOR is set.

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  C_GREEN=$'\033[32m' C_YELLOW=$'\033[33m' C_RED=$'\033[31m'
  C_BLUE=$'\033[34m' C_RESET=$'\033[0m'
else
  C_GREEN='' C_YELLOW='' C_RED='' C_BLUE='' C_RESET=''
fi

say()  { printf '%s%-5s%s %s\n' "$1" "$2" "$C_RESET" "$3"; }
ok()   { say "$C_GREEN" ok "$*"; }
warn() { say "$C_YELLOW" warn "$*" >&2; }
ko()   { say "$C_RED" ko "$*" >&2; }
info() { say "$C_BLUE" info "$*"; }
die()  { ko "$*"; exit 1; }

# need <command>...: stop with an install hint when a tool is missing.
need() {
  for _t in "$@"; do
    command -v "$_t" >/dev/null 2>&1 || die "$_t not found (see toolbox/README.md, Install)"
  done
}
