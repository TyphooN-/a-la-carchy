"""Explicit sampled canonical counter history, not reconstructed request dates."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone as utc_timezone
import hashlib
import json
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MAX_SAFE = 9007199254740991
COUNTERS = ('input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_write_tokens',
            'reasoning_tokens', 'api_call_count', 'prompt_tokens', 'total_tokens')
GRAINS = ('day', 'week', 'month', 'year')
STATUSES = ('baseline', 'delta', 'measured_zero', 'reset', 'missing', 'reappeared', 'gap')
MAX_GAP_SECONDS = 6 * 60 * 60
# Finite linked capacity. Every canonical session/model/route identity keeps its
# own immutable ID, baseline, reset detection and coverage; nothing is collapsed
# per provider or profile.
# Retained identity inventory, not a lifetime union: present identities plus
# absent ones still referenced by a retained cell or event. Only when an
# observation would exceed it, absent identities holding no retained fact (every
# cell pruned by per-grain recorded-bucket retention, every event pruned from the
# bounded journal) retire: lifetime coverage folds into the private `retired`
# summary and a later reappearance is a new baseline. Anything still beyond the
# inventory is refused before any publication.
MAX_HISTORY_SOURCES = 512
MAX_BUCKETS = {'day': 31, 'week': 13, 'month': 12, 'year': 5}
# Dense retention: each retained identity may hold one cell, measured zero
# included, in every retained recorded bucket; only per-grain retention prunes.
MAX_CELLS = MAX_HISTORY_SOURCES * sum(MAX_BUCKETS.values())
MAX_EVENTS = 512
# Shared by the private state/target readers and exports (hermes_accounting
# MAX_OUTPUT) and the public collector/JS readers. At the caps above, a
# conservative compact-JSON envelope charging every counter 16 digits is
# 15,223,710 journal and 15,297,696 public-history bytes.
MAX_STATE_BYTES = 16 * 1024 * 1024
PROVIDERS = frozenset(('openai', 'openai-codex', 'anthropic', 'openrouter', 'google', 'gemini',
                       'groq', 'deepseek', 'nous', 'xai', 'mistral', 'cerebras', 'together', 'fireworks'))


def count(value):
    if type(value) is not int or not 0 <= value <= MAX_SAFE:
        raise ValueError('invalid history counter')
    return value


def zeros():
    return dict.fromkeys(COUNTERS, 0)


def add(left, right):
    return {field: count(left[field] + right[field]) for field in COUNTERS}


def instant(value):
    if not isinstance(value, str) or not re.fullmatch(
            r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|[+-]\d{2}:\d{2})', value):
        raise ValueError('explicit second-resolution offset timestamp required')
    if value.endswith('-00:00'):
        raise ValueError('unknown timestamp offset')
    if not value.endswith('Z') and (int(value[-5:-3]) > 23 or int(value[-2:]) > 59):
        raise ValueError('invalid timestamp offset')
    stamp = datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(utc_timezone.utc)
    if not 1970 <= stamp.year <= 9998:
        raise ValueError('observation timestamp out of range')
    return stamp


def calendar_zone(name):
    if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_+-]+(?:/[A-Za-z0-9_+-]+)*', name):
        raise ValueError('invalid IANA timezone')
    if name in ('localtime', 'posixrules') or name.startswith(('posix/', 'right/')):
        raise ValueError('host-derived or non-IANA timezone')
    try:
        return ZoneInfo(name)
    except (ValueError, ZoneInfoNotFoundError) as error:
        raise ValueError('unavailable IANA timezone') from error


def stamp(value):
    return value.isoformat().replace('+00:00', 'Z')


def bucket_keys(value, zone):
    local = value.astimezone(zone)
    monday = local.date() - timedelta(days=local.weekday())
    return {'day': local.strftime('%Y-%m-%d'), 'week': monday.isoformat(),
            'month': local.strftime('%Y-%m'), 'year': local.strftime('%Y')}


def empty_coverage():
    return {'events': dict.fromkeys(STATUSES, 0), 'observed': zeros(),
            'unallocated': {grain: zeros() for grain in GRAINS},
            'pruned': {grain: zeros() for grain in GRAINS}, 'prunedEvents': 0}


def exact_keys(value, expected):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ValueError('invalid history object shape')


def label(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', value):
        raise ValueError('invalid public profile')


def source_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{64}', value):
        raise ValueError('invalid sanitized source ID')


def validate_counts(value):
    exact_keys(value, COUNTERS)
    for number in value.values():
        count(number)
    if (value['reasoning_tokens'] > value['output_tokens'] or
            value['prompt_tokens'] != count(value['input_tokens'] + value['cache_read_tokens'] + value['cache_write_tokens']) or
            value['total_tokens'] != count(value['prompt_tokens'] + value['output_tokens'])):
        raise ValueError('invalid canonical count semantics')


def validate_source(value, profile, persisted=False):
    fields = {'id', 'provider', 'profile', 'attribution', 'counts'}
    optional = set()
    if persisted:
        fields.update(('present', 'firstObservedAt', 'lastObservedAt', 'baselineAt', 'coverage'))
    else:
        optional = {'firstSeen', 'lastSeen'}
    if not isinstance(value, dict) or not fields <= set(value) <= fields | optional:
        raise ValueError('invalid history object shape')
    for name in ('firstSeen', 'lastSeen'):
        if name not in value:
            continue
        stamp = value[name]
        if type(stamp) not in (int, float) or stamp != stamp or not 0 <= stamp <= 253402300799:
            raise ValueError('invalid usage timestamp')
    source_id(value['id'])
    if not isinstance(value['provider'], str) or value['provider'] not in PROVIDERS:
        raise ValueError('unsupported public provider')
    label(value['profile'])
    if value['profile'] != profile or value['attribution'] != 'unattributed':
        raise ValueError('invalid public provenance')
    validate_counts(value['counts'])


def accounting_record(sources, profile):
    totals = {}
    for source in sources:
        if source.get('present', True):
            provider = source['provider']
            totals[provider] = count(totals.get(provider, 0) + source['counts']['total_tokens'])
    count(sum(totals.values()))
    public = []
    for provider, total in sorted(totals.items()):
        identity = json.dumps(['hermes-token-accounting/1', profile, provider], separators=(',', ':'))
        public.append({'id': hashlib.sha256(identity.encode()).hexdigest(), 'provider': provider,
                       'profile': profile, 'attribution': 'unattributed',
                       'windows': {'today': None, 'week': None, 'history': total}})
    return {'id': 'hermes-token-accounting', 'contract': 'hermes-token-accounting/1',
            'schemaVersion': 1, 'readOnly': True, 'sources': public}


def validate_snapshot(snapshot):
    exact_keys(snapshot, ('profile', 'record', 'sources'))
    label(snapshot['profile'])
    sources = snapshot['sources']
    if not isinstance(sources, list) or len(sources) > MAX_HISTORY_SOURCES:
        raise ValueError('too many history sources')
    seen, totals = set(), zeros()
    for source in sources:
        validate_source(source, snapshot['profile'])
        if source['id'] in seen:
            raise ValueError('duplicate history source')
        seen.add(source['id'])
        totals = add(totals, source['counts'])
    if snapshot['record'] != accounting_record(sources, snapshot['profile']):
        raise ValueError('inconsistent cumulative accounting')


def aggregate_coverage(coverages):
    total = empty_coverage()
    for coverage in coverages:
        for status in STATUSES:
            total['events'][status] = count(total['events'][status] + coverage['events'][status])
        total['observed'] = add(total['observed'], coverage['observed'])
        total['prunedEvents'] = count(total['prunedEvents'] + coverage['prunedEvents'])
        for grain in GRAINS:
            for key in ('unallocated', 'pruned'):
                total[key][grain] = add(total[key][grain], coverage[key][grain])
    return total


def validate_coverage(coverage):
    exact_keys(coverage, ('events', 'observed', 'unallocated', 'pruned', 'prunedEvents'))
    exact_keys(coverage['events'], STATUSES)
    for value in coverage['events'].values():
        count(value)
    count(coverage['prunedEvents'])
    validate_counts(coverage['observed'])
    for key in ('unallocated', 'pruned'):
        exact_keys(coverage[key], GRAINS)
        for grain in GRAINS:
            validate_counts(coverage[key][grain])


def stored_instant(value):
    result = instant(value)
    if stamp(result) != value:
        raise ValueError('noncanonical persisted timestamp')
    return result


def bucket_start(key, grain):
    if not isinstance(key, str):
        raise ValueError('invalid bucket key')
    pattern = {'day': r'\d{4}-\d{2}-\d{2}', 'week': r'\d{4}-\d{2}-\d{2}',
               'month': r'\d{4}-\d{2}', 'year': r'\d{4}'}[grain]
    if not re.fullmatch(pattern, key):
        raise ValueError('invalid bucket key')
    value = key + ('-01-01' if grain == 'year' else '-01' if grain == 'month' else '')
    start = datetime.fromisoformat(value)
    if not 1969 <= start.year <= 9998 or (grain == 'week' and start.weekday() != 0):
        raise ValueError('invalid calendar bucket')
    return start


def bucket_bounds(key, grain, zone):
    beginning = bucket_start(key, grain)
    if grain in ('day', 'week'):
        ending = beginning + timedelta(days=1 if grain == 'day' else 7)
    elif grain == 'month':
        ending = beginning.replace(year=beginning.year + (beginning.month == 12), month=beginning.month % 12 + 1)
    else:
        ending = beginning.replace(year=beginning.year + 1)
    return (beginning.replace(tzinfo=zone, fold=0).astimezone(utc_timezone.utc),
            ending.replace(tzinfo=zone, fold=0).astimezone(utc_timezone.utc))


def allocated_grains(left, right, zone, status):
    if status == 'gap':
        return []
    keys_left, keys_right = bucket_keys(left, zone), bucket_keys(right, zone)
    allocated = []
    for grain in GRAINS:
        key = keys_left[grain]
        if key != keys_right[grain]:
            continue
        beginning, ending = bucket_bounds(key, grain, zone)
        # Earliest local midnight boundaries conservatively exclude a hidden
        # midnight rollback excursion past the bucket's end, even when endpoint
        # labels coincide. A rollback across the bucket's own opening midnight
        # repeats it instead: after its first occurrence the clock drops back
        # into the previous bucket and re-enters only at the second (fold=1)
        # occurrence, e.g. America/St_Johns 2009-11-01 00:00:59 NDT ->
        # 2009-10-31 23:01 NST -> 2009-11-01 00:00 NST. An interval straddling
        # that re-entry hides an excursion and is never split, so it stays
        # unallocated. A repeat that never leaves the bucket (America/Havana
        # 2025-11-02 00:59:59 CDT -> 00:00 CST) is not an excursion. Instants
        # and zone transitions are whole seconds, so one second before the
        # re-entry lies inside any excursion.
        reentry = bucket_start(key, grain).replace(tzinfo=zone, fold=1).astimezone(utc_timezone.utc)
        hidden = beginning < reentry and bucket_keys(reentry - timedelta(seconds=1), zone)[grain] != key
        if beginning <= left <= right < ending and not (hidden and left < reentry <= right):
            allocated.append(grain)
    return allocated


def validate_state(state):
    """Strict, bounded persisted-state admission before any overwrite/projection."""
    fields = {'contract', 'schemaVersion', 'profile', 'timezone', 'startedAt', 'observedAt',
              'snapshotDigest', 'generation', 'sources', 'buckets', 'events', 'coverage'}
    # `retired` is absent from journals that never retired an identity, including
    # every journal written before retirement existed.
    for optional in ('transaction', 'retired'):
        if isinstance(state, dict) and optional in state:
            fields.add(optional)
    exact_keys(state, fields)
    if state['contract'] != 'hermes-observed-state/1' or type(state['schemaVersion']) is not int or state['schemaVersion'] != 1:
        raise ValueError('unsupported history state')
    label(state['profile'])
    zone = calendar_zone(state['timezone'])
    start, end = stored_instant(state['startedAt']), stored_instant(state['observedAt'])
    if start > end or not count(state['generation']):
        raise ValueError('invalid recording range')
    source_id(state['snapshotDigest'])
    sources = state['sources']
    if not isinstance(sources, dict) or len(sources) > MAX_HISTORY_SOURCES:
        raise ValueError('too many persisted sources')
    for identity, source in sources.items():
        validate_source(source, state['profile'], persisted=True)
        if identity != source['id'] or type(source['present']) is not bool:
            raise ValueError('invalid source state')
        first, last, baseline = (stored_instant(source[key]) for key in ('firstObservedAt', 'lastObservedAt', 'baselineAt'))
        if not start <= first <= baseline <= last <= end or (source['present'] and last != end):
            raise ValueError('invalid source recording dates')
        validate_coverage(source['coverage'])
    # Strip optional timestamp keys before digesting so the snapshot digest
    # remains stable across observations that differ only in firstSeen/lastSeen.
    def _stable(source):
        return {field: source[field] for field in ('id', 'provider', 'profile', 'attribution', 'counts')}
    snapshot_sources = [_stable(source)
                        for _, source in sorted(sources.items()) if source['present']]
    snapshot = {'profile': state['profile'], 'record': accounting_record(snapshot_sources, state['profile']),
                'sources': snapshot_sources}
    if digest(snapshot) != state['snapshotDigest']:
        raise ValueError('persisted snapshot counters or provenance changed')
    lifetime = [source['coverage'] for source in sources.values()]
    if 'retired' in state:
        retired = state['retired']
        exact_keys(retired, ('sources', 'coverage'))
        identities, coverage = count(retired['sources']), retired['coverage']
        validate_coverage(coverage)
        # Every retired identity was baselined and went missing, and none keeps a
        # retained cell or event: all its events and measured amounts are pruned
        # or unallocated.
        if (not identities or min(coverage['events']['baseline'], coverage['events']['missing']) < identities or
                sum(coverage['events'].values()) != coverage['prunedEvents'] or
                any(add(coverage['pruned'][grain], coverage['unallocated'][grain]) != coverage['observed']
                    for grain in GRAINS)):
            raise ValueError('invalid retired identity coverage')
        lifetime.append(coverage)
    validate_coverage(state['coverage'])
    if state['coverage'] != aggregate_coverage(lifetime):
        raise ValueError('inconsistent source coverage')
    exact_keys(state['buckets'], GRAINS)
    retained = {identity: {grain: zeros() for grain in GRAINS} for identity in sources}
    cells = 0
    for grain, buckets in state['buckets'].items():
        if not isinstance(buckets, list) or len(buckets) > MAX_BUCKETS[grain]:
            raise ValueError('too many retained buckets')
        previous = ''
        for bucket in buckets:
            exact_keys(bucket, ('key', 'sources'))
            beginning, ending = bucket_bounds(bucket['key'], grain, zone)
            if not previous < bucket['key'] or beginning > end or ending <= start:
                raise ValueError('duplicate or unordered buckets')
            previous = bucket['key']
            values = bucket['sources']
            if not isinstance(values, dict) or not values or not set(values) <= set(sources):
                raise ValueError('unknown bucket sources')
            duration = int((ending - beginning).total_seconds())
            for identity, cell in values.items():
                exact_keys(cell, ('counts', 'coveredSeconds', 'intervals'))
                validate_counts(cell['counts'])
                if not 0 < count(cell['intervals']) <= count(cell['coveredSeconds']) <= duration:
                    raise ValueError('invalid sampled coverage')
                retained[identity][grain] = add(retained[identity][grain], cell['counts'])
                cells += 1
    if cells > MAX_CELLS:
        raise ValueError('too many retained cells')
    for identity, source in sources.items():
        coverage = source['coverage']
        for grain in GRAINS:
            if add(add(retained[identity][grain], coverage['pruned'][grain]), coverage['unallocated'][grain]) != coverage['observed']:
                raise ValueError('inconsistent allocated coverage')
    events = state['events']
    if not isinstance(events, list) or len(events) > MAX_EVENTS:
        raise ValueError('too many retained intervals')
    previous = start
    event_coverage = {identity: empty_coverage() for identity in sources}
    last_event = {}
    for event in events:
        exact_keys(event, ('sourceId', 'status', 'start', 'end', 'counts', 'allocated'))
        if not isinstance(event['sourceId'], str) or event['sourceId'] not in sources or event['status'] not in STATUSES:
            raise ValueError('invalid observation provenance')
        left, right = stored_instant(event['start']), stored_instant(event['end'])
        if not start <= left <= right <= end or right < previous:
            raise ValueError('invalid observation dates')
        previous = right
        if left < last_event.get(event['sourceId'], start):
            raise ValueError('overlapping source observations')
        last_event[event['sourceId']] = right
        allocated = event['allocated']
        if not isinstance(allocated, list) or len(allocated) > 4 or any(grain not in GRAINS for grain in allocated) or len(set(allocated)) != len(allocated):
            raise ValueError('invalid allocation grains')
        if event['status'] in ('delta', 'measured_zero', 'gap'):
            validate_counts(event['counts'])
            seconds = int((right - left).total_seconds())
            if seconds <= 0 or (event['status'] == 'gap') != (seconds > MAX_GAP_SECONDS):
                raise ValueError('invalid gap classification')
            if event['status'] != 'gap' and (event['status'] == 'measured_zero') != (event['counts']['total_tokens'] == 0):
                raise ValueError('invalid measured-zero classification')
            expected = allocated_grains(left, right, zone, event['status'])
            if allocated != expected:
                raise ValueError('invalid cross-calendar allocation')
        elif event['counts'] is not None or allocated:
            raise ValueError('unknown interval cannot have counted tokens')
        coverage = event_coverage[event['sourceId']]
        coverage['events'][event['status']] = count(coverage['events'][event['status']] + 1)
        if event['counts'] is not None:
            coverage['observed'] = add(coverage['observed'], event['counts'])
            for grain in GRAINS:
                if grain not in allocated:
                    coverage['unallocated'][grain] = add(coverage['unallocated'][grain], event['counts'])
    for identity, coverage in event_coverage.items():
        total = sources[identity]['coverage']
        if sum(total['events'].values()) - sum(coverage['events'].values()) != total['prunedEvents']:
            raise ValueError('inconsistent pruned observation coverage')
        vectors = [(coverage['events'], total['events']), (coverage['observed'], total['observed'])]
        vectors.extend((coverage['unallocated'][grain], total['unallocated'][grain]) for grain in GRAINS)
        for remaining, lifetime in vectors:
            if any(remaining[key] > lifetime[key] for key in remaining) or (not total['prunedEvents'] and remaining != lifetime):
                raise ValueError('retained observation counts exceed coverage')
    if 'transaction' in state:
        transaction = state['transaction']
        exact_keys(transaction, ('generation', 'targets'))
        if count(transaction['generation']) != state['generation']:
            raise ValueError('transaction generation differs')
        exact_keys(transaction['targets'], ('accounting', 'history'))
        for target in transaction['targets'].values():
            exact_keys(target, ('path', 'preimage', 'payload'))
            path = target['path']
            if not isinstance(path, str) or not 1 <= len(path) <= 4096 or not path.startswith('/') or any(ord(c) < 32 for c in path):
                raise ValueError('invalid private transaction target')
            if target['preimage'] is not None:
                source_id(target['preimage'])
            source_id(target['payload'])
    if len(json.dumps(state, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()) > MAX_STATE_BYTES:
        raise ValueError('history state byte limit')
    return state


def prune(state):
    """Per-grain recorded-bucket retention and the bounded journal only.

    The retained inventory bounds cells at MAX_CELLS, so no bucket inside its
    grain's retention is ever evicted to make room for another grain."""
    def drop(grain, bucket):
        for identity, cell in bucket['sources'].items():
            for coverage in (state['coverage'], state['sources'][identity]['coverage']):
                coverage['pruned'][grain] = add(coverage['pruned'][grain], cell['counts'])
    for grain in GRAINS:
        buckets = state['buckets'][grain]
        while len(buckets) > MAX_BUCKETS[grain]:
            drop(grain, buckets.pop(0))
    removed = max(0, len(state['events']) - MAX_EVENTS)
    for event in state['events'][:removed]:
        source = state['sources'][event['sourceId']]
        for coverage in (state['coverage'], source['coverage']):
            coverage['prunedEvents'] = count(coverage['prunedEvents'] + 1)
    del state['events'][:removed]


def retire(state):
    """Retire absent identities proven outside retention: no retained cell or event."""
    referenced = {identity for buckets in state['buckets'].values() for bucket in buckets for identity in bucket['sources']}
    referenced.update(event['sourceId'] for event in state['events'])
    idle = sorted(identity for identity, source in state['sources'].items()
                  if not source['present'] and identity not in referenced)
    if idle:
        retired = state.setdefault('retired', {'sources': 0, 'coverage': empty_coverage()})
        for identity in idle:
            retired['coverage'] = aggregate_coverage((retired['coverage'], state['sources'].pop(identity)['coverage']))
        retired['sources'] = count(retired['sources'] + len(idle))


def digest(snapshot):
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def record_event(state, source, status, start, end, delta=None, allocated=()):
    state['events'].append({'sourceId': source['id'], 'status': status,
                            'start': start, 'end': end, 'counts': deepcopy(delta), 'allocated': list(allocated)})
    for coverage in (state['coverage'], source['coverage']):
        coverage['events'][status] = count(coverage['events'][status] + 1)
        if delta is not None:
            coverage['observed'] = add(coverage['observed'], delta)
            for grain in GRAINS:
                if grain not in allocated:
                    coverage['unallocated'][grain] = add(coverage['unallocated'][grain], delta)


def observe(state, snapshot, *, observed_at, timezone):
    """Pure transform. Repeated identical instants are idempotent, not new zeroes."""
    validate_snapshot(snapshot)
    if state is not None:
        validate_state(state)
    now, zone = instant(observed_at), calendar_zone(timezone)
    # Strip optional timestamps and canonicalize source order before digesting,
    # matching validate_state's stable projection without changing source IDs.
    stable_snapshot = dict(snapshot)
    stable_snapshot['sources'] = [
        {field: source[field] for field in ('id', 'provider', 'profile', 'attribution', 'counts')}
        for source in sorted(snapshot['sources'], key=lambda source: source['id'])
    ]
    timestamp, snapshot_digest = stamp(now), digest(stable_snapshot)
    if state is None:
        state = {'contract': 'hermes-observed-state/1', 'schemaVersion': 1,
                 'profile': snapshot['profile'], 'timezone': timezone,
                 'startedAt': timestamp, 'observedAt': timestamp, 'snapshotDigest': snapshot_digest,
                 'generation': 0, 'sources': {}, 'buckets': {grain: [] for grain in GRAINS},
                 'events': [], 'coverage': empty_coverage()}
    else:
        if timezone != state['timezone'] or snapshot['profile'] != state['profile']:
            raise ValueError('history timezone and profile are immutable')
        before = instant(state['observedAt'])
        if now < before or (now == before and snapshot_digest != state['snapshotDigest']):
            raise ValueError('backward or conflicting repeated observation')
        if now == before:
            return deepcopy(state)
    updated = deepcopy(state)
    updated.pop('transaction', None)
    before = instant(state['observedAt'])
    seconds = int((now - before).total_seconds())
    end_keys = bucket_keys(now, zone)
    present = {source['id'] for source in snapshot['sources']}
    for source_id, source in updated['sources'].items():
        if source['present'] and source_id not in present:
            source['present'] = False
            record_event(updated, source, 'missing', stamp(before), timestamp)
    for current in snapshot['sources']:
        source_id = current['id']
        old = state['sources'].get(source_id)
        current = {key: value for key, value in current.items() if key not in ('firstSeen', 'lastSeen')}
        source = deepcopy(old) if old else dict(deepcopy(current), firstObservedAt=timestamp,
                                                baselineAt=timestamp, coverage=empty_coverage())
        source.update(counts=deepcopy(current['counts']), present=True, lastObservedAt=timestamp)
        updated['sources'][source_id] = source
        # Same-day dating is disabled: allocating cumulative counters to
        # dated buckets in a fresh state causes coverage inconsistencies.
        # All first-observed sources baseline; the calendar shows correct
        # data from the next observation onward when deltas are measured.
        if old is None or not old['present']:
            source['baselineAt'] = timestamp
            record_event(updated, source, 'baseline' if old is None else 'reappeared',
                         timestamp, timestamp)
            continue
        differences = {field: current['counts'][field] - old['counts'][field] for field in COUNTERS}
        if any(value < 0 for value in differences.values()) or differences['reasoning_tokens'] > differences['output_tokens']:
            source['baselineAt'] = timestamp
            record_event(updated, source, 'reset', stamp(before), timestamp)
            continue
        delta = {field: count(value) for field, value in differences.items()}
        status = ('gap' if seconds > MAX_GAP_SECONDS else
                  'measured_zero' if delta['total_tokens'] == 0 else 'delta')
        allocated = allocated_grains(before, now, zone, status)
        record_event(updated, source, status, stamp(before), timestamp, delta, allocated)
        for grain in allocated:
            buckets = updated['buckets'][grain]
            bucket = next((item for item in buckets if item['key'] == end_keys[grain]), None)
            if bucket is None:
                bucket = {'key': end_keys[grain], 'sources': {}}
                buckets.append(bucket)
                buckets.sort(key=lambda item: item['key'])
            cell = bucket['sources'].setdefault(source_id, {'counts': zeros(), 'coveredSeconds': 0, 'intervals': 0})
            cell['counts'] = add(cell['counts'], delta)
            cell['coveredSeconds'] = count(cell['coveredSeconds'] + seconds)
            cell['intervals'] = count(cell['intervals'] + 1)
    updated['observedAt'] = timestamp
    updated['snapshotDigest'] = snapshot_digest
    updated['generation'] = count(updated['generation'] + 1)
    prune(updated)
    if len(updated['sources']) > MAX_HISTORY_SOURCES:
        retire(updated)
        if len(updated['sources']) > MAX_HISTORY_SOURCES:
            raise ValueError('retained history source capacity reached')
    return validate_state(updated)


def public_coverage(coverage, has_sources):
    coverage = deepcopy(coverage)
    measured = sum(coverage['events'][key] for key in ('delta', 'measured_zero', 'gap'))
    coverage.update(status='partial-observed' if measured else 'baseline-only' if has_sources else 'unknown',
                    missingHistory=True, interpretation='sampled-counter-deltas-not-requests')
    return coverage


def project_history(state, *, providers=None, profiles=None, source_ids=None, grain=None):
    """Detached filtered view, never a mutation, backfill, or missing-bucket zero."""
    validate_state(state)
    for values, validator in ((providers, lambda item: isinstance(item, str) and item in PROVIDERS),
                              (profiles, lambda item: isinstance(item, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', item)),
                              (source_ids, lambda item: isinstance(item, str) and re.fullmatch(r'[a-f0-9]{64}', item))):
        if values is not None and (not isinstance(values, (list, tuple)) or len(values) > MAX_HISTORY_SOURCES or
                                   any(not validator(item) for item in values) or len(set(values)) != len(values)):
            raise ValueError('invalid history filter')
    if grain is not None and grain not in GRAINS:
        raise ValueError('invalid calendar grain')
    selected = {identity: source for identity, source in state['sources'].items()
                if (providers is None or source['provider'] in providers) and
                   (profiles is None or source['profile'] in profiles) and
                   (source_ids is None or identity in source_ids)}
    buckets = {}
    for selected_grain in (GRAINS if grain is None else (grain,)):
        buckets[selected_grain] = []
        for item in state['buckets'][selected_grain]:
            cells = [dict(deepcopy(cell), id=identity) for identity, cell in sorted(item['sources'].items()) if identity in selected]
            if cells:
                buckets[selected_grain].append({'key': item['key'], 'sources': cells})
    sources = []
    for _, source in sorted(selected.items()):
        public = deepcopy(source)
        public['coverage'] = public_coverage(source['coverage'], True)
        sources.append(public)
    return {'contract': 'hermes-observed-history/1', 'schemaVersion': 1, 'readOnly': True,
            'timezone': state['timezone'], 'startedAt': state['startedAt'], 'observedAt': state['observedAt'],
            'generation': state['generation'], 'sources': sources, 'buckets': buckets,
            'events': [deepcopy(event) for event in state['events'] if event['sourceId'] in selected],
            'coverage': public_coverage(aggregate_coverage(source['coverage'] for source in selected.values()), bool(selected))}
