"""Terminal output: ok (green), warn (yellow), ko (red), info (blue), spinner.

Colors are disabled when the stream is not a TTY or NO_COLOR is set.
"""

import os
import sys
import threading

_CODES = {"ok": "32", "warn": "33", "ko": "31", "info": "34"}
_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def _use_color(stream):
    return hasattr(stream, "isatty") and stream.isatty() and not os.environ.get("NO_COLOR")


def _say(level, message, stream):
    label = f"{level:<7}"
    if _use_color(stream):
        label = f"\033[{_CODES[level]}m{label}\033[0m"
    print(f"{label} {message}", file=stream, flush=True)


def ok(message):
    _say("ok", message, sys.stdout)


def warn(message):
    _say("warn", message, sys.stdout)


def ko(message):
    _say("ko", message, sys.stderr)


def info(message):
    _say("info", message, sys.stdout)


class Spinner:
    """Context manager animating a spinner on stderr while a long task runs.

    Silent when stderr is not a TTY (container logs, cron). `update()` changes
    the message, e.g. to show progress.
    """

    def __init__(self, message):
        self.message = message
        self._stop = threading.Event()
        self._thread = None

    def update(self, message):
        self.message = message

    def _run(self):
        color = _use_color(sys.stderr)
        i = 0
        while not self._stop.wait(0.08):
            frame = _FRAMES[i % len(_FRAMES)]
            if color:
                frame = f"\033[34m{frame}\033[0m"
            sys.stderr.write(f"\r\033[K{frame}       {self.message}")
            sys.stderr.flush()
            i += 1
        sys.stderr.write("\r\033[K")
        sys.stderr.flush()

    def __enter__(self):
        if sys.stderr.isatty():
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        if self._thread:
            self._thread.join()
        return False
