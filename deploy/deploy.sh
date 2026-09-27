#!/bin/sh
# Deploy seedbox to a Docker host over SSH.
#
# Everything host- or person-specific (address, SSH key, remote paths, secrets
# or the commands that fetch them) comes from a local deploy config that is
# never committed. Lookup order:
#   -c FILE, $SEEDBOX_DEPLOY_CONF, <repo>/deploy/deploy.conf,
#   ${XDG_CONFIG_HOME:-~/.config}/seedbox/deploy.conf
# The config may `include` other files (e.g. from your dotfiles). Template:
# deploy/deploy.conf.example. Details: docs/deployment.md.
#
# POSIX sh; needs ssh and tar locally, sh, tar and Docker Compose on the host.

set -eu

usage() {
  cat <<'EOF'
Usage: deploy/deploy.sh [-c FILE] [--print-config | --render DIR | --check]

  (no option)      render, upload, pull the image, restart, run `seedbox check`
  -c, --config F   deploy config file
  --print-config   show the resolved config (secrets masked) and exit
  --render DIR     render the deployment files into DIR, no connection
  --check          only run `seedbox check` in the running container
  -h, --help       this help
EOF
}

# --- Output: ok (green), warn (yellow), ko (red), info (blue); no color without TTY or with NO_COLOR.
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  C_OK=$(printf '\033[32m') C_WARN=$(printf '\033[33m') C_KO=$(printf '\033[31m')
  C_INFO=$(printf '\033[34m') C_OFF=$(printf '\033[0m')
else
  C_OK='' C_WARN='' C_KO='' C_INFO='' C_OFF=''
fi
say()  { printf '%s%-7s%s %s\n' "$1" "$2" "$C_OFF" "$3"; }
ok()   { say "$C_OK" ok "$*"; }
warn() { say "$C_WARN" warn "$*"; }
ko()   { say "$C_KO" ko "$*" >&2; }
info() { say "$C_INFO" info "$*"; }
die()  { ko "$*"; exit 1; }

# spin <message> <command...>: run in background with a spinner, then ok or ko + output.
spin() {
  msg=$1
  shift
  log="$WORK/spin.log"
  "$@" >"$log" 2>&1 &
  pid=$!
  if [ -t 2 ]; then
    while kill -0 "$pid" 2>/dev/null; do
      for f in ⠋ ⠙ ⠹ ⠸ ⠼ ⠴ ⠦ ⠧ ⠇ ⠏; do
        kill -0 "$pid" 2>/dev/null || break
        printf '\r%s%s%s       %s' "$C_INFO" "$f" "$C_OFF" "$msg" >&2
        sleep 0.1
      done
    done
    printf '\r\033[K' >&2
  fi
  rc=0
  wait "$pid" || rc=$?
  if [ "$rc" -eq 0 ]; then
    ok "$msg"
  else
    ko "$msg"
    sed 's/^/        /' "$log" >&2
  fi
  return "$rc"
}

REPO_DIR=$(cd "$(dirname "$0")/.." && pwd)

KEYS=" DEPLOY_HOST DEPLOY_USER DEPLOY_PORT DEPLOY_SSH_KEY DEPLOY_DIR DEPLOY_COMPOSE DEPLOY_DOCKER COMPOSE_TEMPLATE
  SEEDBOX_TOML SEEDBOX_VERSION PUID PGID TZ MEDIA_ROOT SEEDBOX_PORT
  QBT_PASSWORD QBT_PASSWORD_CMD PROWLARR_API_KEY PROWLARR_API_KEY_CMD "
PATH_KEYS=" DEPLOY_SSH_KEY COMPOSE_TEMPLATE SEEDBOX_TOML "

has_word() { case " $(printf '%s' "$1" | tr '\n' ' ') " in *" $2 "*) return 0 ;; esac; return 1; }

trim() {
  t=${1#"${1%%[![:space:]]*}"}
  printf '%s' "${t%"${t##*[![:space:]]}"}"
}

unquote() {
  case $1 in
    \"*\") t=${1#\"}; printf '%s' "${t%\"}" ;;
    \'*\') t=${1#\'}; printf '%s' "${t%\'}" ;;
    *) printf '%s' "$1" ;;
  esac
}

# expand_path <path> <base dir>: ~ expansion, relative paths from the config file's directory.
# shellcheck disable=SC2088  # matching a literal ~ on purpose
expand_path() {
  case $1 in
    "~") printf '%s' "$HOME" ;;
    "~/"*) printf '%s/%s' "$HOME" "${1#"~/"}" ;;
    /*) printf '%s' "$1" ;;
    *) printf '%s/%s' "$2" "$1" ;;
  esac
}

# parse_file <file> <depth>: print normalized KEY=value lines, following includes.
# Runs in a subshell so recursion does not clobber variables. Later values win.
parse_file() (
  file=$1
  depth=$2
  [ "$depth" -le 8 ] || { ko "include nesting too deep at $file"; exit 1; }
  [ -f "$file" ] || { ko "config file not found: $file"; exit 1; }
  dir=$(cd "$(dirname "$file")" && pwd)
  printf '#FILE=%s/%s\n' "$dir" "$(basename "$file")"
  n=0
  while IFS= read -r line || [ -n "$line" ]; do
    n=$((n + 1))
    line=$(trim "$line")
    case $line in
      '' | '#'*) ;;
      'include '* | 'include	'*)
        inc=$(unquote "$(trim "${line#include}")")
        parse_file "$(expand_path "$inc" "$dir")" $((depth + 1)) || exit 1
        ;;
      *=*)
        key=$(trim "${line%%=*}")
        val=$(unquote "$(trim "${line#*=}")")
        has_word "$KEYS" "$key" || { ko "$file:$n: unknown key '$key'"; exit 1; }
        if [ -n "$val" ] && has_word "$PATH_KEYS" "$key"; then
          val=$(expand_path "$val" "$dir")
        fi
        printf '%s=%s\n' "$key" "$val"
        ;;
      *) ko "$file:$n: expected KEY=value or 'include <file>'"; exit 1 ;;
    esac
  done <"$file"
)

# shell-quote for the remote command line
q() { printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"; }

# --- Arguments
CONF='' MODE=deploy RENDER_DIR=''
while [ $# -gt 0 ]; do
  case $1 in
    -c | --config) [ $# -ge 2 ] || die "$1 needs a file"; CONF=$2; shift 2 ;;
    --print-config) MODE=print; shift ;;
    --render) [ $# -ge 2 ] || die "$1 needs a directory"; MODE=render; RENDER_DIR=$2; shift 2 ;;
    --check) MODE=check; shift ;;
    -h | --help) usage; exit 0 ;;
    *) usage >&2; die "unknown argument: $1" ;;
  esac
done

if [ -z "$CONF" ]; then
  for candidate in "${SEEDBOX_DEPLOY_CONF:-}" "$REPO_DIR/deploy/deploy.conf" \
    "${XDG_CONFIG_HOME:-$HOME/.config}/seedbox/deploy.conf"; do
    if [ -n "$candidate" ] && [ -f "$candidate" ]; then CONF=$candidate; break; fi
  done
  [ -n "$CONF" ] || die "no deploy config found; start from deploy/deploy.conf.example (see docs/deployment.md)"
fi

# Short path on purpose: the SSH control socket path must stay under 104 bytes
# (macOS $TMPDIR is too long). Template form works with BSD and GNU mktemp.
WORK=$(mktemp -d /tmp/seedbox-deploy.XXXXXX)
cleanup() {
  [ -n "${TARGET:-}" ] && ssh -o ControlPath="$WORK/cm-%C" -O exit "$TARGET" >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT
trap 'exit 130' INT TERM

# --- Load config
for k in $KEYS; do eval "C_$k=''"; done
FILES=''
parse_file "$CONF" 0 >"$WORK/conf" || exit 1
while IFS= read -r kv; do
  k=${kv%%=*}
  v=${kv#*=}
  if [ "$k" = "#FILE" ]; then FILES="$FILES $v"; continue; fi
  eval "C_$k=\$v"
done <"$WORK/conf"

: "${C_DEPLOY_COMPOSE:=docker compose}"
: "${C_DEPLOY_DOCKER:=docker}"
: "${C_COMPOSE_TEMPLATE:=$REPO_DIR/compose.example.yaml}"
: "${C_TZ:=UTC}"
: "${C_SEEDBOX_PORT:=8080}"
if [ -z "$C_SEEDBOX_VERSION" ]; then
  C_SEEDBOX_VERSION=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$REPO_DIR/seedbox/__init__.py")
fi
TARGET=${C_DEPLOY_USER:+$C_DEPLOY_USER@}$C_DEPLOY_HOST

masked() { if [ -n "$1" ]; then printf '<set>'; else printf '<empty>'; fi; }

if [ "$MODE" = print ]; then
  info "config:$FILES"
  for k in $KEYS; do
    eval "v=\$C_$k"
    case $k in QBT_PASSWORD | PROWLARR_API_KEY) v=$(masked "$v") ;; esac
    printf '%s=%s\n' "$k" "$v"
  done
  exit 0
fi

[ -n "$C_DEPLOY_HOST" ] || [ "$MODE" = render ] || die "DEPLOY_HOST is not set"
[ -n "$C_DEPLOY_DIR" ] || die "DEPLOY_DIR is not set"
case $C_DEPLOY_DIR in /*) ;; *) die "DEPLOY_DIR must be an absolute path: $C_DEPLOY_DIR" ;; esac

# --- Remote access (one multiplexed SSH connection for every step)
rssh() {
  cmd=$1
  set --
  [ -z "$C_DEPLOY_SSH_KEY" ] || set -- "$@" -i "$C_DEPLOY_SSH_KEY" -o IdentitiesOnly=yes
  [ -z "$C_DEPLOY_PORT" ] || set -- "$@" -p "$C_DEPLOY_PORT"
  ssh -o BatchMode=yes -o ConnectTimeout=15 -o ControlMaster=auto -o "ControlPath=$WORK/cm-%C" \
    -o ControlPersist=120 "$@" "$TARGET" "$cmd"
}

RDIR=$(q "$C_DEPLOY_DIR")
# -f: Compose v1 (docker-compose, e.g. Synology) does not look for compose.yaml by itself.
compose() { rssh "cd $RDIR && $C_DEPLOY_COMPOSE -f compose.yaml $1"; }
# `docker exec`, not `compose exec`: Compose v1 shells out to a `docker` it looks
# up in PATH, which sudo and non-interactive SSH may not provide.
check() { rssh "$C_DEPLOY_DOCKER exec seedbox python -m seedbox check"; }

if [ "$MODE" = check ]; then
  check
  exit $?
fi

# --- Checks and secrets
[ -f "$C_SEEDBOX_TOML" ] || die "SEEDBOX_TOML not found: ${C_SEEDBOX_TOML:-<not set>}"
[ -f "$C_COMPOSE_TEMPLATE" ] || die "COMPOSE_TEMPLATE not found: $C_COMPOSE_TEMPLATE"
[ -n "$C_MEDIA_ROOT" ] || die "MEDIA_ROOT is not set"

secret() { # secret <name> <value> <command>
  if [ -n "$3" ]; then
    sh -c "$3" || { ko "$1: command failed: $3"; return 1; }
  else
    printf '%s' "$2"
  fi
}
QBT_SECRET=$(secret QBT_PASSWORD_CMD "$C_QBT_PASSWORD" "$C_QBT_PASSWORD_CMD") || exit 1
PROWLARR_SECRET=$(secret PROWLARR_API_KEY_CMD "$C_PROWLARR_API_KEY" "$C_PROWLARR_API_KEY_CMD") || exit 1
[ -n "$QBT_SECRET" ] || warn "no qBittorrent password (fine if seedbox.toml has it or the client whitelists the host)"

if [ "$MODE" = deploy ]; then
  spin "SSH connection to $TARGET" rssh true || exit 1
  REMOTE_IDS=$(rssh 'id -u; id -g')
  REMOTE_UID=$(printf '%s\n' "$REMOTE_IDS" | sed -n 1p)
  : "${C_PUID:=$REMOTE_UID}"
  : "${C_PGID:=$(printf '%s\n' "$REMOTE_IDS" | sed -n 2p)}"
  if [ "$REMOTE_UID" != "$C_PUID" ]; then
    die "files would be owned by uid $REMOTE_UID but the container runs as PUID $C_PUID: deploy as that user"
  fi
fi
[ -n "$C_PUID" ] && [ -n "$C_PGID" ] || die "PUID and PGID must be set (auto-detected only when deploying)"

# --- Render
STAGE="$WORK/stage"
umask 077
mkdir -p "$STAGE/secrets"
cp "$C_COMPOSE_TEMPLATE" "$STAGE/compose.yaml"
cp "$C_SEEDBOX_TOML" "$STAGE/seedbox.toml"
printf '%s' "$QBT_SECRET" >"$STAGE/secrets/qbt_password"
printf '%s' "$PROWLARR_SECRET" >"$STAGE/secrets/prowlarr_api_key"
for k in SEEDBOX_VERSION PUID PGID TZ MEDIA_ROOT SEEDBOX_PORT; do
  eval "v=\$C_$k"
  case $v in *"'"* | *'
'*) die "$k contains a quote or newline" ;; esac
  printf "%s='%s'\n" "$k" "$v"
done >"$STAGE/.env"

if [ "$MODE" = render ]; then
  mkdir -p "$RENDER_DIR"
  cp -Rp "$STAGE/." "$RENDER_DIR/"
  ok "rendered into $RENDER_DIR (contains secrets: delete it after use)"
  exit 0
fi

# --- Deploy
upload() {
  COPYFILE_DISABLE=1 tar --format=ustar -C "$STAGE" -cf - . |
    rssh "umask 077 && mkdir -p $RDIR/data && tar -C $RDIR -xf - && chmod 700 $RDIR/secrets"
}
info "seedbox $C_SEEDBOX_VERSION -> $TARGET:$C_DEPLOY_DIR"
spin "Upload config, secrets and compose file" upload || exit 1
spin "Pull image ghcr.io/francoisgn/seed-box:$C_SEEDBOX_VERSION" compose "pull -q" || exit 1
spin "Start container" compose "up -d --remove-orphans" || exit 1
rc=0
check >"$WORK/check.log" 2>&1 || rc=$?
sed 's/^/        /' "$WORK/check.log"
if [ "$rc" -ne 0 ]; then
  die "seedbox check reported problems (see above); container left running"
fi
ok "seedbox check passed"
ok "dashboard: http://$C_DEPLOY_HOST:$C_SEEDBOX_PORT/"
