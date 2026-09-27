# seed-box

A small dashboard that answers one question: **is my library actually shared
on my trackers, and are all my trackers fed?**

It bridges three sources that do not talk to each other:

| Source | What it tells |
|---|---|
| Disk | what the library contains (entries, sizes) |
| qBittorrent | which torrents seed which files, on which trackers, how much they uploaded |
| Prowlarr *(optional)* | which trackers exist, whether they are enabled or failing, their grabs |

Output: a self-contained HTML dashboard, a CSV inventory, CSV history (one line
per run, overall and per tracker) and a JSON snapshot. Standard library only,
one container.

## How it works

Torrents are matched to library entries **by inode**, not by name or path.
With cross-seed or any hardlink setup, the file seeded by the client and the
file in the organised library are the same data on disk:

```
/media/movies/Some.Collection/Some.Entry/file.mkv   <- library
          | same inode (hardlink)
/media/downloads/.cross-seed/tracker-a/Other.Name/x.mkv   <- seeded by qBittorrent
```

An entry stays recognised after being renamed or moved. The torrent file list
comes from the qBittorrent API, so a torrent only claims its own files.

An **entry** is the first folder, walking down from a root, that directly holds
a media file. Grouping folders are walked through, everything below an entry
(subtitles, extras) belongs to it, and a season folder is one entry, matching
season packs. Loose media files in a root are one entry each.

| Status | Meaning |
|---|---|
| 🟢 seeded | at least one complete torrent |
| 🟠 incomplete | torrents exist but none is at 100 % |
| 🔴 orphan | no torrent at all |

For trackers, announce hosts (qBittorrent) and indexer URLs (Prowlarr) are
reduced to their domain and merged. The dashboard flags:

- 🟠 a tracker in Prowlarr that seeds nothing from the library,
- 🟠 a tracker seeding content but missing from Prowlarr (cross-seed will not search it),
- 🔴 a tracker Prowlarr disabled after errors,
- per entry, **"missing on tracker X"**: content you could still share there.

## Quick start (container)

```sh
cp compose.example.yaml compose.yaml
cp .env.example .env              # fill in paths and secrets
cp seedbox.example.toml seedbox.toml
docker compose run --rm seedbox check    # validate every source
docker compose up -d                     # collect every 24 h, serve on :8080
```

`seedbox check` tests each source separately (library roots, path mapping,
qBittorrent, Prowlarr, output dir) and is the first thing to run.

**Constraint:** seedbox must see the library and the torrent data on the same
filesystem as qBittorrent, otherwise inodes cannot match. Mount their common
parent, read-only, and map the paths qBittorrent reports with
`[qbittorrent.path_map]`.

The dashboard has no authentication: keep it on the LAN or behind a reverse
proxy with auth.

## Without a container

Python 3.11+, no dependency:

```sh
python3 -m seedbox -c seedbox.toml check
python3 -m seedbox -c seedbox.toml collect   # one run, for cron
python3 -m seedbox -c seedbox.toml run       # service: serve + collect periodically
```

## Configuration

A TOML file ([`seedbox.example.toml`](seedbox.example.toml)), found through
`--config`, `$SEEDBOX_CONFIG`, `./seedbox.toml` or `/config/seedbox.toml`.
Environment variables override it. Any of them can be read from a file with
the `_FILE` suffix (Docker secrets), e.g. `SEEDBOX_QBT_PASSWORD_FILE`.

| Variable | TOML key | Default |
|---|---|---|
| `SEEDBOX_ROOTS` (comma-separated) | `library.roots` | *(required)* |
| `SEEDBOX_MAX_DEPTH` | `library.max_depth` | `4` |
| `SEEDBOX_QBT_URL` | `qbittorrent.url` | `http://localhost:8080` |
| `SEEDBOX_QBT_USERNAME` | `qbittorrent.username` | `admin` |
| `SEEDBOX_QBT_PASSWORD` | `qbittorrent.password` | empty = no login (whitelisted client) |
| | `qbittorrent.path_map` | none |
| `SEEDBOX_PROWLARR_URL` | `prowlarr.url` | empty = disabled |
| `SEEDBOX_PROWLARR_API_KEY` | `prowlarr.api_key` | |
| | `trackers.aliases` | none |
| `SEEDBOX_OUTPUT_DIR` | `output.dir` | `/data` |
| | `output.csv_delimiter` | `,` |
| `SEEDBOX_INTERVAL_HOURS` | `service.interval_hours` | `24` |
| `SEEDBOX_PORT` | `service.port` | `8080` |

## Output

| File | Content |
|---|---|
| `index.html` | the dashboard, works offline |
| `inventory.csv` | current state, one line per entry |
| `history.csv` | one line per run: coverage, sizes, upload |
| `history-trackers.csv` | one line per run and tracker |
| `snapshot.json` | everything above, for other tools |

The coverage curve appears from the second run.

## Known limits

- 🟠 An orphan may simply not have been searched by cross-seed yet.
- 🟠 A folder holding both media files and sub-folders of other content is one entry.
- 🟠 Torrents whose files are missing from disk are matched by path only.
- 🟠 Two library entries hardlinked to each other count once (first one wins).

## Development

```sh
pre-commit install
python3 -m unittest discover -s tests -v
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request.

## License

[PolyForm Strict 1.0.0](LICENSE): source-available, **not** open source.

- You may **use** the tool for any noncommercial purpose, e.g. on your own seedbox.
- You may **not** copy, redistribute, reuse the code elsewhere, modify it
  outside of contributions, use it commercially, or build a competing product.
- **Contributions are welcome**: forking and changing the code to submit an
  issue or a pull request is explicitly allowed, see [CONTRIBUTING.md](CONTRIBUTING.md).

## Support

If this helps you keep your ratio healthy, you can buy me a coffee:

[![Buy Me a Coffee](https://img.shields.io/badge/Buy%20Me%20a%20Coffee-francoisgn-FFDD00?logo=buymeacoffee&logoColor=black)](https://buymeacoffee.com/francoisgn)
