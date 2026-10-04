#!/usr/bin/env python3
"""Collect documented Grok status-line counters. No credentials or network."""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
import time

FIELDS = ('input_tokens', 'output_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens')
MAX = 2**53 - 1
PAYLOAD_LIMIT = 1024 * 1024


def parse(payload):
    if not isinstance(payload, dict):
        raise ValueError('payload must be an object')
    schema = payload.get('schema_version')
    if type(schema) is not int or schema < 1:
        raise ValueError('unsupported status-line schema')
    sid = payload.get('session_id')
    if not isinstance(sid, str) or not re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', sid):
        raise ValueError('invalid session identifier')
    context = payload.get('context_window')
    if not isinstance(context, dict):
        return None
    usage = context.get('session_usage')
    if usage is None:
        # Known full totals of zero prove an empty session, allowing us to
        # count its first call. Otherwise absence is unknown, never zero.
        if type(context.get('session_input_tokens')) is int and context['session_input_tokens'] == 0 and type(context.get('session_output_tokens')) is int and context['session_output_tokens'] == 0:
            usage = dict.fromkeys(FIELDS, 0)
        else:
            return None
    if not isinstance(usage, dict) or any(k not in usage for k in FIELDS):
        raise ValueError('incomplete usage counters')
    values = tuple(usage[k] for k in FIELDS)
    if any(type(v) is not int or not 0 <= v <= MAX for v in values) or sum(values) > MAX:
        raise ValueError('invalid usage counters')
    model = payload.get('model')
    model = model.get('id') if isinstance(model, dict) else None
    if not isinstance(model, str) or not model or len(model) > 256 or any(ord(c) < 32 for c in model):
        raise ValueError('model identity unavailable')
    # The official contract says these splits sum to full session input.
    for key, total in [('session_input_tokens', values[0] + values[2] + values[3]), ('session_output_tokens', values[1])]:
        if key in context and (type(context[key]) is not int or context[key] != total):
            raise ValueError('inconsistent session totals')
    return sid, model, values


class Ledger:
    def __init__(self, path):
        self.db = sqlite3.connect(path, timeout=5)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY, model TEXT NOT NULL, i INTEGER NOT NULL,
            o INTEGER NOT NULL, r INTEGER NOT NULL, w INTEGER NOT NULL, seen REAL NOT NULL);
          CREATE TABLE IF NOT EXISTS daily (
            day TEXT NOT NULL, model TEXT NOT NULL, i INTEGER NOT NULL,
            o INTEGER NOT NULL, r INTEGER NOT NULL, w INTEGER NOT NULL, PRIMARY KEY(day,model));
          CREATE TABLE IF NOT EXISTS coverage (day TEXT PRIMARY KEY);
          CREATE INDEX IF NOT EXISTS daily_model ON daily(model);
        ''')

    def observe(self, observation, now):
        sid, model, values = observation
        day = dt.datetime.fromtimestamp(now).strftime('%Y-%m-%d')
        cutoff_date = dt.datetime.fromtimestamp(now).date() - dt.timedelta(days=364)
        cutoff = dt.datetime.combine(cutoff_date, dt.time()).timestamp()
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            self.db.execute('DELETE FROM daily WHERE day < ?', (str(cutoff_date),))
            self.db.execute('DELETE FROM coverage WHERE day < ?', (str(cutoff_date),))
            self.db.execute('DELETE FROM sessions WHERE seen < ?', (cutoff,))
            old = self.db.execute('SELECT model,i,o,r,w,seen FROM sessions WHERE id=?', (sid,)).fetchone()
            if old and now < old[5]:
                raise ValueError('clock moved backwards')
            if old and old[0] == model and tuple(old[1:5]) == values and now - old[5] < 5:
                return False
            if not old and self.db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0] >= 4096:
                raise ValueError('retained session bound exceeded')
            delta = (0, 0, 0, 0)
            bucket = model
            if old and all(new >= previous for new, previous in zip(values, old[1:5])):
                same_day = dt.datetime.fromtimestamp(old[5]).strftime('%Y-%m-%d') == day
                if same_day or now - old[5] <= 25:
                    delta = tuple(new - previous for new, previous in zip(values, old[1:5]))
                    if old[0] != model:
                        # Current-model metadata cannot identify calls between
                        # observations spanning a switch. Don't misattribute them.
                        bucket = 'Unattributed (model changed between observations)'
            existing = self.db.execute('SELECT i,o,r,w FROM daily WHERE day=? AND model=?', (day, bucket)).fetchone()
            if existing and sum(existing) + sum(delta) > MAX:
                raise ValueError('daily token bound exceeded')
            known_model = self.db.execute('SELECT 1 FROM daily WHERE model=? LIMIT 1', (bucket,)).fetchone()
            if not known_model and self.db.execute('SELECT COUNT(DISTINCT model) FROM daily').fetchone()[0] >= 256:
                raise ValueError('retained model bound exceeded')
            self.db.execute('INSERT OR REPLACE INTO sessions VALUES (?,?,?,?,?,?,?)', (sid, model, *values, now))
            self.db.execute('INSERT INTO daily VALUES (?,?,?,?,?,?) ON CONFLICT(day,model) DO UPDATE SET i=i+excluded.i,o=o+excluded.o,r=r+excluded.r,w=w+excluded.w', (day, bucket, *delta))
            self.db.execute('INSERT OR IGNORE INTO coverage VALUES (?)', (day,))
        return True

    def record(self, now, status='Observed tokens · subscription quota unavailable'):
        today = dt.datetime.fromtimestamp(now).date()
        models, totals, today_models = {}, {}, {}
        for day, model, i, o, r, w in self.db.execute('SELECT day,model,i,o,r,w FROM daily ORDER BY day,model'):
            bucket = models.setdefault(model, {'inputTokens': 0, 'outputTokens': 0, 'cacheReadInputTokens': 0, 'cacheCreationInputTokens': 0})
            for key, value in zip(bucket, (i, o, r, w)):
                bucket[key] += value
            totals[day] = totals.get(day, 0) + i + o + r + w
            if day == str(today):
                today_models[model] = i + o + r + w
        known = {row[0] for row in self.db.execute('SELECT day FROM coverage')}
        # The stock widget coerces null/missing counts to zero. Omit unknown
        # days entirely; only dated observations earn a chart row.
        days = []
        for offset in range(6, -1, -1):
            day = str(today - dt.timedelta(days=offset))
            if day in known:
                days.append({'date': day, 'messageCount': totals.get(day, 0), 'recorded': True})
        return {'id': 'grok', 'name': 'Grok', 'ready': bool(known),
                'tierLabel': '', 'usageStatusText': status,
                'authHelpText': 'Token-only collection from Grok status-line metadata. Subscription quotas are not exported by this interface. First observations are baselines; inherited history is not backfilled. Calendar gaps and model switches can leave unknown attribution.',
                # A genuinely unknown allowance, not an invented weekly window.
                # Native Agents uses -1 to display an unavailable value as —.
                'limits': [{'key': 'quota', 'label': 'Subscription quota', 'percent': -1}],
                'hasLocalStats': bool(known), 'hasPromptStats': False,
                'totalPrompts': 0, 'totalSessions': 0, 'activeDays': sum(n > 0 for n in totals.values()),
                'todayTotalTokens': totals.get(str(today), 0), 'todayTokensByModel': today_models,
                'recentDays': days, 'modelUsage': models}


def publish(path, record):
    path = Path(path)
    encoded = json.dumps(record, allow_nan=False, ensure_ascii=True) + '\n'
    try:
        if path.read_text() == encoded:
            return False
    except FileNotFoundError:
        pass
    fd, name = tempfile.mkstemp(prefix='.grok-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)
    return True


def main():
    state = Path(os.environ.get('XDG_STATE_HOME') or Path.home()/'.local/state')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', type=Path, default=state/'omarchy/grok-usage')
    parser.add_argument('--output', type=Path, help='Alternative record path; parent must exist')
    parser.add_argument('--hook', action='store_true', help='Read one documented status-line payload on stdin and publish')
    parser.add_argument('--initialize', action='store_true', help='Publish waiting state without reading input')
    args = parser.parse_args()
    if args.hook == args.initialize:
        parser.error('select exactly one of --hook or --initialize')
    args.state_dir.mkdir(parents=True, exist_ok=True)
    output = args.output or args.state_dir/'agents/grok.json'
    if args.output is None:
        output.parent.mkdir(parents=True, exist_ok=True)
    ledger = Ledger(args.state_dir/'tokens.sqlite3')
    try:
        status = 'Waiting for Grok token metadata · quota unavailable'
        if args.hook:
            try:
                raw = sys.stdin.buffer.read(PAYLOAD_LIMIT + 1)
                if len(raw) > PAYLOAD_LIMIT:
                    raise ValueError('payload too large')
                observation = parse(json.loads(raw))
                if observation is not None:
                    ledger.observe(observation, time.time())
                    status = 'Observed tokens · subscription quota unavailable'
            except (ValueError, KeyError, TypeError, UnicodeError, sqlite3.Error) as exc:
                # Never print incoming payloads, filenames, commands or raw errors.
                status = f'Token metadata unavailable ({type(exc).__name__}) · history retained'
        record = ledger.record(time.time(), status)
        publish(output, record)
        if args.hook:
            # An opt-in new row only. Never execute an existing custom row's
            # command, infer subscription quota, or interpolate payload strings.
            today = dt.datetime.now().strftime('%Y-%m-%d')
            value = str(record['todayTotalTokens']) if any(day['date'] == today for day in record['recentDays']) else '—'
            print('Grok · observed tokens ' + value)
    finally:
        ledger.db.close()


if __name__ == '__main__':
    main()
