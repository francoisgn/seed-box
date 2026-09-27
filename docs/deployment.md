# Deployment

The repository holds everything that is **the same for everybody**: the
image, the templates and a portable deploy script. Everything that is
**yours** (host address, SSH access, remote paths, secrets) lives in a local
deploy config on your machine, never on GitHub.

Once set up, deploying or upgrading is:

```sh
git pull
deploy/deploy.sh
```

## What lives where

| Item | Where | On GitHub |
|---|---|---|
| Code, image `ghcr.io/francoisgn/seed-box:<version>` | this repo, image built on tag `v*` | ✅ |
| `deploy/deploy.sh`, templates (`deploy/deploy.conf.example`, `seedbox.example.toml`, `compose.example.yaml`) | this repo | ✅ |
| Deploy config: host, SSH user/key, remote dir, compose command, secret commands | your machine, e.g. `~/.config/seedbox/deploy.conf` (can `include` files from your dotfiles) | ❌ |
| App config `seedbox.toml`: roots, path map, schedule | your machine, path given by `SEEDBOX_TOML` | ❌ |
| Secrets | your secret store (keychain, pass, 1Password…), read at deploy time | ❌ |
| Rendered files, collected data | the host, `DEPLOY_DIR` | ❌ |

## Requirements

On your machine: `sh`, `ssh`, `tar`, and this repo cloned.

On the host:

- SSH access for a user whose **uid is the container user** (`PUID`): it owns
  the deployed files, which are `600`. The script checks this and refuses a
  mismatch.
- That user may run Docker Compose non-interactively: member of the `docker`
  group, or a `sudoers` rule without password for the docker binary, e.g.
  `seedbox ALL=(root) NOPASSWD: /usr/local/bin/docker`.
- That user can create `DEPLOY_DIR` and read the media under `MEDIA_ROOT`.
- Library and torrent data on **one filesystem**, under `MEDIA_ROOT`
  (matching is done by inode).

## Setup, once per machine

```sh
mkdir -p ~/.config/seedbox
cp deploy/deploy.conf.example ~/.config/seedbox/deploy.conf
cp seedbox.example.toml ~/.config/seedbox/seedbox.toml
chmod 600 ~/.config/seedbox/deploy.conf
$EDITOR ~/.config/seedbox/deploy.conf ~/.config/seedbox/seedbox.toml
deploy/deploy.sh --print-config        # what the script will use, secrets masked
```

The script looks for its config in: `-c FILE`, `$SEEDBOX_DEPLOY_CONF`,
`deploy/deploy.conf` in the repo (git-ignored), then
`${XDG_CONFIG_HOME:-~/.config}/seedbox/deploy.conf`.

### Deploy config

`KEY=value` lines, `#` comments, and `include <file>` to read another file at
that point. Later lines win: put includes first, overrides after. `~` and
relative paths are resolved from the file that contains them. Unknown keys are
errors (typos do not go unnoticed). Values are never executed, except the
`*_CMD` keys.

| Key | Default | Meaning |
|---|---|---|
| `DEPLOY_HOST` | *(required)* | host name, IP or `~/.ssh/config` alias |
| `DEPLOY_USER`, `DEPLOY_PORT`, `DEPLOY_SSH_KEY` | from `~/.ssh/config` | SSH user, port, private key (`IdentitiesOnly` is set with a key) |
| `DEPLOY_DIR` | *(required)* | absolute remote directory |
| `DEPLOY_COMPOSE` | `docker compose` | compose command on the host, e.g. `sudo /usr/local/bin/docker compose` |
| `SEEDBOX_TOML` | *(required)* | local app config, your copy of `seedbox.example.toml` |
| `QBT_PASSWORD_CMD` / `QBT_PASSWORD` | empty | command printing the qBittorrent password, or the value |
| `PROWLARR_API_KEY_CMD` / `PROWLARR_API_KEY` | empty | same for the Prowlarr API key |
| `SEEDBOX_VERSION` | version of this checkout | image tag to deploy |
| `PUID`, `PGID` | uid/gid of the SSH user | container user |
| `TZ` | `UTC` | container time zone, used by `[service] schedule` |
| `MEDIA_ROOT` | *(required)* | host path mounted read-only as `/media` |
| `SEEDBOX_PORT` | `8080` | dashboard port on the host |
| `COMPOSE_TEMPLATE` | `compose.example.yaml` | compose file to deploy |

Example split between a generic config and a private file from dotfiles:

```sh
# ~/.config/seedbox/deploy.conf (symlinked by your dotfiles install)
include ~/Git/dotfiles/private/seedbox.conf     # host, user, key, paths
SEEDBOX_TOML=~/Git/dotfiles/private/seedbox.toml
QBT_PASSWORD_CMD=security find-generic-password -s seedbox-qbt -w
PROWLARR_API_KEY_CMD=security find-generic-password -s seedbox-prowlarr -w
```

Keep secrets as `*_CMD` pointing to a secret store rather than values: the
config files then contain nothing that must not leak, only where to find it.

## What the script does

```
 deploy.conf (+ includes) ──┐
 seedbox.toml ──────────────┤
 secret store (*_CMD) ──────┼─> render in a private temp dir (umask 077)
 compose.example.yaml ──────┘      compose.yaml  .env  seedbox.toml  secrets/{qbt_password,prowlarr_api_key}
                                            │
                              one SSH connection (multiplexed)
                                            v
 host: DEPLOY_DIR/  <── tar over ssh, files 600, secrets/ 700, data/ kept
        compose pull -q  ->  compose up -d  ->  compose exec seedbox python -m seedbox check
```

1. Load and validate the config; run the `*_CMD` secret commands.
2. Connect, read the remote uid/gid; refuse if it differs from `PUID`.
3. Render the files locally in a private temp directory, deleted on exit.
4. Upload them in one `tar` stream (`ustar`, no macOS metadata); `data/` is
   never touched.
5. `pull`, `up -d`, then `seedbox check` inside the container: the deploy
   fails if the check reports a `ko`.

The container reads the secrets from `/run/secrets/*` through
`SEEDBOX_*_FILE`; an empty file means "not set", so a value in `seedbox.toml`
still works for setups without the script.

## Options

| Command | Effect |
|---|---|
| `deploy/deploy.sh` | full deploy |
| `deploy/deploy.sh --print-config` | resolved config, secrets masked, no connection |
| `deploy/deploy.sh --render DIR` | render the files into `DIR`, no connection (contains secrets: delete it after) |
| `deploy/deploy.sh --check` | only run `seedbox check` in the running container |
| `-c FILE` | use this deploy config |

## Upgrade, rotation, rollback

- **Upgrade**: `git pull && deploy/deploy.sh`. The image tag follows the
  checkout, and templates come from the same checkout.
- **Rotate a secret**: update it in your store, run `deploy/deploy.sh`.
- **Rollback**: `git checkout v0.3.0 && deploy/deploy.sh`, or pin
  `SEEDBOX_VERSION` in the deploy config.

## Portability notes

- The script is POSIX `sh` (checked with ShellCheck, tested with dash in CI);
  secret commands run through `sh -c`.
- The SSH control socket lives under `/tmp`: macOS limits socket paths to 104
  bytes and its `$TMPDIR` is too long.
- Non-interactive SSH sessions often lack `/usr/local/bin` in `PATH`
  (Synology): give the full path in `DEPLOY_COMPOSE`.
- `.env` values are written single-quoted; quotes and newlines are refused.

## Checklist

- [ ] SSH user uid = `PUID`, Docker Compose runs without a password prompt
- [ ] `MEDIA_ROOT` holds library and torrent data on one filesystem
- [ ] `[qbittorrent.path_map]` translates every `save_path` qBittorrent reports
- [ ] Deploy config and `seedbox.toml` are outside any public repo, `600`
- [ ] `deploy/deploy.sh --print-config` shows the expected values
- [ ] `deploy/deploy.sh` ends with `seedbox check passed`
- [ ] Dashboard reachable only from the LAN or behind an authenticating proxy
- [ ] Host `DEPLOY_DIR/data` included in backups (history is not recomputable)
