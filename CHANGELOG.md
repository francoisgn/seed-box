# Changelog

## 0.9.0

Seedbox control plane: from a coverage report to a dashboard that diagnoses
and fixes, through qBittorrent.

- Dashboard redone in Material 3 dark: navigation rail, top bar with library
  search, KPI tiles, charts (status and torrent-state donuts, coverage per
  tracker, seeded entries over time, torrents added per day, coverage per
  collection, CPU/IO wait, busiest disk, memory, transfer, volume gauges),
  tooltips on every mark, logo (Clawd, eye patch on, at an iMac) and pirate
  flag favicon.
- Entries: one per film when films sit side by side (`incoming`, `archives`,
  `disney` were each one giant entry), CD1/CD2 parts grouped, season packs made
  of one folder per episode grouped, samples ignored.
- Matching blind spot fixed: files still named `.!qB` (incomplete or being
  rechecked) were not found, so cross-seed torrents landed in "outside the
  library". Each outside torrent now has a reason (only cross-seed links left,
  other share, files missing, downloading).
- Diagnosis per entry and torrent, with one-click fixes: same file uploaded
  several times on one tracker (remove the extras), failed cross-seed matches
  (0 % after recheck), stopped torrents, matches waiting for extras nobody
  seeds (skip them), errors, several versions of a work, episodes twice, lone
  film in a grouping folder.
- Library filters: seeded everywhere, partially seeded, on disk not seeded,
  downloading, problems, duplicates; folder and missing-on-tracker filters;
  multi-select to move, recheck or start in batch.
- Actions through qBittorrent (`[service] actions = true`): move, recheck,
  start, skip missing extras, remove (library files are never deleted), collect
  now. Jobs are tracked in `jobs.json`, their status read back from
  qBittorrent; moves started elsewhere are read from its log.
- System sampling in `seedbox run` (`metrics_interval`, 5 min): `/proc` and one
  light qBittorrent call, 14 days in `metrics.jsonl`.
- API: `GET /api/status`, `/api/metrics`, `/api/collect`; `POST /api/action`,
  `/api/collect`, guarded by an `X-Seedbox` header, JSON body and same origin.
- Log events are matched on the message, not on torrent names ("Movie").
- History: entries are finer from this version, so the entry count jumps once.

## 0.5.1

- Deploy: `up -d --force-recreate`, so a changed `seedbox.toml` (single-file bind
  mount, replaced by the upload) is actually seen by the container.
- Deploy: when Compose gives up on a slow host (60 s timeout) while the daemon
  carries on, converge with `up -d` / `docker start` instead of failing, then
  remove the old containers left renamed `<id>_seedbox`.
- `seedbox run` exits at once on SIGTERM (it ignored it as PID 1: every
  `docker stop` waited 10 s, then killed it with exit 137).
- `seedbox run` no longer collects at every start: only without a previous
  collection, after a config change, or when a scheduled run (or the interval)
  was missed. Restarts and redeploys stop adding duplicate history lines.
- Snapshot: `config` fingerprint of the settings that shape a collection.

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
