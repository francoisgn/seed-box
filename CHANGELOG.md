# Changelog

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
