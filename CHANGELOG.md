# Changelog

## 0.1.0

First version, rewritten from a single script:

- Library scan, entries detected by media folder, indexed by inode.
- qBittorrent: torrents matched through their API file list (inode, then path).
- Prowlarr (optional): torrent indexers, state and grabs; tracker health warnings.
- Dashboard (offline HTML), CSV inventory, CSV history, JSON snapshot.
- TOML config + `SEEDBOX_*` environment variables, `_FILE` secrets.
- `seedbox check | collect | run`, container image, CI.
