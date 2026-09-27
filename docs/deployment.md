# Deployment

This repository ships the **image** and the **templates**. Deploying it on a
given host (where the secrets come from, which host, how files get there) is
your own CD and lives outside this repository: in your dotfiles, a private
repo, Ansible, whatever you already use. This page is the contract that CD
has to fulfil.

## What lives where

| Item | Where | Versioned here |
|---|---|---|
| Code, image `ghcr.io/francoisgn/seed-box:<version>` | this repo, built on tag `v*` | ✅ |
| Templates: `seedbox.example.toml`, `compose.example.yaml`, `.env.example` | this repo | ✅ |
| Secrets: qBittorrent password, Prowlarr API key | your secret store (keychain, password manager, vault…) | ❌ |
| Rendered `seedbox.toml`, `.env`, `compose.yaml` | target host, deploy directory | ❌ |
| Deploy script, host inventory, SSH access | your CD (dotfiles, private repo) | ❌ |
| Collected data (`data/`: dashboard, CSV, history) | target host | ❌ |

## Flow

```
 secret store ─┐
               ├─> render (umask 077) ──> seedbox.toml  ─┐
 templates ────┘                          .env           ├─ ssh/scp ─> host:<deploy dir>/
 (this repo, pinned version)              compose.yaml  ─┘                 │
                                                                           v
                                         docker compose pull && up -d
                                                                           │
                                                                           v
                                     docker compose exec seedbox python -m seedbox check
```

1. **Render** `seedbox.toml` from `seedbox.example.toml` of the deployed
   version, with the secrets pulled from your store at that moment. Render
   `.env` from `.env.example` (no secret in it). Take `compose.example.yaml`
   as is, or adapt it.
2. **Copy** the three files to the deploy directory on the host.
   `seedbox.toml`: mode `600`, owned by `PUID:PGID` (the container user must
   read it; nobody else should). Temporary copies on the deploying machine are
   deleted, or created in a `umask 077` directory.
3. **Start**: `docker compose pull && docker compose up -d`.
4. **Verify**: `docker compose exec seedbox python -m seedbox check` must end
   without `ko`. It reports each source separately and whether each secret is
   set, never its value.
5. **Watch**: the container healthcheck covers the dashboard; `docker compose
   logs seedbox` shows each collection (`ok`, `warn`, `ko`).

Rotating a secret is steps 1, 2, then `docker compose restart seedbox`.
Upgrading is the same with a new `SEEDBOX_VERSION`, templates taken from the
matching tag. Rollback: previous `SEEDBOX_VERSION`, redeploy.

## Host layout

```
<deploy dir>/
├── compose.yaml     from compose.example.yaml
├── .env             host values: version, PUID/PGID, TZ, MEDIA_ROOT, port
├── seedbox.toml     connections, secrets, schedule          (600, PUID:PGID)
└── data/            dashboard and history, keep it in backups (PUID:PGID)
```

## Configuration contract

| Section | Keys | Secret |
|---|---|---|
| `[library]` | `roots` (paths inside the container), `max_depth`, `skip_dirs`, `media_ext` | |
| `[qbittorrent]` | `url`, `username`, **`password`** | 🔴 |
| `[qbittorrent.path_map]` | qBittorrent path → container path | |
| `[prowlarr]` | `url`, **`api_key`** | 🔴 |
| `[trackers.aliases]` | host → tracker name | |
| `[output]` | `dir`, `csv_delimiter` | |
| `[service]` | `schedule` (`"sun 04:00"`), `interval_hours`, `port` | |

Every key has an environment override (see README). If you prefer to keep
secrets out of the file entirely, leave them empty and pass
`SEEDBOX_QBT_PASSWORD_FILE` / `SEEDBOX_PROWLARR_API_KEY_FILE` pointing at
Docker secrets. Env and `_FILE` values win over the file.

`seedbox` warns at start when `seedbox.toml` holds a secret and is readable
by group or others.

## Rendering example

A minimal POSIX sh renderer. `secret` is the only part tied to your secret
store: here the macOS keychain, swap it for `pass`, `op read`, `bw get`…

```sh
#!/bin/sh
set -eu

secret() { security find-generic-password -s "$1" -w; }   # macOS keychain

out=$(mktemp -d)                  # private dir: mktemp -d creates it 700
trap 'rm -rf "$out"' EXIT
umask 077

qbt_password=$(secret seedbox-qbt-password)
prowlarr_api_key=$(secret seedbox-prowlarr-api-key)

# Replace the CHANGE_ME placeholders, in file order: qBittorrent then Prowlarr.
awk -v q="$qbt_password" -v p="$prowlarr_api_key" '
  /^password = "CHANGE_ME"/ { sub(/CHANGE_ME/, q) }
  /^api_key = "CHANGE_ME"/  { sub(/CHANGE_ME/, p) }
  { print }
' seedbox.example.toml > "$out/seedbox.toml"
# Then adapt roots, path_map, schedule... (sed, or keep your own template).

scp -p "$out/seedbox.toml" nas:/srv/seedbox/seedbox.toml
ssh nas 'cd /srv/seedbox && docker compose pull -q && docker compose up -d \
  && docker compose exec -T seedbox python -m seedbox check'
```

Notes on portability:

- `awk` `sub()` treats `&` and `\` in the replacement specially: if a secret
  may contain them, escape them first or render with Python/`jq`-like tooling.
- `sed -i` differs between BSD (macOS: `sed -i ''`) and GNU (`sed -i`);
  writing to a new file as above avoids it.
- The file owner on the host must match `PUID`: `scp` keeps the remote user,
  so deploy with that user or `chown` afterwards (`sudo` on the NAS).

## Checklist

- [ ] Host sees library and torrent data on one filesystem, mounted at `MEDIA_ROOT`
- [ ] `path_map` translates every `save_path` qBittorrent reports
- [ ] `PUID:PGID` can read the media and write `data/`
- [ ] `seedbox.toml` is `600`, owned by `PUID:PGID`, absent from any repo
- [ ] `seedbox check` has no `ko`
- [ ] Dashboard reachable only from the LAN or behind an authenticating proxy
- [ ] `data/` included in backups (history is not recomputable)
