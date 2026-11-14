#!/usr/bin/env python3
"""Offline passive Codex quota calibration; reads only explicit sanitized publications.

No quota requests, credentials, database, Hermes runtime imports or model calls.
"""
import argparse
import copy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re
import sys

CONTRACT = 'hermes-quota-calibration/1'
STATE_CONTRACT = 'hermes-quota-calibration-state/3'
PREVIOUS_STATE_CONTRACT = 'hermes-quota-calibration-state/2'
LEGACY_STATE_CONTRACT = 'hermes-quota-calibration-state/1'
ATTRIBUTION = 'Quota-movement correlation only; Hermes counters have no credential identity. External use and workload changes can bias estimates.'
MAX_ACCOUNTS = 256
MAX_SOURCES = 512
MAX_SAMPLES = 24
MAX_BYTES = 1024 * 1024
FRESH_SECONDS = 120
MAX_GAP = 7200
MIN_INTERVAL = 300
MIN_SAMPLES = 3
MIN_COVERAGE = 1800
MIN_POINTS = 1.0
MAX_POINTS = 25.0
MAX_RATE = 1_000_000_000
MAX_SAFE = 9007199254740991
PUBLIC_INVENTORY = '.hermes-credential-inventory'
PUBLIC_MANIFEST = '.hermes-codex-publication'


def sibling(name):
    spec = importlib.util.spec_from_file_location('owned_quota_'+name, Path(__file__).with_name(name+'.py'))
    if spec is None or spec.loader is None:
        raise ValueError('missing sibling')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def number(value, low=0, high=MAX_SAFE):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError('invalid finite number')
    return value


def hex_id(value):
    if not isinstance(value, str) or re.fullmatch('[a-f0-9]{64}', value) is None:
        raise ValueError('invalid public identity')
    return value


def instant(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 35:
        raise ValueError('invalid instant')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('offset required')
    return number(parsed.timestamp(), 1, 4102444800)


def stamp(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def read_record(adapter, path, limit):
    before = adapter.fingerprint(path)
    if before is None:
        raise ValueError('missing sanitized publication')
    raw = adapter.read_bytes(path, limit=limit)
    if adapter.fingerprint(path) != before or hashlib.sha256(raw).hexdigest() != before['sha256']:
        raise ValueError('publication changed')
    value = json.loads(raw, object_pairs_hook=adapter.unique_object)
    if not isinstance(value, dict):
        raise ValueError('object required')
    return value, before


def quota_snapshot(adapter, directory, now):
    directory = Path(directory)
    inventory, inv_pre = read_record(adapter, directory/PUBLIC_INVENTORY, 128*1024)
    manifest, man_pre = read_record(adapter, directory/PUBLIC_MANIFEST, 128*1024)
    entries = inventory.get('entries')
    if (set(inventory) != {'schemaVersion','contract','generation','entries'} or
            type(inventory['schemaVersion']) is not int or inventory['schemaVersion'] != 1 or
            inventory['contract'] != 'hermes-credential-inventory/1' or
            not isinstance(entries, list) or len(entries) > MAX_ACCOUNTS or
            hex_id(inventory['generation']) != digest(entries)):
        raise ValueError('invalid public inventory')
    if set(manifest) != {'version','files'} or type(manifest['version']) is not int or manifest['version'] != 1:
        raise ValueError('invalid public manifest')
    files = manifest['files']
    expected = {PUBLIC_INVENTORY}
    accounts, seen, previous = {}, set(), ''
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {'id','provider','principalId','name','quota'}:
            raise ValueError('invalid inventory entry')
        principal = hex_id(entry['principalId'])
        provider = entry['provider']
        if not isinstance(provider, str) or not re.fullmatch('[a-z0-9-]{1,64}', provider):
            raise ValueError('invalid public provider')
        codex = provider == 'openai-codex'
        ident = ('codex-hermes-' if codex else 'hermes-credential-') + principal
        if (entry['id'] != ident or ident <= previous or principal in seen or
                entry['quota'] != ('codex' if codex else 'unavailable')):
            raise ValueError('duplicate or mismatched inventory')
        previous = ident; seen.add(principal); expected.add(ident+'.json')
        if not codex:
            continue
        if entry['name'] != 'Codex (Hermes '+principal[:8]+')':
            raise ValueError('invalid public name')
        record, preimage = read_record(adapter, directory/(ident+'.json'), 65536)
        if not isinstance(files, dict) or files.get(ident+'.json') != preimage['sha256']:
            raise ValueError('uncommitted quota publication')
        if (type(record.get('schemaVersion')) is not int or record['schemaVersion'] != 1 or
                any(record.get(k) != v for k,v in dict(id=ident, principalId=principal,
                    name=entry['name'], credentialProvider=provider, inventoryGeneration=inventory['generation']).items()) or
                record.get('readOnly') is not True or record.get('ready') is not True or
                record.get('hasLocalStats') is not False or record.get('hasPromptStats') is not False or
                record.get('limitsStale') is not False or record.get('usageStatusText') != '' or
                record.get('authHelpText') != '' or
                record.get('tierLabel') not in ('free','plus','pro','team','business','enterprise','edu','Hermes')):
            raise ValueError('quota unavailable or identity mismatch')
        fetched = number(record.get('limitsFetchedAt'), 1, 4102444800000)/1000
        if not 0 <= now-fetched <= FRESH_SECONDS:
            raise ValueError('quota stale or future')
        limits = record.get('limits')
        if not isinstance(limits, list) or not 1 <= len(limits) <= 2:
            raise ValueError('missing quota windows')
        weekly = []
        labels = set()
        for window in limits:
            if not isinstance(window, dict) or set(window) != {'label','title','percent','resetsAt'}:
                raise ValueError('invalid quota window')
            match = re.fullmatch(r'([0-9]{1,8})-(day|hour|minute|second) window', str(window['label']))
            seconds = int(match[1])*{'day':86400,'hour':3600,'minute':60,'second':1}[match[2]] if match else 0
            if not 1 <= seconds <= 31622400 or seconds in labels:
                raise ValueError('invalid window units')
            labels.add(seconds)
            used = number(window['percent'],0,1)  # Producer used_percent / 100, NOT remaining.
            reset = instant(window['resetsAt'])
            if not 0 < reset-now <= seconds:
                raise ValueError('expired or invalid window')
            if seconds == 604800:
                weekly.append(dict(used=used, reset=reset, fetched=fetched, tier=record['tierLabel']))
        if len(weekly) != 1:
            raise ValueError('weekly quota required')
        accounts[principal] = weekly[0]
    if (not isinstance(files, dict) or set(files) != expected or
            any(hex_id(v) != v for v in files.values()) or files[PUBLIC_INVENTORY] != inv_pre['sha256'] or
            adapter.fingerprint(directory/PUBLIC_INVENTORY) != inv_pre or
            adapter.fingerprint(directory/PUBLIC_MANIFEST) != man_pre):
        raise ValueError('publication membership changed')
    return inventory['generation'], accounts


def canonical_snapshot(adapter, path, profile, now):
    record, _ = read_record(adapter, path, adapter.MAX_OUTPUT)
    if (record.get('contract') != 'hermes-session-model-usage/1' or
            record.get('counterSemantics') != 'hermes-canonical/1' or record.get('feed') != 'hermes-canonical-feed/1'):
        raise ValueError('canonical feed association required')
    binding = digest([hex_id(record.get('sourceBinding')), hex_id(record.get('configBinding')), profile])
    at = instant(record.get('observedAt'))
    if not 0 <= now-at <= FRESH_SECONDS:
        raise ValueError('canonical feed stale or future')
    # Reuse maintained canonical semantics; never reinterpret provider token buckets.
    snapshot = adapter.canonical_snapshot(record.get('rows'), profile)
    counters = {s['id']:s['counts'] for s in snapshot['sources'] if s['provider'] == 'openai-codex'}
    return binding, at, counters


def baseline(binding, at, counters, generation, accounts):
    return dict(binding=binding, at=at, counters=counters, inventory=generation, accounts=accounts)


def estimate(samples, quota):
    if len(samples) < MIN_SAMPLES or sum(s['end']-s['start'] for s in samples) < MIN_COVERAGE:
        return None
    rates = [s['tokens']/s['points'] for s in samples]
    if max(rates) > 2*min(rates):
        return None
    rate = sum(s['tokens'] for s in samples)/sum(s['points'] for s in samples)
    return dict(tokensPerQuotaPoint=round(rate,2), usedWeeklyTokens=round(rate*quota['used']*100),
                observedRange=[round(min(rates),2),round(max(rates),2)],
                observedSeconds=sum(s['end']-s['start'] for s in samples),
                observedFrom=samples[0]['start'], observedTo=samples[-1]['end'])


def project(state):
    anchor = state['anchor']
    until = min([anchor['at']+FRESH_SECONDS] +
                [min(q['fetched']+FRESH_SECONDS,q['reset']) for q in anchor['accounts'].values()])
    accounts = []
    for principal, quota in sorted(anchor['accounts'].items()):
        samples = state['samples'][principal]
        value = estimate(samples,quota) if state['status'][principal] == 'usable' else None
        status = 'estimated' if value is not None else ('ambiguous' if state['status'][principal] == 'ambiguous' else 'calibrating')
        accounts.append(dict(principalId=principal, status=status, sampleCount=len(samples),
                             estimate=value, reason=state['status'][principal], quotaUsed=quota['used'],
                             quotaObservedAt=quota['fetched'], resetsAt=quota['reset']))
    return dict(contract=CONTRACT, schemaVersion=1, readOnly=True, attribution=ATTRIBUTION,
                inventoryGeneration=anchor['inventory'],
                observedAt=anchor['at'], validUntil=until, accounts=accounts,
                limits=dict(minSamples=MIN_SAMPLES,minIntervalSeconds=MIN_INTERVAL,maxGapSeconds=MAX_GAP,
                            minQuotaPoints=MIN_POINTS,maxQuotaPoints=MAX_POINTS,maxSamples=MAX_SAMPLES,
                            maxRateRatio=2,sampleMaxAgeSeconds=86400))


def segment_baseline(anchor):
    # Keep only the learning start, not a duplicate of the canonical ledger.
    return dict(at=anchor['at'], tokens=0,
                accounts={p:dict(used=q['used'], fetched=q['fetched'])
                          for p,q in anchor['accounts'].items()})


def observe(state, anchor):
    if state is not None and state['anchor'] == anchor:
        return state
    updated: dict = dict(contract=STATE_CONTRACT, anchor=anchor, segment=segment_baseline(anchor),
                         samples={p:[] for p in anchor['accounts']},
                         status={p:'baseline' for p in anchor['accounts']})
    if state is None:
        return updated
    previous = state['anchor']
    if previous['binding'] != anchor['binding']:
        raise ValueError('canonical association changed')
    span = anchor['at']-previous['at']
    if span <= 0:
        raise ValueError('observation not newer')
    if any(p in previous['accounts'] and q['fetched'] <= previous['accounts'][p]['fetched']
           for p,q in anchor['accounts'].items()):
        raise ValueError('quota observation not newer')
    if set(previous['counters']) - set(anchor['counters']):
        raise ValueError('canonical source disappeared')
    tokens = 0
    for key, counts in anchor['counters'].items():
        old = previous['counters'].get(key,{k:0 for k in counts})
        if any(counts[k] < old[k] for k in counts):
            raise ValueError('canonical counter decreased')
        tokens += counts['total_tokens']-old['total_tokens']
    if tokens > MAX_SAFE:
        raise ValueError('delta overflow')
    same_window = True
    movements = {}
    for principal, quota in anchor['accounts'].items():
        old = previous['accounts'].get(principal)
        if old and (quota['reset'],quota['tier']) == (old['reset'],old['tier']) and quota['used'] >= old['used']:
            updated['samples'][principal] = [s for s in state['samples'][principal] if anchor['at']-s['end'] <= 86400]
            movements[principal] = (quota['used']-old['used'])*100
        else:
            same_window = False
    active = [p for p,points in movements.items() if points > 0]
    pair_active = active
    # A zero-token pair can still discard an earlier unfinished token window.
    # Invalidation must not let its old samples become eligible after that loss.
    unresolved = ((tokens > 0 and not active) or
                  state.get('segment', {}).get('tokens', 0) > 0)
    if set(previous['counters']) != set(anchor['counters']):
        if unresolved:
            updated['samples'] = {p:[] for p in anchor['accounts']}
        updated['status'] = {p:'sources-changed' for p in anchor['accounts']}
        return updated
    if previous['inventory'] != anchor['inventory'] or not same_window:
        if unresolved:
            updated['samples'] = {p:[] for p in anchor['accounts']}
        return updated
    if span > MAX_GAP or any(q['fetched']-previous['accounts'][p]['fetched'] > MAX_GAP
                             for p,q in anchor['accounts'].items()):
        if unresolved:
            updated['samples'] = {p:[] for p in anchor['accounts']}
        updated['status'] = {p:'resolution' if unresolved else 'interval' for p in anchor['accounts']}
        return updated
    # Freshness uses the latest pair; learning uses a separate bounded window.
    # Rounded quota can stay flat while tokens advance. Keep that whole delta
    # unassigned and withhold estimates until one principal's movement resolves
    # the window; never pair a later tick with only its own minute's tokens.
    segment = copy.deepcopy(state.get('segment', segment_baseline(previous)))
    segment['tokens'] += tokens
    if segment['tokens'] > MAX_SAFE:
        raise ValueError('segment overflow')
    span = anchor['at']-segment['at']
    movements = {p:(q['used']-segment['accounts'][p]['used'])*100 for p,q in anchor['accounts'].items()}
    active = [p for p,points in movements.items() if points > 0]
    if len(active) > 1 or (active and segment['tokens'] <= 0):
        updated['status'] = {p:'ambiguous' for p in anchor['accounts']}
        return updated
    clocks = [q['fetched']-segment['accounts'][p]['fetched'] for p,q in anchor['accounts'].items()]
    if span > MAX_GAP or any(seconds > MAX_GAP for seconds in clocks):
        if segment['tokens'] > 0:
            updated['samples'] = {p:[] for p in anchor['accounts']}
        updated['status'] = {p:'interval' for p in anchor['accounts']}
        return updated
    if not active or not pair_active:
        if segment['tokens'] > 0:
            updated['segment'] = segment
            updated['status'] = {p:'resolution' for p in anchor['accounts']}
        else:
            updated['status'] = state['status'].copy()
        return updated
    principal = active[0]
    points = movements[principal]
    if points > MAX_POINTS or not 0 < segment['tokens']/points <= MAX_RATE:
        updated['status'][principal] = 'resolution'
        return updated
    short = span < MIN_INTERVAL or any(seconds < MIN_INTERVAL for seconds in clocks)
    if short or points < MIN_POINTS-1e-10:
        updated['segment'] = segment
        # A consistent unfinished segment is not a new sample. It may keep an
        # existing usable rate; no prior rate/coverage is invented or borrowed.
        usable = state['status'][principal] == 'usable' and points >= MIN_POINTS-1e-10
        updated['status'][principal] = 'usable' if usable else ('interval' if short else 'resolution')
        return updated
    updated['samples'][principal].append(dict(start=segment['at'],end=anchor['at'],tokens=segment['tokens'],points=points))
    updated['samples'][principal] = updated['samples'][principal][-MAX_SAMPLES:]
    updated['status'][principal] = 'usable'
    return updated


def validate_state(state, adapter, config):
    required = {'contract','config','anchor','samples','status','transaction'}
    current = isinstance(state,dict) and state.get('contract') in (STATE_CONTRACT,PREVIOUS_STATE_CONTRACT)
    if current:
        required.add('segment')
    if (not isinstance(state,dict) or set(state) != required or
            state['contract'] not in (STATE_CONTRACT,PREVIOUS_STATE_CONTRACT,LEGACY_STATE_CONTRACT) or state['config'] != config):
        raise ValueError('invalid state contract or configuration')
    anchor = state['anchor']
    if not isinstance(anchor,dict) or set(anchor) != {'binding','at','counters','inventory','accounts'}:
        raise ValueError('invalid anchor')
    hex_id(anchor['binding']); hex_id(anchor['inventory'])
    at = number(anchor['at'],1,4102444800)
    counters, accounts = anchor['counters'],anchor['accounts']
    if not isinstance(counters,dict) or len(counters) > MAX_SOURCES:
        raise ValueError('invalid counter inventory')
    fields = {*adapter.COUNTERS,'prompt_tokens','total_tokens'}
    for ident, counts in counters.items():
        hex_id(ident)
        if not isinstance(counts,dict) or set(counts) != fields:
            raise ValueError('invalid source counts')
        for value in counts.values(): adapter.safe_count(value)
        if (counts['total_tokens'] != sum(counts[k] for k in adapter.COUNTERS[:4]) or
                counts['prompt_tokens'] != counts['total_tokens']-counts['output_tokens'] or
                counts['reasoning_tokens'] > counts['output_tokens']):
            raise ValueError('conflicting canonical counts')
    adapter.safe_count(sum(c['total_tokens'] for c in counters.values()))
    if not isinstance(accounts,dict) or len(accounts) > MAX_ACCOUNTS:
        raise ValueError('invalid account inventory')
    for principal, quota in accounts.items():
        hex_id(principal)
        if not isinstance(quota,dict) or set(quota) != {'used','reset','fetched','tier'}:
            raise ValueError('invalid quota anchor')
        number(quota['used'],0,1); number(quota['reset'],1,4102444800); number(quota['fetched'],1,4102444800)
        if (abs(quota['fetched']-at) > FRESH_SECONDS or not 0 < quota['reset']-at <= 604800 or
                quota['tier'] not in ('free','plus','pro','team','business','enterprise','edu','Hermes')):
            raise ValueError('invalid quota association')
    segment_at = at
    unassigned = False
    if current:
        segment = state['segment']
        if not isinstance(segment,dict) or set(segment) != {'at','tokens','accounts'}:
            raise ValueError('invalid learning segment')
        segment_at = number(segment['at'],1,at)
        pending = adapter.safe_count(segment['tokens'])
        if (at-segment_at > MAX_GAP or pending > sum(c['total_tokens'] for c in counters.values()) or
                (segment_at == at) != (pending == 0) or
                not isinstance(segment['accounts'],dict) or set(segment['accounts']) != set(accounts)):
            raise ValueError('invalid learning interval or membership')
        moving = 0
        for principal, start in segment['accounts'].items():
            quota = accounts[principal]
            if not isinstance(start,dict) or set(start) != {'used','fetched'}:
                raise ValueError('invalid learning quota')
            used = number(start['used'],0,quota['used'])
            fetched = number(start['fetched'],1,quota['fetched'])
            if (abs(fetched-segment_at) > FRESH_SECONDS or quota['fetched']-fetched > MAX_GAP or
                    segment_at < quota['reset']-604800):
                raise ValueError('invalid learning quota clock')
            moving += quota['used'] > used
        if (moving > 1 or (not pending and moving) or
                (state['contract'] == PREVIOUS_STATE_CONTRACT and moving != (1 if pending else 0))):
            raise ValueError('invalid learning movement')
        unassigned = pending > 0 and moving == 0
    if (not isinstance(state['samples'],dict) or not isinstance(state['status'],dict) or
            set(state['samples']) != set(accounts) or set(state['status']) != set(accounts)):
        raise ValueError('state membership mismatch')
    if unassigned and any(status != 'resolution' for status in state['status'].values()):
        raise ValueError('unassigned token window must withhold estimates')
    for principal, samples in state['samples'].items():
        if (not isinstance(samples,list) or len(samples) > MAX_SAMPLES or
                state['status'][principal] not in ('baseline','interval','ambiguous','resolution','usable','sources-changed')):
            raise ValueError('invalid samples or status')
        previous_end = 0
        for sample in samples:
            if not isinstance(sample,dict) or set(sample) != {'start','end','tokens','points'}:
                raise ValueError('invalid sample')
            start,end = number(sample['start'],1,at),number(sample['end'],1,at)
            tokens = adapter.safe_count(sample['tokens'])
            points = number(sample['points'],MIN_POINTS-1e-10,MAX_POINTS)
            if (not MIN_INTERVAL <= end-start <= MAX_GAP or start < previous_end or at-end > 86400 or
                    end > segment_at or
                    start < accounts[principal]['reset']-604800 or not 0 < tokens/points <= MAX_RATE):
                raise ValueError('invalid sample interval or ratio')
            previous_end = end
    transaction = state['transaction']
    if not isinstance(transaction,dict) or set(transaction) != {'path','preimage','payload'}:
        raise ValueError('invalid export transaction')
    if not isinstance(transaction['path'],str) or not 1 <= len(transaction['path']) <= 4096:
        raise ValueError('invalid export target')
    hex_id(transaction['payload'])
    if transaction['preimage'] is not None: hex_id(transaction['preimage'])
    if len(adapter.payload_bytes(state)) > MAX_BYTES:
        raise ValueError('state exceeded bound')


def replay(adapter, state, output):
    transaction = state['transaction']
    record = project(state)
    expected = hashlib.sha256(adapter.payload_bytes(record)).hexdigest()
    if transaction['path'] != str(output) or transaction['payload'] != expected:
        raise ValueError('export association changed')
    preimage = adapter.fingerprint(output)
    current = adapter.preimage_digest(preimage)
    if current not in (transaction['preimage'],expected):
        raise ValueError('output modified outside owned transaction')
    if current != expected:
        adapter.publish(record,output,expected=preimage)
    adapter.sync_private(output)
    verified = adapter.fingerprint(output)
    if adapter.preimage_digest(verified) != expected:
        raise ValueError('export readback mismatch')
    return verified


def preflight(adapter, args):
    lock = args.state.with_name(args.state.name+'.lock')
    targets = [args.state,args.output,lock,adapter.temporary_path(args.state),adapter.temporary_path(args.output)]
    adapter.reject_aliases(args.canonical,*targets)
    for target in targets:
        if target.resolve().is_relative_to(args.quota_dir.resolve()):
            raise ValueError('outputs cannot overlap scanner inputs')
        adapter.fingerprint(target)
    return lock


def run(args):
    adapter = sibling('hermes_accounting')
    now = instant(args.now) if args.now is not None else datetime.now(timezone.utc).timestamp()
    preflight(adapter,args)
    config = digest([str(args.canonical),str(args.quota_dir),args.profile,str(args.state),str(args.output)])
    feed = sibling('canonical_feed')
    with feed.feed_lock(args.state.with_name(args.state.name+'.lock'),adapter):
        preflight(adapter,args)
        state_pre = adapter.fingerprint(args.state)
        state = read_record(adapter,args.state,MAX_BYTES)[0] if state_pre is not None else None
        if state is not None:
            validate_state(state,adapter,config)
            if adapter.fingerprint(args.state) != state_pre:
                raise ValueError('state changed while loading')
        elif adapter.fingerprint(args.output) is not None:
            raise ValueError('unowned output without state')
        if args.recover:
            if state is None:
                raise ValueError('no committed state')
            replay(adapter,state,args.output)
            return project(state)
        binding, at, counters = canonical_snapshot(adapter,args.canonical,args.profile,now)
        generation, accounts = quota_snapshot(adapter,args.quota_dir,now)
        if any(abs(q['fetched']-at) > FRESH_SECONDS for q in accounts.values()):
            raise ValueError('unaligned observations')
        updated = observe(state,baseline(binding,at,counters,generation,accounts))
        output = project(updated)
        authorized_output = None  # Initialization owns absence, never a later file.
        if state is not None:
            authorized_output = replay(adapter,state,args.output)
            if state == updated:
                return output
        if adapter.fingerprint(args.output) != authorized_output:
            raise ValueError('output changed before journal commit')
        updated['config'] = config
        updated['transaction'] = dict(path=str(args.output),
            preimage=adapter.preimage_digest(authorized_output),
            payload=hashlib.sha256(adapter.payload_bytes(output)).hexdigest())
        validate_state(updated,adapter,config)
        adapter.publish(updated,args.state,expected=state_pre)
        adapter.sync_private(args.state)
        if adapter.preimage_digest(adapter.fingerprint(args.state)) != hashlib.sha256(adapter.payload_bytes(updated)).hexdigest():
            raise ValueError('state readback mismatch')
        replay(adapter,updated,args.output)
        return output


def service_text(args):
    if args.recover or args.now is not None:
        raise ValueError('rendering cannot collect or replay')
    if not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_-]{0,63}',args.profile):
        raise ValueError('invalid public profile')
    arguments = ['/usr/bin/python3','-I','-B',str(Path(__file__).resolve())]
    paths = [args.canonical,args.quota_dir,args.state,args.output]
    for path in paths:
        if not path.is_absolute() or str(path) != str(path.resolve()):
            raise ValueError('explicit absolute normalized paths required')
    if len(set(paths)) != len(paths) or any(p.is_relative_to(args.quota_dir) for p in (args.state,args.output)):
        raise ValueError('unit inputs and outputs must differ')
    for option in ('canonical','quota-dir','profile','state','output'):
        arguments.extend(('--'+option,str(getattr(args,option.replace('-','_')))))
    if any(not text or len(text)>4096 or any(ord(c)<32 or ord(c)==127 for c in text) for text in arguments):
        raise ValueError('invalid service arguments')
    command = ' '.join(json.dumps(v.replace('%','%%').replace('$','$$'),ensure_ascii=False) for v in arguments)
    template = Path(__file__).with_name('quota-calibration.service.in').read_text()
    if template.count('@EXEC_START@') != 1:
        raise ValueError('invalid service template')
    return template.replace('@EXEC_START@',command)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--canonical',required=True,type=Path,help='existing sanitized canonical-feed export, not a DB')
    parser.add_argument('--quota-dir',required=True,type=Path,help='existing committed sanitized scanner publication')
    parser.add_argument('--profile',required=True,help='canonical feed public profile label')
    parser.add_argument('--state',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--render-service',action='store_true',help='print dormant systemd service; no observation or install')
    parser.add_argument('--recover',action='store_true',help='replay committed export only; never refresh its timestamp')
    parser.add_argument('--now',help='explicit RFC3339 clock for offline replay; defaults to current UTC')
    args = parser.parse_args(argv)
    try:
        if args.render_service:
            print(service_text(args),end='')
            return 0
        with sibling('canonical_feed').cli_limits():
            run(args)
    except (ValueError,OSError,OverflowError,RecursionError,MemoryError,TypeError,KeyError):
        print('Passive quota calibration refused; require fresh canonical and committed sanitized quota inputs. Last good files retained.',file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
