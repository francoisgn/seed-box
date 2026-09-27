# Changelog

## Unreleased

- Deploy: `up -d --force-recreate`, so a changed `seedbox.toml` (single-file bind
  mount, replaced by the upload) is actually seen by the container.

## 0.5.0

- `seedbox status`: what qBittorrent is busy with right now (rechecks and bytes
  left to read, moves, errors, disk queue, recent moves/removals/errors from the
  log, queueing and disk limits). Same data at `GET /api/status` under
  `seedbox run`, shown by the dashboard's "qBittorrent activity" panel (Refresh
  button). `deploy/deploy.sh --status` runs it in the deployed container.
- qBittorrent 5.x: session cookie `QBT_SID_<port>` accepted (was only `SID`,
  so every API call after login answered 403).

## 0.4.1

- qBittorrent: accept the `204` login answer of recent versions (was reported
  as "login refused"); failed logins now answer `401`.
- Deploy script: final check through `docker exec` (`DEPLOY_DOCKER`), since
  Compose v1 `exec` needs `docker` in the PATH.

## 0.4.0

- `deploy/deploy.sh`: portable (POSIX sh) deploy over SSH from a local deploy
  config with `include` support (dotfiles), secrets from commands (keychain,
  pass…), one multiplexed SSH connection, uid check, `seedbox check` at the end.
  Options `--print-config`, `--render DIR`, `--check`.
- Secrets reach the container as files (`secrets/`, `SEEDBOX_*_FILE`); an empty
  file means "not set".
- Compose template mounts `secrets/`; `seedbox.example.toml` no longer holds secrets.
- ShellCheck in pre-commit.
- Compose v1 (`docker-compose`) supported: `-f compose.yaml` always passed.

## 0.3.0

- CI: GitHub Actions bumped to their Node 24 majors (docker/* v4, metadata-action v6,
  setup-python v7).

## 0.2.0

- `[service] schedule`: fixed collection times ("sun 04:00"), local time (TZ).
- Warning when the config file holds secrets and is readable by others.
- `seedbox check` shows whether each secret is set, and the schedule.
- docs/deployment.md: deployment flow and contract for an external CD.
- Templates: one config file for connections, secrets and schedule; `.env`
  keeps host values only (version, PUID/PGID, TZ, paths).

## 0.1.0

First version, rewritten from a single script:

- Library scan, entries detected by media folder, indexed by inode.
- qBittorrent: torrents matched through their API file list (inode, then path).
- Prowlarr (optional): torrent indexers, state and grabs; tracker health warnings.
- Dashboard (offline HTML), CSV inventory, CSV history, JSON snapshot.
- TOML config + `SEEDBOX_*` environment variables, `_FILE` secrets.
- `seedbox check | collect | run`, container image, CI.
