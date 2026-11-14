#!/usr/bin/env python3
"""Real offline calendar-clock CLI; no credential, ledger or network inputs."""
import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / 'extras/local-router-stats/calendar_clock.py'


def tick(stamp):
    return dt.datetime.fromisoformat(stamp.replace('Z', '+00:00')).timestamp()


class CalendarClockTests(unittest.TestCase):
    def run_clock(self, zone, at, *, local_zone='UTC'):
        env = dict(os.environ, TZ=local_zone)
        child = subprocess.run([sys.executable, '-I', '-B', str(HELPER),
                                '--timezone', zone, '--now', str(at)],
                               env=env, capture_output=True, text=True, timeout=5)
        self.assertEqual(child.returncode, 0, child.stderr or child.stdout)
        return json.loads(child.stdout)

    def test_utc_clock_is_bounded_and_matches_the_consumer_contract(self):
        clock = self.run_clock('UTC', tick('2024-02-29T15:05:00Z') + 0.125)
        self.assertEqual(set(clock), {'state', 'timezone', 'observedAt', 'expiresAt',
                                      'keys', 'localCalendarAligned'})
        self.assertEqual(clock['state'], 'ready')
        self.assertEqual(clock['timezone'], 'UTC')
        self.assertEqual(clock['observedAt'], '2024-02-29T15:05:00Z')
        self.assertEqual(clock['expiresAt'], '2024-02-29T15:05:45Z')
        self.assertEqual(clock['keys'], {'day': '2024-02-29', 'week': '2024-02-26',
                                        'month': '2024-02', 'year': '2024'})
        self.assertIs(clock['localCalendarAligned'], True)
    def test_local_alignment_uses_proven_zone_identity_not_a_guess(self):
        at = tick('2024-02-29T15:05:00Z')
        self.assertIs(self.run_clock('UTC', at, local_zone='Etc/UTC')['localCalendarAligned'], True)
        self.assertIs(self.run_clock('America/New_York', at, local_zone='America/New_York')['localCalendarAligned'], True)
        self.assertIs(self.run_clock('America/New_York', at, local_zone='UTC')['localCalendarAligned'], False)
        self.assertIs(self.run_clock('UTC', at, local_zone='UNRESOLVED_LOCAL_CLOCK')['localCalendarAligned'], False)

    def test_clock_expires_before_any_local_date_rollover(self):
        cases = [('UTC', '2024-02-29T23:59:50Z', '2024-02-29', '2024-03-01T00:00:00Z'),
                 ('America/New_York', '2024-03-10T04:59:50Z', '2024-03-09', '2024-03-10T05:00:00Z'),
                 ('America/New_York', '2024-11-03T03:59:50Z', '2024-11-02', '2024-11-03T04:00:00Z'),
                 ('America/New_York', '2025-01-01T04:59:50Z', '2024-12-31', '2025-01-01T05:00:00Z'),
                 ('Pacific/Apia', '2011-12-30T09:59:50Z', '2011-12-29', '2011-12-30T10:00:00Z')]
        for zone, now, day, expires in cases:
            with self.subTest(zone=zone, now=now):
                clock = self.run_clock(zone, tick(now))
                self.assertEqual(clock['keys']['day'], day)
                self.assertEqual(clock['expiresAt'], expires)
                self.assertGreater(tick(expires), tick(clock['observedAt']))
                self.assertLessEqual(tick(expires) - tick(clock['observedAt']), 45)
    def test_midnight_rollback_expires_at_the_first_actual_date_change(self):
        before = self.run_clock('America/St_Johns', tick('2009-11-01T02:30:30Z'))
        last = self.run_clock('America/St_Johns', tick('2009-11-01T02:30:59Z'))
        after = self.run_clock('America/St_Johns', tick('2009-11-01T02:31:00Z'))
        self.assertEqual(before['keys']['day'], '2009-11-01')
        self.assertEqual(last['keys']['day'], '2009-11-01')
        self.assertEqual(after['keys']['day'], '2009-10-31')
        self.assertEqual(after['keys']['month'], '2009-10')
        self.assertEqual(before['expiresAt'], '2009-11-01T02:31:00Z')
        self.assertEqual(last['expiresAt'], '2009-11-01T02:31:00Z')
        self.assertEqual(after['expiresAt'], '2009-11-01T02:31:45Z')
        source = (HELPER.parent / 'history_consumer.js').read_text() + '''
const p=JSON.parse(require('fs').readFileSync(0,'utf8'));
const old=selectDatedHistory(null,null,p.before,{},Date.parse(p.before.observedAt));
const stale=selectDatedHistory(null,null,p.before,{},Date.parse(p.after.observedAt));
const renewed=selectDatedHistory(null,null,p.after,{},Date.parse(p.after.observedAt));
console.log(JSON.stringify({old:old,stale:stale,renewed:renewed}));
'''
        child = subprocess.run(['node', '-e', source],
                               input=json.dumps({'before': before, 'after': after}),
                               capture_output=True, text=True, timeout=5)
        self.assertEqual(child.returncode, 0, child.stderr)
        result = json.loads(child.stdout)
        self.assertEqual(result['old']['periods']['day']['key'], '2009-11-01')
        self.assertTrue(result['stale']['selectionError'])
        self.assertEqual(result['stale']['bars'], [])
        self.assertEqual(result['renewed']['periods']['day']['key'], '2009-10-31')
        self.assertEqual(result['renewed']['periods']['month']['key'], '2009-10')

    def test_invalid_zones_and_ticks_fail_closed_without_error_payloads(self):
        for zone, at in [('../UTC', 1), ('right/UTC', 1), ('posix/UTC', 1),
                         ('localtime', 1), ('posixrules', 1), ('No/Such_Zone', 1),
                         ('UTC', 'nan'), ('UTC', 'inf'), ('UTC', '-inf'),
                         ('UTC', -1), ('UTC', 1e100)]:
            with self.subTest(zone=zone, at=at):
                child = subprocess.run([sys.executable, '-I', '-B', str(HELPER),
                                        '--timezone', zone, '--now=' + str(at)],
                                       env=dict(os.environ, TZ='UTC'), capture_output=True,
                                       text=True, timeout=5)
                self.assertEqual(child.returncode, 2)
                self.assertEqual(json.loads(child.stdout), {'state': 'unavailable'})
                self.assertEqual(child.stderr, '')

    def test_real_clock_payload_drives_the_shared_consumer_and_expires(self):
        clock = self.run_clock('UTC', tick('2024-02-29T23:59:50Z'))
        next_clock = self.run_clock('UTC', tick('2024-03-01T00:00:00Z'))
        source = (HELPER.parent / 'history_consumer.js').read_text() + '''
const p=JSON.parse(require('fs').readFileSync(0,'utf8'));
const local={id:'local',readOnly:true,hasLocalStats:true,
  recentDays:[{date:'2024-02-29',messageCount:9,recorded:true}]};
const first=selectDatedHistory(null,local,p.clock,{},Date.parse(p.clock.observedAt));
const stale=selectDatedHistory(null,local,p.clock,{},Date.parse(p.clock.expiresAt));
const next=selectDatedHistory(null,local,p.next,{},Date.parse(p.next.observedAt));
console.log(JSON.stringify({first:first,stale:stale,next:next}));
'''
        child = subprocess.run(['node', '-e', source], input=json.dumps({'clock': clock, 'next': next_clock}),
                               capture_output=True, text=True, timeout=5)
        self.assertEqual(child.returncode, 0, child.stderr)
        result = json.loads(child.stdout)
        self.assertEqual(result['first']['periods']['day']['value'], 9)
        self.assertEqual(result['first']['bars'][-1], result['first']['periods']['day'])
        self.assertTrue(result['stale']['selectionError'])
        self.assertEqual(result['stale']['bars'], [])
        self.assertIsNone(result['next']['periods']['day']['value'])
        self.assertEqual(result['next']['periods']['week']['value'], 9)
        self.assertIsNone(result['next']['periods']['month']['value'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
