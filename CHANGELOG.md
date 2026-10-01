# Changelog

## 2.0.3

- Upload section, films missing there: a **Create .torrent + .nfo** button in
  each film's detail (the library's dialog, for that tracker), to upload by
  hand on the tracker's site while sending through its API is off. Disabled
  when the check found the release already there: seed that one instead.

## 2.0.2

- Library page: the Upload link of the rail and its section are there from
  the start when an upload API is configured (#6). They used to appear once
  the API answered, a second later, pushing "Back Home" down under the
  cursor.

## 2.0.1

- Tracker chips look like the status badges (the tracker's colour faded
  behind), in the library and in the Upload section: its title, and the
  trackers already seeding each film missing there.
- Films missing there: filter chips as in the library (resolution, language,
  seeded on each tracker or nowhere, check result, with counts) instead of
  drop-down menus.

## 2.0.0

Major: the dashboard becomes several pages, and library management and
advanced cross-seeding get a page of their own.

- **Home page** (`index.html`), the control plane: warnings, overview,
  system, qBittorrent activity, logs.
- **Library page** (`library.html`), the library management plane: library,
  duplicates, a lighter activity (errors, jobs, busy torrents), and the
  Upload section (1.8.0, 1.9.0): films missing on a tracker with an upload
  API, checks, upload, verify and inject a release the tracker already has,
  or keep its `.torrent` for review.
- Navigation: the rail lists the page's sections, then, after a separator,
  the other page (Library page / Back Home); the rail and the top bar stay in
  place while the content cross-fades, both ways (View Transitions, recent
  Chrome and Safari). The home page's tiles open the library page filtered.
- Library page header: Clawd reading in front of a bookcase, "Library
  Management plane".
- Light UX rework:
  - status colours on the gauges (library shared: under 20 % error, 50 %
    warning, 66 % info, then ok; volume usage: under 50 % ok, 75 % info,
    90 % warning, then error) and on the tracker ratios (under 0.5 error,
    1 warning, 2 info, then ok);
  - coverage by tracker in shades of one blue;
  - torrents added per day: cross-seed in blue, other additions (downloads,
    own uploads) in green;
  - system charts: CPU with a filled area, busiest disk in orange like IO
    wait, qBittorrent upload (blue) and download (green) with filled areas;
    thinner lines.

## 1.9.0

- Two pages: the control plane's home (warnings, overview, system,
  activity, logs) and `library.html` (library, duplicates, activity,
  upload). The home page's tiles open the library page with their filter;
  `/upload` now leads to the Upload section of the library page.
- Upload check: a release the tracker already has can be verified against the
  local file and injected (release matching), or its `.torrent` kept in
  `<output>/review/` with what is known of the local entry (JSON), to be
  matched by hand. Those files, like created ones, are never served.

## 1.8.0

- Upload page for a tracker with an upload API, described in the config
  (`[upload.api]`: paths, headers, fields, answer codes, limits; no tracker
  built in): the library's films missing there, filters (resolution,
  language, other trackers, seeders, upload), a check per film (targeted
  searches on the tracker, the name against MediaInfo, TMDB), then upload,
  one at a time, and seeding of the same torrent. Sending stays off until
  `[upload] send = true`.
- `.nfo` layout: the seedbox pirate in ASCII, "automated through seedbox",
  release, fields, video, audio and subtitle tracks, then MediaInfo.
- Release matching searches title + year + release group, then + resolution,
  before title + year: some indexers return 50 results at most, and the
  release sought was beyond them for popular titles.

## 1.7.3

- A `.torrent` creation still queued can be cancelled from the jobs list (the
  one hashing runs to the end).
- A seedbox restart marks the background jobs it stopped as failed right away,
  instead of leaving them running for 13 hours.
- Jobs list: job statuses are saved when they change (removals and category
  moves stayed "pending" and old jobs piled up); it keeps the 60 most recent
  jobs, more only while more are open, in pages of 30 without a page limit.
- Auto refresh every 90 s instead of 60 s.

## 1.7.2

- A created `.torrent` and its `.nfo` download once: the page polled every
  5 s without waiting for the answer, and `/api/status` can take longer, so
  each overlapping answer started the downloads again.

## 1.7.1

- Torrents added by seedbox (release matching inject, Seed of a created
  torrent) no longer go to qBittorrent's incomplete-downloads folder ("Keep
  incomplete torrents in"): the recheck of an injected release looked there,
  found nothing and stayed at 0 %.

## 1.7.0

- Library: 10 entries at first, then pages of 20 (first, last and nearby
  pages, previous / next), instead of 100 then "show 200 more".
- Logs: 10 lines at first, then pages of 20, with a level filter (info,
  warning, error, any combination; warnings and errors by default).
- Activity: the dashboard jobs come before the busy torrents.

## 1.6.1

- Same content as 1.6.0 below: the `v1.6.0` tag (and its image) was set on
  the 1.5.0 commit by mistake, and tags cannot be moved. Use 1.6.1.

## 1.6.0

- Tracker upload: a release form before creating the `.torrent`, prefilled
  from the name (title, year, language, source, group) and MediaInfo
  (resolution, video and audio codecs, channels, bit depth, HDR / Dolby
  Vision), mandatory fields to complete when missing. A `.nfo` with these
  fields, the audio and subtitle tracks and the full MediaInfo report is
  downloaded beside the `.torrent`.
- The container image now includes `mediainfo`.

## 1.5.0

- Uploading to a tracker: in an entry's detail, **Create a .torrent for**
  each declared tracker it is missing on. The `.torrent` is hashed on the
  server (no network read of the files), private, with the announce URL and
  the `source` field taken from that tracker's torrents in qBittorrent. The
  page downloads it as soon as it is ready; once uploaded, **Seed** it from
  Activity, jobs (added to qBittorrent on the library files, hash check
  skipped). At most `output.created_max` (10) kept in `<output>/created/`,
  the oldest replaced, deletable by hand, served only through the API.

## 1.4.0

- Ratio tiles: one per declared tracker (Prowlarr / cross-seed) plus one for
  the other trackers: upload / download of the torrents in qBittorrent, the
  volumes, and the upload over 7 and 30 days from a new per-collection history
  (`history-ratios.csv`). They replace the Uploaded and Library tiles.
- Overview tiles in four rows: library and sources, ratios, problems, work.
  Charts in 1/3 - 2/3 rows, donut legends on the left.
- Library coverage since the first torrent: rebuilt from the add dates from 0,
  with the values measured at each collection on top.
- cross-seed indexers: a rate limit whose retry time is past shows as over
  (cross-seed keeps the status until it queries the indexer again) and no
  longer raises a collection warning; an active one shows until when.

## 1.3.0

- Orphan link files: files in the cross-seed folders that no torrent uses
  (torrents removed without their files), counted at each collection with the
  space deleting them frees, in the Warnings section with a cleanup script.
- Removing duplicate extras, or a torrent that only seeds from its cross-seed
  links: "also delete their link files" is ticked by default, so no orphan is
  left behind.

## 1.2.1

- The dashboard page is rebuilt when `seedbox run` starts, from the last
  snapshot: after an upgrade without a new collection, the previous version's
  page stayed served (1.1.x and 1.2.0 features only showed after a collection).
- Version in the top bar; when the running seedbox differs from the page's
  (upgrade while the page is open), it turns into a reload link.

## 1.2.0

- Torrents outside the library: a Remove button per torrent; for one that
  only seeds from its cross-seed links (library copy deleted), the links can
  go with it and the space is freed.
- Latest qBittorrent errors: Clear hides the warnings and errors logged so
  far (a marker in `ui-state.json`: qBittorrent's log itself cannot be
  cleared through its API); the empty list says when it was cleared.

## 1.1.1

The release matching below. `1.1.0` was tagged by mistake on the 1.0.1
commit: its image and release are identical to 1.0.1, use 1.1.1.

- Release matching ("Find on trackers" in an entry's detail, `seedbox match`):
  search the trackers through Prowlarr under every TMDB title of the film
  (French, English, original) and its IMDb id, candidates of the exact file
  size, proof by hashing a sample of the .torrent's pieces from the local file.
- Apply as a background job: inject the release (stopped, pointing at the
  library file, started only after a 100 % recheck) and/or rename the library
  file to the release name through qBittorrent, the other torrents on the file
  following; `mv` script for the sidecars.
- Optional TMDB API key: `[tmdb] api_key`, `SEEDBOX_TMDB_API_KEY(_FILE)`,
  `TMDB_API_KEY_CMD` in the deploy config.
- Jobs list shows the progress note of background jobs.

## 1.0.1

- Move destinations resolved (`realpath`) before the library-root check: a
  symlink inside a root can no longer lead a move outside it.
- Image without pip and its bundled wheels (setuptools, msgpack CVEs; seedbox
  has no dependency).
- Releases: SBOM and signed build provenance on the image, GitHub release notes
  from this changelog; weekly container scan in the Security tab.

## 1.0.0

- Dashboard layout: Warnings first (collection warnings, torrents outside the
  library), then Overview, System, Activity, Duplicates, Library, Logs.
- Seeded entries over time: stacked area per tracker, the total is the top of
  the stack; swapped with "Torrents added per day".
- Latest qBittorrent errors and jobs sent from the dashboard: 5 lines, then
  pages (15 and 30 lines, 2 pages).
- Auto refresh button shows its period.
- Library search box moved into the Library section.
- Regroup plan removed (`library.merge_from`, `library.merge_into` are ignored).
- Link category taken from `cross_seed.link_category` everywhere, not the
  hard-coded default.
- README: logo, torrents deleted by their tracker, unreadable files;
  social preview in `docs/assets/`.

## 0.9.8

- `seedbox check` flags media files nobody can read (mode 000 and the like),
  once per inode: qBittorrent shows their torrents as seeding until a peer
  asks, then fails with `file_open`.
- Regroup plan takes sub-folders of `merge_from` too (`films/saga/Hannibal/…`),
  flat into `merge_into`.
- Dashboard content centred beside the section rail on wide screens.

## 0.9.7

- "Disk queue" becomes "qBittorrent disk I/O": block reads and writes waiting
  inside qBittorrent, not torrents. New list of the torrents using the disk
  (move, running recheck, downloads, uploads with their speed).
- "Queued jobs" becomes "Queued moves & removals"; rechecks pending split into
  running and waiting their turn.
- Latest qBittorrent errors: warnings and errors from its log on their own
  card, no longer pushed out by bursts of moves.
- Torrents deleted by their tracker also caught on HTTP 404, unless the whole
  tracker answers 404 (announce URL or passkey changed). One button removes
  them all from the "Torrents in error" tile.
- `seedbox status` shows the same: disk I/O sources, running/waiting rechecks,
  latest errors.

## 0.9.6

- Upload opportunity split: "absent: upload it" (absent from a tracker
  cross-seed searched) vs "other release present" (only other releases of the
  title there: an upload may be refused as a dupe).
- Library filters combine: a seeding group (everywhere, partial, not seeded,
  downloading) and a situation group (problems, duplicates, absent, other
  release, not searched yet); OR inside a group, AND between groups. Counts
  follow the other group's selection.

## 0.9.5

- Copy script works on the plain-http dashboard: the clipboard API only exists
  on secure pages, so fall back to a temporary textarea, then select the text
  for Cmd/Ctrl+C.

## 0.9.4

- Categories tile: each torrent checked against the qBittorrent category that
  matches its folder (library torrents), the link category (cross-seed links),
  or the transient folder (`transient_dir`, until finished). One-click fixes:
  set the matching category (nothing moves), or apply the category folder
  (auto management on: qBittorrent moves the torrent).
- Regroup through the category whose folder is the target: set it, auto
  management on, qBittorrent moves one torrent at a time.
- "Outside declared trackers" tile: torrents on trackers Prowlarr does not
  know (public, one-off), with ratio and seeding time; clean the finished ones,
  with their files when they sit in the transient folder.
- Actions `set_category`, `apply_category`; files may be deleted for transient
  downloads too (never for library content).

## 0.9.3

- Collect now: reported as running right away, so the page does not reload
  before the collection starts.
- Regroup and cross-seed settings are part of the config fingerprint: changing
  them triggers a collection at the next start.

## 0.9.2

- cross-seed search history (its database, read-only through `CROSS_SEED_DIR`):
  for content missing on a tracker, **upload opportunity** (searched there,
  nothing matching, or only another release) vs **not searched yet** vs outside
  cross-seed's data folders. Tiles, library filters, per-tracker detail, and the
  cross-seed indexer states (rate limited…). Decision URLs carry tracker API keys:
  only their domain is read.
- Torrents deleted by their tracker (unregistered, dupe, trumped…) and tracker
  errors detected; "Torrents in error" tile with remove buttons.
- Regroup plan (`merge_from`, `merge_into`): torrents moved by qBittorrent in one
  click, a `mv` script for the entries without a library torrent. A move may
  target a folder that does not exist yet (qBittorrent creates it).
- The misleading "move by hand" command on entries without a library torrent is
  gone.
- Episodes twice: the files of each episode are listed.
- Font: Inconsolata (Google Fonts, system monospace offline).
- Template filling no longer breaks on an "@" in the page (the font URL).

## 0.9.1

- Volume usage: one volume seen through several bind mounts is shown once.

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
