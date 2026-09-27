"""Collection schedule for `seedbox run`: "HH:MM" daily, or days + time.

Examples: "04:00", "sun 04:00", "mon,thu 03:30". Local time of the process
(set TZ in the container).
"""

import re
from datetime import datetime, timedelta

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

_PATTERN = re.compile(r"^(?:(?P<days>[a-z]{3}(?:,[a-z]{3})*)\s+)?(?P<h>\d{1,2}):(?P<m>\d{2})$")


def parse(text):
    """Return (weekday set, hour, minute). Raises ValueError on bad syntax."""
    match = _PATTERN.match(text.strip().lower())
    if not match:
        raise ValueError(f"invalid schedule {text!r}, expected e.g. '04:00' or 'sun 04:00'")
    hour, minute = int(match["h"]), int(match["m"])
    if hour > 23 or minute > 59:
        raise ValueError(f"invalid time in schedule {text!r}")
    days = set(range(7))
    if match["days"]:
        names = match["days"].split(",")
        unknown = [d for d in names if d not in DAYS]
        if unknown:
            raise ValueError(f"unknown day(s) {', '.join(unknown)} in schedule {text!r}")
        days = {DAYS.index(d) for d in names}
    return days, hour, minute


def next_run(spec, now):
    """First datetime strictly after `now` matching the parsed schedule."""
    days, hour, minute = spec
    for offset in range(8):
        candidate = (now + timedelta(days=offset)).replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate > now and candidate.weekday() in days:
            return candidate
    raise AssertionError("unreachable: a weekday always matches within 8 days")


def describe(spec):
    days, hour, minute = spec
    when = "every day" if len(days) == 7 else ",".join(DAYS[d] for d in sorted(days))
    return f"{when} at {hour:02d}:{minute:02d}"


def now():
    return datetime.now()
