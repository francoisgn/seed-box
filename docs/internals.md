# Internals

How seedbox works inside: the data flow, each mechanism and where it lives,
and the runbooks for the usual changes. Read this before touching the code;
update it in the same commit as any change to a mechanism described here.

User-facing behaviour is in the [README](../README.md), deployment in
[deployment.md](deployment.md).

## Data flow

```
library roots (disk) ─┐
qBittorrent API ──────┼─> collect.run() ─> snapshot dict ─> report.write()
Prowlarr API ─────────┤        │                               ├─ snapshot.json, inventory.csv
cross-seed.db (copy) ─┘        │                               ├─ history*.csv (append)
                               │                               └─ dashboard.write_pages() -> index/library/plex.html
                               └─ warnings (collect + config)

seedbox run (cli._Service + cli._Handler)
  ├─ collection on schedule or on demand (POST /api/collect)
  ├─ serves the output folder (pages embed the snapshot as JSON; Cache-Control: no-cache)
  ├─ live API: status, metrics, plex, upload, checks, created
  └─ write API: action, match, create, upload, check (LAN only, service.actions)
```

The pages are static HTML with the data embedded: they work as files
(offline copy) and become live when served by `seedbox run`. Everything
visual is computed client-side in `seedbox/web/app.js` from the snapshot.

## Modules

| Module | Role |
|---|---|
| `cli.py` | Commands (`collect`, `check`, `status`, `run`, `match`), the HTTP handler and the collection loop |
| `config.py` | TOML + env (+ `_FILE` secrets), validation, `map_path`/`unmap_path` (qBittorrent paths ↔ local), `PALETTE` |
| `library.py` | Disk inventory: roots → entries, inode index |
| `collect.py` | Correlation of disk, qBittorrent, Prowlarr, cross-seed; diagnosis; every table of the snapshot |
| `trackers.py` | Tracker identity: URL → tracker key |
| `prowlarr.py` | Indexers (keyed by tracker key), searches for release matching |
| `qbittorrent.py` | qBittorrent Web API v2 client |
| `crossseed.py` | cross-seed database (read-only copy): searched where, decision |
| `report.py` | Writes the snapshot files and histories; `rerender` after a deploy |
| `dashboard.py` | Page skeletons (sections, nav), inlines `web/` assets and the data |
| `web/app.js`, `web/app.css` | Home and library pages: charts, tiles, tables, actions |
| `web/upload.js`, `web/plex.js` | Upload section, Plex page |
| `actions.py` | Write actions through qBittorrent (move, recheck, start, skip files, strip trackers, remove) and the jobs list |
| `status.py` | What qBittorrent is busy with (rechecks, moves, errors, disk queue) |
| `metrics.py` | Host and qBittorrent samples every `metrics_interval` (System charts) |
| `match.py` | Release matching: find a renamed library file on the trackers, verify, rename or inject |
| `torrentfile.py` | Bencode read/write, piece proof of a local file |
| `create.py` | Create a .torrent (+ .nfo) for a tracker an entry is missing on; seed it |
| `nfo.py` | Release fields from the name and MediaInfo, .nfo text |
| `upload.py` | Checks against a tracker and uploads through a config-described API |
| `tmdb.py` | Film titles (fr/en/original), year, IMDb id |
| `titles.py` | Release name → title, year, episode, resolution |
| `plex.py` | Plex page data (read-only) |
| `disc.py` | `seedbox disc`: placeholder videos for physical discs in a Plex library (workstation side) |
| `schedule.py` | `"HH:MM"` / `"sun 04:00"` schedules |
| `api.py`, `ui.py` | urllib JSON helper; terminal colours and spinner |

## Mechanisms

### Entries and matching by inode

`library.build` turns the roots into entries (a film file with its sidecars,
a folder with one film, a season, a numbered collection) and an index
`(dev, ino) → entries`. `collect.correlate` takes each torrent's file list
from the qBittorrent API, maps the paths (`config.map_path`), stats them and
attaches the torrent to the entries owning those inodes. Renames and moves
keep the match; files not finished or being rechecked are handled apart.
Torrents matching nothing go to "Torrents outside the library" with a reason
(outside the roots, transient folder, link folder leftovers…).

### Tracker identity

One **tracker key** per tracker, used everywhere (snapshot, charts, checks,
uploads). `trackers.key_for_url`: announce or site URL → host → alias if the
host is in `[trackers.aliases]`, else the registrable domain (`domain()`,
with two-level suffixes like `co.uk`), then alias of that domain. Pseudo
trackers (DHT, PeX, LSD) give `None`.

Prowlarr indexers get their key from their site URLs (`prowlarr.indexers`),
torrents from their announce URLs (`collect._torrent_trackers`). When the
announce domain differs from the site domain, the two do not merge: the
torrent shows as an undeclared tracker until an alias maps it.

`tracker_table` = union of Prowlarr indexers (`in_prowlarr: true`, with
Prowlarr's `id`, `enabled`, `failing`) and trackers seen on torrents.

### Declared, undeclared and extra trackers

- **Declared** tracker = a key Prowlarr knows. The target set for "seeded
  everywhere" is the *enabled* declared trackers (`target` in `collect.run`).
- **Undeclared torrent** (`declared: false`, `snapshot.undeclared`): its
  tracker is not in Prowlarr (public or one-off sharing).
- **Extra trackers** (`extra_trackers` on a record, `snapshot.extra_trackers`):
  a torrent on a declared tracker that also announces to unknown ones (public
  trackers shipped in a .torrent). They leak the torrent outside the private
  tracker. The "Undeclared trackers" tile lists both; its Strip button runs
  the `strip_trackers` action (`actions._undeclared_urls`: removes every
  announce URL no declared tracker owns, only on torrents that have a
  declared one).
- Collection warnings: declared tracker seeding nothing, tracker seeding
  library content but not in Prowlarr, tracker failing in Prowlarr,
  cross-seed indexer rate limited.

### Tracker colours

One colour per tracker, the same in every chart, chip, tile and filter
(`web/app.js`, `TRACKERS` / `TCOLOR`):

1. declared trackers sorted by Prowlarr `id`, then the others;
2. `[trackers.colors]` (copied onto the tracker row by `collect.run` as
   `color`) wins: a `PALETTE` name or `#rrggbb`, validated in `config.load`;
3. otherwise slot *n* of `SLOTS` (n = position in that order);
4. trackers outside Prowlarr: neutral grey.

`PALETTE` (config.py) and `SLOT_NAMES` (app.js) list the same names in the
same order as `SLOTS`: change the three together. Adding a tracker in
Prowlarr with a higher id never recolours the others.

### Coverage, issues, duplicates

`collect.diagnose`: per entry, coverage `everywhere` (all of `target`),
`partial`, `none`; issues (incomplete, tracker errors, missing files…) with
the fixes the dashboard offers; duplicate groups (same file on a tracker
twice, several versions of a work, episodes twice). `search_status` crosses
"missing on tracker X" with the cross-seed database: searched and nothing
found (upload opportunity), other release present, not searched yet, outside
cross-seed's data folders.

### Ratios

`collect.ratio_table`: per enabled declared tracker, upload/download/count of
the torrents now in qBittorrent (not the tracker's own ratio). Other trackers
are left out. `report.write` appends them to `history-ratios.csv` and derives
7- and 30-day upload (`upload_since`). Home page: one tile per tracker on a
row of its own (`.ratio-row`, `--n` = number of tiles), in colour order.

### Histories

`history.csv` (coverage per collection), `history-trackers.csv` (entries per
tracker), `history-ratios.csv`. The "Seeded entries over time" chart is
rebuilt from torrent add dates (`collect.timeline`), so it covers the time
before seedbox existed.

### Actions and jobs

`POST /api/action` → `actions.run`: validates against the last snapshot
(destination under the roots, not in a link folder…), then calls
qBittorrent. Media are mounted read-only: every write goes through
qBittorrent. Jobs (`jobs.json`) track long operations (moves, rechecks,
creations, uploads, matching) and are refreshed from the live torrent list.

### cross-seed database

`crossseed.read` copies `cross-seed.db` (+ WAL) and reads indexers, rate
limits, and per content and tracker the last search and decision. Decision
URLs carry API keys: reduced to their domain on read.

### Release matching, creation, checks, upload

- `match.py`: TMDB titles → Prowlarr search per indexer → candidates →
  piece proof (`torrentfile.verify`) → rename the file or inject the
  tracker's torrent (perfect match: same bytes, seed it).
- `create.py`: hashes on the server; announce URL and `source` copied from a
  torrent of that tracker already in qBittorrent (so a tracker needs one
  torrent in the client before creation works).
- `upload.py`: nothing tracker-specific in code; `[upload.api]` describes
  the API (paths, fields, answer codes, limits). Checks (`check`) search the
  tracker through Prowlarr and classify matches (`classify`).

### Plex, metrics, status

Read-only side panels: `plex.py` (token from config or the mounted
Preferences.xml), `metrics.py` (`/proc` + one qBittorrent call, never the
media disks), `status.py` (rechecks, moves, errors; deletions only show up
in the log).

### Physical discs

`disc.py`, reached by `seedbox disc …` (routed first thing in `cli.main`,
before the config is loaded and validated: it runs on a workstation where
the deploy secrets are absent). It reads only `[physical]` and `[plex] url`
of the TOML file (`load_settings`). Steps of `add`: `plex_name` ("Title
(Year)" + `{edition-…}`), refuse an existing folder unless `--force`, clip
from `yt-dlp` (search `ytsearchN:`, `--url`) converted by ffmpeg
(`encode_command`: H.264 + AAC .mkv, Direct Play) or a card (`card_command`:
black video + a default forced srt, no `drawtext`), copy over ssh
(`install`: `mkdir -p` + `cat >` with every argument shell-quoted by
`remote_command`), then Plex: library found by its folder (`plex_dir`),
partial refresh of the new folder, poll until the file shows, `PUT` the
collection tags. Pure helpers are tested in `tests/test_disc.py`.

## Runbooks

### Add a tracker

No code change. Private settings (aliases, colours) go in your own
`seedbox.toml`, never in this repository.

1. Add the indexer in Prowlarr (same query/grab limits as the others).
2. Add its Torznab URL to cross-seed's `config.js`, restart cross-seed, check
   its log says the configuration is valid.
3. Optional colour: `[trackers.colors] "<site domain>" = "purple"`.
4. Deploy or wait for the next collection (`Collect now` on the dashboard).
5. After the first cross-seed injections, check the warnings: "tracker X
   seeds N entries but is not in Prowlarr" means the announce domain differs
   from the site domain → add `[trackers.aliases] "<announce domain>" =
   "<site domain>"`.

Charts, chips, ratio tiles, "missing on" filters and checks follow the
Prowlarr list.

### Remove a tracker

Disable or delete the indexer in Prowlarr and remove it from cross-seed.
A disabled indexer leaves the "everywhere" target; its torrents still in
qBittorrent show as undeclared once the indexer is deleted.

### Release

1. Bump `seedbox/__init__.py`, add the CHANGELOG entry.
2. `pre-commit run --all-files`, commit `Release X.Y.Z: …`, check the commit
   landed (subject, clean `git status`) before going on.
3. Push, then the annotated tag `vX.Y.Z` on that commit: it builds the
   image (`.github/workflows/release.yml`). Tags cannot be deleted: a wrong
   tag means a new patch release.
4. Once the image is built: `deploy/deploy.sh` (version read from the
   checkout), see [deployment.md](deployment.md).
