# Toolbox

Command-line tools that the seedbox itself does not need but that its
operator does: they run on a **workstation** (a laptop or desktop with a fast
CPU), not on the NAS or in the container. Scripts here are portable
(`bash`, POSIX tools, no reference to any particular setup) and take their
settings from options or a config file built from a `*.example` template.

| Folder | Purpose |
|---|---|
| [`video/`](video/README.md) | Inspect and re-encode videos: x265, 2160p to 1080p, drop audio/subtitle tracks |
| `lib/ui.sh` | Shared coloured statuses (`ok`, `warn`, `ko`, `info`), off without a TTY or with `NO_COLOR` |

## Where things run

- **Workstation**: anything CPU-heavy (encoding, transcription). Apple
  Silicon, a recent x86 or a GPU box all work; the NAS usually cannot
  transcode.
- **NAS / seedbox**: stays the home of the library and the torrents. Move
  files with `rsync` (resumable) in both directions, for example
  `rsync -avP --partial nas:/path/to/source/ ./work/in/` then back the same
  way. Never write into a folder a torrent is seeding from.
- Keep a working tree on the workstation with `in/`, `out/` and `logs/`
  folders; nothing in this repository depends on its name or location.

## Install

Tools are installed by the package manager, not vendored here.

| Tool | macOS (Homebrew) | Debian/Ubuntu | Needed by |
|---|---|---|---|
| ffmpeg + ffprobe | `brew install ffmpeg` | `apt install ffmpeg` | `video/` |
| MediaInfo (optional) | `brew install mediainfo` | `apt install mediainfo` | reading tracks by eye |
| MKVToolNix (optional) | `brew install mkvtoolnix` | `apt install mkvtoolnix` | edit tracks/chapters in place |
| HandBrake CLI (optional) | `brew install handbrake` | `apt install handbrake-cli` | alternative encoder |

Binaries and release notes: <https://ffmpeg.org/download.html>,
<https://mediaarea.net/en/MediaInfo>, <https://mkvtoolnix.download/>,
<https://handbrake.fr/downloads2.php>.

## Conventions

- Output in English, coloured statuses, never mix languages.
- Scripts never modify their input and refuse to overwrite an output.
- Config: copy the `*.example` template to
  `${XDG_CONFIG_HOME:-$HOME/.config}/toolbox/` and edit; keep your copy out
  of version control.
- Shellcheck-clean (`pre-commit run --all-files`), bash 3.2 compatible
  (macOS default).
