# video

| Script | Purpose |
|---|---|
| `probe.sh FILE...` | Size, bitrate, resolution, HDR flag, audio and subtitle tracks with languages |
| `inventory.sh [--cache FILE] [--exclude NAME] PATH...` | Bulk scan, one TSV line per video: mtime, duration, size, container, codec, resolution, fps, video Mb/s, bits per pixel, HDR, scan type, audio and subtitle tracks (`--csv` for `;`). ffprobe or mediainfo, so it can run on a NAS that has either |
| `reencode.sh [options] IN OUT.mkv\|OUT.mp4` | Re-encode to x265, cap the resolution, keep only wanted languages, drop duplicate audio (`--dedupe-audio`), make audio mp4-friendly (`--audio compat`), encode an excerpt (`--start S --duration N`) |
| `dupes.sh [options] PATH...` | Find probable duplicates (same content, other resolution or encode): grouped by duration, confirmed by frame hashes and the loudness curve of the audio. Read-only TSV with a SAME / LIKELY / DIFFERENT verdict and a `--cache` so rescans are cheap |
| `queue.sh [--remote HOST] --home DIR` | Run a queue of `reencode.sh` jobs on this machine for files that live on a NAS: fetch, encode, push back, record the state. Sequential, resumable, run it in the background |

Setup: [toolbox README](../README.md#install), then optionally
`cp ../video.conf.example ~/.config/toolbox/video.conf`.

## Recipes

```sh
# Inventory of a library (TSV, redirect to a file)
./inventory.sh /path/to/library > videos.tsv

# Rescan cheaply: only files whose path, size or mtime changed are probed
./inventory.sh --cache videos.tsv --exclude .tmp /path/to/library > /dev/null

# NAS without ffprobe/mediainfo: run it where mediainfo exists (a container)
# with the folder mounted, e.g. PROBE=mediainfo bash inventory.sh --cache ...

# Inspect first: what is in there?
./probe.sh movie.mkv

# 40 GB disc remux -> 1080p x265, French and English audio and subtitles
./reencode.sh movie.remux.mkv movie.x265.mkv

# 2160p HDR -> 1080p x265 (HDR10 colour kept)
./reencode.sh --height 1080 movie.2160p.mkv movie.1080p.mkv

# Only drop tracks, no re-encode (seconds, lossless)
./reencode.sh --codec copy --audio-langs fre --sub-langs fre,eng in.mkv out.mkv

# Replace huge lossless audio (TrueHD, DTS-HD MA) by E-AC-3 640k
./reencode.sh --audio eac3 in.mkv out.mkv

# Several files
for f in in/*.mkv; do ./reencode.sh "$f" "out/$(basename "$f")"; done

# Print the ffmpeg command without running it
./reencode.sh --dry-run in.mkv out.mkv
```

Inventory notes: `vbitrate_mbps` is the stream bitrate; when the container
does not store it (mkv) it is estimated from the stream size, or the overall
bitrate minus the audio (128 kb/s assumed for an audio track of unknown
bitrate). `bpp` = video bit/s / (width x height x fps): below about 0.02 an
AVC picture is starved, 0.05 to 0.10 is typical of an efficient encode. With
`--cache` the TSV is rewritten at the end (vanished files are dropped,
unreadable ones are retried at each scan). `mtime` is epoch seconds.

Run long jobs under `caffeinate -i` on a Mac so it does not sleep.

## What to expect

| Question | Answer |
|---|---|
| x265 vs x264 | About 30-50 % smaller at the same visual quality; 10-bit also avoids banding. Plays on any recent TV/streamer box (Direct Play), not on old hardware. |
| Disc remux (30-40 GB) | A 1080p x265 at CRF 20 usually lands around 5-12 GB. Grainy films are bigger, animation much smaller. |
| 2160p to 1080p | Roughly a third of the size on top of the codec gain. HDR10 colour tags are kept, but Dolby Vision data and the HDR static metadata (mastering display, MaxCLL) are not. For a clean SDR picture use `--tonemap` (needs an ffmpeg built with zimg, e.g. Homebrew's `ffmpeg-full`). |
| CRF | 18 transparent, 20 default, 22 small, 24 very small. Lower is bigger. Test on a 2-minute excerpt first (`-ss 600 -t 120` before `-i` in a copy of the command from `--dry-run`). |
| Preset | `medium` is the default. `slow`/`slower` shave 5-10 % more for 2-3x the time. |
| Speed (Apple M4) | `libx265` medium on 1080p: around real time or a bit faster; 2160p source adds decode and scale cost. `--codec videotoolbox` is 5-10x faster but 30-60 % bigger at similar quality: fine for a quick pass, not for archiving. |
| Lossy again | Re-encoding a lossy file loses quality a second time. Always start from the best source (disc remux, not a previous encode). |
| Audio | Kept as is by default. Atmos/TrueHD tracks are the biggest non-video item (3-6 GB); `--audio eac3` keeps 5.1 at 640k (Atmos object data is lost). |
| Subtitles | Copied (text and image-based). Forced tracks are kept when their language is kept. |

## Torrents and re-encodes

A re-encoded file is a different file: it no longer matches the torrent it
came from and cannot be cross-seeded. Keep the source where the torrent seeds
from until its tracker's minimum seeding time is met, work on a copy, and
decide separately what happens to the original.

## Encoding on a workstation for files on a NAS

A low-power NAS cannot encode HEVC (a Celeron-class CPU is some 40 times slower
than a recent laptop and has no 10-bit HEVC hardware). Copy the file to the
workstation, encode there, copy the result back: a few minutes of transfer on
gigabit LAN against hours saved. Measured on an Apple M4 (10 cores), 1080p
10-bit libx265: preset `medium` about 1.2x real time, `slow` about 0.36x.

`queue.sh` automates it. In the folder given with `--home` (on the NAS when
`--remote` is used) put `queue.tsv`:

```
id <TAB> source path <TAB> destination path <TAB> reencode.sh options
```

Run it detached and come back later; it records progress in `state.tsv` (one
line per change: id, status, time, detail), `STATUS` (`running` then
`finished ... done=N failed=M`) and `logs/<id>.log`:

```sh
nohup caffeinate -i ./queue.sh --remote nas --home /path/to/home >/dev/null 2>&1 &
cat /path/to/home/STATUS        # on the NAS: is it finished?
```

Rerunning the same command resumes: ids already `done` are skipped. Outputs go
to the destination as `.part` and are renamed once their size is verified; the
source is never modified. Only ids appear on the console.

## Finding duplicates

```sh
./dupes.sh --cache dupes.cache.tsv /path/to/library > dupes.tsv
```

Candidates are files whose durations differ by at most `--tol` seconds (3 by default).
Each candidate gets a perceptual signature: a 64-bit hash of `--frames` thumbnails
(resolution independent, so a 1080p and a 2160p of the same picture match) and the
loudness curve of the first `--audio-secs` seconds (a re-encode keeps it). The verdict is
`SAME` when picture and sound agree (or both are silent), `LIKELY` when one agrees,
`DIFFERENT` otherwise (printed with `--all`). It decodes a few frames and minutes per
file, so run it when the CPU is free. It never deletes anything: pick the version to keep
yourself, using the resolution and Mb/s columns.
