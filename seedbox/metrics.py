"""Lightweight host and qBittorrent sampling for the system charts.

One sample every `metrics_interval` seconds (5 min by default): a few /proc
files and one small qBittorrent call, nothing that touches the media disks.
In a container, /proc/stat, /proc/meminfo, /proc/loadavg and /proc/diskstats
describe the host (no lxcfs on a NAS), which is what we want to see.

Samples go to <output>/metrics.jsonl, trimmed to `metrics_days`.
"""

import json
import os
import threading
import time

from seedbox import plex
from seedbox.api import ApiError

_lock = threading.Lock()


def _read(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        return ""


def parse_cpu(text):
    """(total jiffies, idle jiffies, iowait jiffies) from /proc/stat."""
    for line in text.splitlines():
        if line.startswith("cpu "):
            values = [int(v) for v in line.split()[1:]]
            idle = values[3] if len(values) > 3 else 0
            iowait = values[4] if len(values) > 4 else 0
            return sum(values[:8]), idle, iowait
    return 0, 0, 0


def parse_meminfo(text):
    info = {}
    for line in text.splitlines():
        name, _, rest = line.partition(":")
        parts = rest.split()
        if parts and parts[0].isdigit():
            info[name] = int(parts[0]) * 1024
    total = info.get("MemTotal", 0)
    available = info.get("MemAvailable", info.get("MemFree", 0) + info.get("Cached", 0))
    return total, available


def parse_diskstats(text):
    """{device: (sectors read, sectors written, ms doing IO)} for whole disks (sdX, nvmeXnY)."""
    out = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 14:
            continue
        name = parts[2]
        whole = (name.startswith(("sd", "hd", "vd")) and name[-1].isalpha()) or (
            name.startswith("nvme") and "p" not in name[4:]
        )
        if whole:
            out[name] = (int(parts[5]), int(parts[9]), int(parts[12]))
    return out


def parse_sectors(text, names):
    """{device: (sectors read, sectors written)} for the given devices."""
    out = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 14 and parts[2] in names:
            out[parts[2]] = (int(parts[5]), int(parts[9]))
    return out


def top_devices(sys_block="/sys/block"):
    """Block devices at the top of the stack: what the filesystems read and write.

    RAID members and the disks under them count an IO several times (mirror
    copies, parity), so throughput is measured on the devices nothing else
    holds (LVM volumes, md arrays used directly, plain disks), from
    /sys/block/*/holders. None when sysfs is not readable: whole disks then.
    """
    try:
        names = os.listdir(sys_block)
    except OSError:
        return None
    top = set()
    for name in names:
        if name.startswith(("loop", "ram", "zram", "synoboot", "sr", "fd")):
            continue
        base = os.path.join(sys_block, name)
        held = [os.path.join(base, "holders")] + [
            os.path.join(base, p, "holders") for p in _listdir(base) if p.startswith(name)
        ]
        if not any(_listdir(h) for h in held):
            top.add(name)
    return top or None


def _listdir(path):
    try:
        return os.listdir(path)
    except OSError:
        return []


def volumes(paths):
    """Usage of the filesystems holding paths, one row per device."""
    seen = {}
    for path in paths:
        try:
            st = os.stat(path)
            vfs = os.statvfs(path)
        except OSError:
            continue
        # Bind mounts of one volume can show different st_dev in a container:
        # the filesystem's own counters identify it.
        key = (vfs.f_blocks, vfs.f_files, vfs.f_frsize)
        if st.st_dev in seen or key in seen:
            continue
        total = vfs.f_blocks * vfs.f_frsize
        free = vfs.f_bavail * vfs.f_frsize
        seen[st.st_dev] = seen[key] = {"path": path, "total": total, "used": total - free, "free": free}
    unique = []
    for row in seen.values():
        if row not in unique:
            unique.append(row)
    return unique


class Sampler:
    """Keeps the previous counters to turn them into rates."""

    def __init__(self, cfg, client_factory):
        self.cfg = cfg
        self.client_factory = client_factory
        self.client = None
        self.prev = None
        self.top = None

    def sample(self):
        now = time.time()
        cpu = parse_cpu(_read("/proc/stat"))
        stats = _read("/proc/diskstats")
        disks = parse_diskstats(stats)
        top = self.top if self.top is not None else top_devices()
        self.top = top
        io = parse_sectors(stats, top) if top else {k: v[:2] for k, v in disks.items()}
        mem_total, mem_avail = parse_meminfo(_read("/proc/meminfo"))
        load = _read("/proc/loadavg").split()
        point = {
            "t": int(now),
            "mem_used_pct": round((mem_total - mem_avail) / mem_total * 100, 1) if mem_total else None,
            "load1": float(load[0]) if load else None,
        }
        if self.prev:
            dt = now - self.prev["time"]
            total = cpu[0] - self.prev["cpu"][0]
            if total > 0:
                point["cpu_pct"] = round((1 - (cpu[1] - self.prev["cpu"][1]) / total) * 100, 1)
                point["iowait_pct"] = round((cpu[2] - self.prev["cpu"][2]) / total * 100, 1)
            # Busy: the busiest physical disk. Throughput: the top of the stack,
            # so a RAID write is not counted once per member.
            busy, read, written = 0.0, 0, 0
            for name, (_, _, io_ms) in disks.items():
                old = self.prev["disks"].get(name)
                if old:
                    busy = max(busy, (io_ms - old[2]) / (dt * 1000) * 100)
            for name, (r, w) in io.items():
                old = self.prev["io"].get(name)
                if old:
                    read += r - old[0]
                    written += w - old[1]
            if dt > 0 and disks:
                point["disk_busy_pct"] = round(min(busy, 100), 1)
                point["disk_read_bps"] = int(read * 512 / dt)
                point["disk_write_bps"] = int(written * 512 / dt)
        self.prev = {"time": now, "cpu": cpu, "disks": disks, "io": io}
        info = self._transfer()
        if info:
            point["up_bps"] = info.get("up_info_speed", 0)
            point["dl_bps"] = info.get("dl_info_speed", 0)
        # Plex playback at the same moment: whether the disks keep up with a film.
        playing = plex.playback(self.cfg)
        if playing:
            point.update(playing)
        return point

    def _transfer(self):
        """qBittorrent speeds, reusing the session; one new login if it expired."""
        for _ in range(2):
            try:
                if self.client is None:
                    self.client = self.client_factory()
                return self.client.transfer()
            except ApiError:
                self.client = None
        return None


def _path(cfg):
    return os.path.join(cfg.output_dir, "metrics.jsonl")


def append(cfg, point):
    os.makedirs(cfg.output_dir, exist_ok=True)
    horizon = time.time() - cfg.metrics_days * 86400
    with _lock:
        path = _path(cfg)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(point, separators=(",", ":")) + "\n")
        # Trim about once a day's worth of samples, not on every write.
        if point["t"] % 86400 < max(cfg.metrics_interval, 60):
            rows = [r for r in read(cfg) if r.get("t", 0) >= horizon]
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as handle:
                handle.writelines(json.dumps(r, separators=(",", ":")) + "\n" for r in rows)
            os.replace(tmp, path)


def read(cfg, since=0):
    rows = []
    try:
        with open(_path(cfg), encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get("t", 0) >= since:
                    rows.append(row)
    except OSError:
        pass
    return rows


def series(cfg, hours=48, buckets=288):
    """Samples of the last `hours`, averaged into at most `buckets` points."""
    since = time.time() - hours * 3600
    rows = read(cfg, since)
    if len(rows) <= buckets:
        return rows
    width = hours * 3600 / buckets
    grouped = {}
    for row in rows:
        grouped.setdefault(int((row["t"] - since) // width), []).append(row)
    out = []
    for _, group in sorted(grouped.items()):
        merged = {"t": group[len(group) // 2]["t"]}
        # Every key of the group: a field added by a newer version is not in the older samples.
        for key in sorted(set().union(*group)):
            if key == "t":
                continue
            values = [r[key] for r in group if isinstance(r.get(key), (int, float))]
            if values:
                merged[key] = round(sum(values) / len(values), 1)
        out.append(merged)
    return out


def loop(cfg, client_factory, log):
    """Background thread body for `seedbox run`."""
    if cfg.metrics_interval <= 0:
        return
    sampler = Sampler(cfg, client_factory)
    sampler.sample()  # primes the counters
    while True:
        time.sleep(cfg.metrics_interval)
        try:
            append(cfg, sampler.sample())
        except OSError as exc:
            log.warn(f"metrics: {exc}")
