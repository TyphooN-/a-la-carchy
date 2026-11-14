#!/usr/bin/env python3
"""Resolve calendar keys with system IANA data, never usage or credentials."""
import argparse
import datetime as dt
import json
import io
import math
import os
from pathlib import Path
import re
import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def zone_name(value):
    if (not isinstance(value, str) or not value or len(value) > 128
            or not re.fullmatch(r'[A-Za-z0-9_+-]+(?:/[A-Za-z0-9_+-]+)*', value)
            or value in ('localtime', 'posixrules')
            or value.startswith(('right/', 'posix/'))):
        raise ValueError('Unsupported timezone')
    return value


def zone_path(name):
    root = Path('/usr/share/zoneinfo').resolve(strict=True)
    path = (root / zone_name(name)).resolve(strict=True)
    path.relative_to(root)
    if not path.is_file():
        raise ValueError('Unsupported timezone')
    return path


def zone_bytes(path):
    with path.open('rb') as stream:
        data = stream.read(1024 * 1024 + 1)
    if len(data) > 1024 * 1024:
        raise ValueError('Timezone file exceeds bound')
    return data


def machine_zone():
    if 'TZ' in os.environ:
        return zone_name(os.environ['TZ'])
    return zone_name(Path('/etc/localtime').resolve(strict=True)
                     .relative_to(Path('/usr/share/zoneinfo').resolve(strict=True)).as_posix())


def calendar_clock(timezone=None, now=None):
    try:
        local_data = zone_bytes(zone_path(machine_zone()))
    except (ValueError, OSError, ZoneInfoNotFoundError):
        local_data = None
    name = zone_name(timezone) if timezone is not None else machine_zone()
    requested_path = zone_path(name)
    requested_data = zone_bytes(requested_path)
    zone = ZoneInfo.from_file(io.BytesIO(requested_data), key=name)
    now = time.time() if now is None else now
    if type(now) not in (int, float) or not math.isfinite(now) or now < 0:
        raise ValueError('Invalid clock tick')
    observed = dt.datetime.fromtimestamp(math.floor(now), dt.timezone.utc)
    if not 1970 <= observed.year <= 9998:
        raise ValueError('Unsupported clock year')
    local = observed.astimezone(zone)
    day = local.date()
    # TZif transitions have whole-second resolution. Check the fixed TTL horizon
    # for the first actual date change: a midnight rollback can return to yesterday
    # before tomorrow's nominal midnight, and a forward transition can skip a date.
    expires = observed + dt.timedelta(seconds=45)
    for seconds in range(1, 46):
        candidate = observed + dt.timedelta(seconds=seconds)
        if candidate.astimezone(zone).date() != day:
            expires = candidate
            break
    stamp = lambda value: value.strftime('%Y-%m-%dT%H:%M:%SZ')
    return {'state': 'ready', 'timezone': name,
            'observedAt': stamp(observed), 'expiresAt': stamp(expires),
            'keys': {'day': day.isoformat(),
                     'week': (day - dt.timedelta(days=day.weekday())).isoformat(),
                     'month': day.strftime('%Y-%m'), 'year': day.strftime('%Y')},
            'localCalendarAligned': local_data is not None and requested_data == local_data}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--timezone')
    parser.add_argument('--now', type=float, help='Explicit offline diagnostic Unix timestamp')
    args = parser.parse_args()
    try:
        result = calendar_clock(args.timezone, args.now)
    except (ValueError, OverflowError, OSError, ZoneInfoNotFoundError):
        print(json.dumps({'state': 'unavailable'}))
        return 2
    print(json.dumps(result, separators=(',', ':')))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
