#!/usr/bin/env python3
"""Native headless Quickshell load of the staged Agents + Local clone.

Fixture only, never live acceptance: an isolated HOME/XDG tree, an offscreen Qt
platform, no session bus, and a PATH that holds nothing but logging stubs (the
panel's own refresh command included), so no collector, login, network or
desktop command can run. The installed Agents plugin is only read.

One native primitive is replaced: KeyboardPanel is a layer-shell window, which
cannot exist offscreen, so a plain Item with the same API stands in for it.
Everything else (the patched Panel/Main, Agent, and the shell's Ui/Commons
components) is the real code. Popup placement, focus and theming are therefore
NOT covered here.
"""
import datetime as dt
import decimal
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
STOCK = Path(os.environ.get('ALACARCHY_AGENTS_SOURCE', '/usr/share/omarchy/shell/plugins/agents'))
SHELL = Path(os.environ.get('ALACARCHY_SHELL_SOURCE', '/usr/share/omarchy/shell'))
QS = Path('/usr/bin/qs')
# Everything the panel could spawn. Only the first two run without a click.
EXPECTED_STUBS = ('omarchy-agent-usage-update', 'omarchy-agent-account-add', 'omarchy-agent-local')
FORBIDDEN_STUBS = ('omarchy-agent', 'omarchy-agent-prompt', 'omarchy-agent-account-rename', 'omarchy-agent-account-use',
                   'omarchy-agent-account-mode', 'omarchy-launch-browser', 'bash', 'sh', 'curl', 'systemctl', 'grok', 'claude', 'codex')


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def skip(reason):
    print('SKIP: ' + reason)
    print('Headless fixture only; rendered/live acceptance not observed.')
    sys.exit(0)


def count_label(n):
    """Native formatTokenCount() for the en catalog: toFixed(1) plus a unit."""
    if n is None:
        return '?'
    for unit, scale in (('B', 10**9), ('M', 10**6), ('K', 10**3)):
        if n >= scale:
            # toFixed() rounds the exact binary quotient; exact ties go up.
            return str(decimal.Decimal(n / scale).quantize(decimal.Decimal('0.1'), rounding=decimal.ROUND_HALF_UP)) + unit
    return str(n)


def renderer_stimuli():
    """Renderer-only synthetic views: NOT production data or a consumer contract.

    The shipped selectHistory() emits at most seven (year: five) buckets, so
    8- and 14-bucket views exist only here, to prove the generated calendar
    wraps only after the seventh date. Long counts (133.4M+) and unknown and
    partial markers exercise label fitting at every popup width."""
    ids = [hashlib.sha256(b'synthetic-renderer-source-%d' % i).hexdigest() for i in range(3)]
    end = dt.date(2024, 2, 29)
    def keys(grain, n):
        back = range(n - 1, -1, -1)
        if grain == 'day':
            return [str(end - dt.timedelta(days=j)) for j in back]
        if grain == 'week':
            return [str(end - dt.timedelta(days=end.weekday(), weeks=j)) for j in back]
        if grain == 'month':
            return ['%04d-%02d' % (m // 12, m % 12 + 1) for m in (end.year * 12 + end.month - 1 - j for j in back)]
        return [str(end.year - j) for j in back]
    # Combined buckets: (value, partial). Per-source cells cycle through counts.
    combined = [(133_400_000, True), (None, False), (0, False), (987_654_321, True),
                (12_340, False), (1_234_567_890, True), (999, False), (0, True)]
    counts = [133_400_000, None, 0, 987_654_321, 12_340, 1_234_567_890, 999]
    def view(name, grain, mode, n, sources=2):
        chosen, bars = ids[:sources], []
        for k, key in enumerate(keys(grain, n)):
            if mode == 'combined':
                value, partial = combined[k % len(combined)]
                known = 0 if value is None else sources - 1 if partial else sources
                series = []
            else:
                series = [{'id': s, 'value': counts[(k + j) % len(counts)], 'colorIndex': int(s[:2], 16) % 6}
                          for j, s in enumerate(chosen)]
                measured = [c['value'] for c in series if c['value'] is not None]
                value, known = (sum(measured) if measured else None), len(measured)
                partial = 0 < known < sources
            bars.append({'key': key, 'value': value, 'known': known, 'partial': partial, 'series': series})
        return {'name': name, 'view': {
            'state': 'observed', 'grain': grain, 'metric': 'total_tokens', 'mode': mode,
            'summaries': [], 'bars': bars, 'selectionEmpty': False, 'selectionError': False,
            'maximum': max(b['value'] for b in bars if b['value'] is not None),
            'sources': [{'id': s, 'provider': 'synthetic', 'present': True,
                         'label': 'synthetic · renderer · ' + s[:8]} for s in chosen],
            'selectedSources': chosen}}
    return [view('day-7-combined', 'day', 'combined', 7), view('day-8-combined', 'day', 'combined', 8),
            view('week-14-multiple', 'week', 'multiple', 14, 3), view('month-8-combined', 'month', 'combined', 8),
            view('year-14-multiple', 'year', 'multiple', 14)]


def expected_labels(view):
    """Unbroken calendar labels in item order: each date's source cells, then its date."""
    def date(key):
        return key[5:] if view['grain'] in ('day', 'week') else key
    out = []
    for bar in view['bars']:
        if view['mode'] == 'multiple':
            out += [c['id'][:4] + '\n' + count_label(c['value']) for c in bar['series']]
            out.append(date(bar['key']))
        else:
            out.append(date(bar['key']) + '\n' + count_label(bar['value']) + ('+' if bar['partial'] and bar['value'] is not None else ''))
    return out


def fitted_lines(shown, full):
    """Shown lines grouped per unbroken label line, or None unless `shown`
    is `full` with only line breaks inserted."""
    lines, groups = iter(shown.split('\n')), []
    for logical in full.split('\n'):
        part = next(lines, None)
        group, built = ([] if part is None else [part]), part
        while built is not None and built != logical and logical.startswith(built):
            part = next(lines, None)
            built = None if part is None else built + part
            group.append(part)
        if built != logical:
            return None
        groups.append(group)
    return groups if next(lines, None) is None else None


if not QS.is_file() or not (STOCK/'Panel.qml').is_file() or not (SHELL/'Ui').is_dir():
    skip('Quickshell and the installed Omarchy Agents plugin are required')

stager = load('local_stage', ROOT/'extras/local-router-stats/stage_plugin.py')
collector = load('local_collector', ROOT/'extras/local-router-stats/collector.py')
bridge = load('qml_bridge', ROOT/'extras/hermes-codex-usage/collector.py')
bridge_fixture = load('qml_bridge_fixture', ROOT/'tests/hermes-codex-usage.py')
# Quota-card visibility uses the real ticking display clock; keep this synthetic
# publication on that clock instead of the unit fixture's fixed calendar date.
bridge_fixture.NOW = int(dt.datetime.now(dt.timezone.utc).timestamp())
LOCAL_CASE = os.environ.get('ALACARCHY_LOCAL_CASE', 'sampled')
assert LOCAL_CASE in ('sampled', 'unrecorded', 'metrics-disabled', 'no-model')
CLOUD_CASE = os.environ.get('ALACARCHY_CLOUD_CASE', 'recorded')
assert CLOUD_CASE in ('recorded', 'zero', 'missing')
ACCOUNTING_CASE = os.environ.get('ALACARCHY_ACCOUNTING_CASE', 'missing')
assert ACCOUNTING_CASE in ('missing', 'observed', 'baseline', 'zero', 'wrong-path', 'reactive')
LIVE_CASE = os.environ.get('ALACARCHY_LIVE_CASE', 'none')
RECENT_METRICS = os.environ.get('ALACARCHY_RECENT_METRICS') == '1'
assert not RECENT_METRICS or LIVE_CASE == 'metrics'
assert LIVE_CASE in ('none', 'rates', 'expired', 'malformed', 'cancelled', 'identity', 'republish', 'metrics')
if ACCOUNTING_CASE == 'reactive':
    assert (CLOUD_CASE, LOCAL_CASE, LIVE_CASE) == ('recorded', 'sampled', 'none')
if LIVE_CASE == 'republish':
    # Owns its own native handshakes; never mixed with accounting/history mutations.
    assert (LOCAL_CASE, ACCOUNTING_CASE, os.environ.get('ALACARCHY_HISTORY_CASE', 'missing')) == ('sampled', 'missing', 'missing')

# Keep native Quickshell IPC socket paths below Linux's sun_path limit.
with tempfile.TemporaryDirectory(prefix='ag-') as td:
    fixture = Path(td)
    runtime = fixture/'runtime'
    runtime.mkdir(mode=0o700)
    content_case = os.environ.get('ALACARCHY_CONTENT_CASE', 'native')
    assert content_case in ('native', 'eager')
    native_source = STOCK
    # Admit and stage the real native source unchanged. Eager geometry below is
    # a declared renderer-only compatibility stimulus, not a synthetic source ABI.
    candidate = os.environ.get('ALACARCHY_AGENTS_CANDIDATE')
    if candidate:
        manager = load('qml_manager', ROOT/'extras/agents-clone/manage.py')
        assert manager.clone_info(Path(candidate)) is not None, 'candidate is not a complete managed clone'
        candidate_manifest = json.loads((Path(candidate)/'manifest.json').read_text())
        native_fingerprint = stager.fingerprint(STOCK)
        origin_path = os.environ.get('ALACARCHY_AGENTS_ORIGIN_MANIFEST')
        if origin_path:
            # An existing user clone retains its real older origin: do not
            # relabel it as newly generated from the current native package.
            # Validate the complete origin manifest and allow ONLY Panel.qml's
            # managed hash to change. All imports still use the current Ui.
            origin = json.loads(Path(origin_path).read_text())
            expected = json.loads(json.dumps(origin))
            expected['omarchy']['managedFiles']['Panel.qml'] = candidate_manifest['omarchy']['managedFiles']['Panel.qml']
            assert candidate_manifest == expected, 'candidate changes more than the exact existing calendar panel'
            print('EXISTING_CLONE_PROVENANCE ' + json.dumps({
                'retained_origin': origin['omarchy']['alaCarchySource'],
                'current_native_imports': native_fingerprint,
                'origin_unchanged': True, 'only_panel_hash_changed': True}), flush=True)
        else:
            assert candidate_manifest['omarchy']['alaCarchySource'] == native_fingerprint
        shutil.copytree(candidate, fixture/'plugin')
    else:
        stager.stage(native_source, fixture/'plugin', 'fixture.agents')
    if content_case == 'eager':
        assert not candidate, 'eager renderer stimulus does not mutate a supplied managed candidate'
        generated_panel = fixture/'plugin/Panel.qml'
        panel_source = generated_panel.read_text()
        before = hashlib.sha256(panel_source.encode()).hexdigest()
        for name in ('panelFlick','hero','column','providerSections'):
            anchor = 'panel.loadedContent ? panel.loadedContent.' + name + ' : null'
            assert panel_source.count(anchor) == 1
            panel_source = panel_source.replace(anchor, 'keyCatcher.' + name, 1)
        for old, new in [
            ('    content: PanelKeyCatcher {', '    PanelKeyCatcher {'),
            ('    contentHeight: panel.fittedContentHeight(panel.loadedContentHeight)',
             '    contentHeight: panel.fittedContentHeight(keyCatcher.implicitHeight)')]:
            assert panel_source.count(old) == 1
            panel_source = panel_source.replace(old, new, 1)
        panel_source = panel_source.replace('panel.loadedContent', 'keyCatcher')
        generated_panel.write_text(panel_source)
        print('EAGER_RENDERER_STIMULUS ' + json.dumps({'native_source_unchanged':True,
            'generated_before':before, 'generated_after':hashlib.sha256(panel_source.encode()).hexdigest(),
            'production_source_admission_claim':False}), flush=True)
    layout = json.loads((fixture/'plugin/manifest.json').read_text())['omarchy']['alaCarchyLayout']
    if layout != 'limits':
        skip('installed Agents panel uses the %s layout; this harness drives the limits-first layout' % layout)
    (fixture/'Commons').symlink_to(SHELL/'Commons', target_is_directory=True)
    (fixture/'Ui').mkdir()
    for path in (SHELL/'Ui').iterdir():
        if path.name != 'KeyboardPanel.qml':
            (fixture/'Ui'/path.name).symlink_to(path)
    (fixture/'Ui/KeyboardPanel.qml').write_text('''import QtQuick
import Quickshell
// Test stand-in for the layer-shell popup: same API, plain Item.
Item {
  id: root
  objectName: "keyboardPanel"
  // Current native API; this fixture does not claim layer-shell focus proof.
  property bool yielding: false
  property Item anchorItem: null
  property QtObject bar: null
  property var owner: null
  property int margin: 0
  property int padding: 0
  property int contentWidth: 380
  property int contentHeight: 600
  property bool centerOnBar: false
  property bool open: false
  property Item focusTarget: null
  default property alias contentItem: contentHolder.children
  property Component content: null
  property bool contentRequested: false
  readonly property Item loadedContent: contentLoader.item
  readonly property real loadedContentHeight: loadedContent ? loadedContent.implicitHeight : 0
  onOpenChanged: if (open && content) contentRequested = true
  function ensureContent() { if (content) contentRequested = true; return contentLoader.item }
  function close() { if (owner && "close" in owner) owner.close(); else root.open = false }
  // Native cap: a narrower screen than the desired width fits the card to it.
  property real availableCardWidth: Quickshell.env("ALACARCHY_WIDTH_CASE") === "narrow" ? 248 : 0
  function fittedContentWidth(width, cap) { var d = Math.max(1, Number(width) || 1); var m = availableCardWidth > 0 ? availableCardWidth : d; if (Number(cap) > 0) m = Math.min(m, Number(cap)); return Math.round(Math.min(d, m)) }
  readonly property int availableCardHeight: Quickshell.env("ALACARCHY_SCREEN_CASE") === "short" ? 450 : 1400
  function fittedContentHeight(height, cap) { var d = Math.max(1, Number(height) || 0); var maximum = Number(cap) > 0 ? Math.min(availableCardHeight, Number(cap)) : availableCardHeight; return Math.round(Math.min(d, maximum)) }
  function cappedContentHeight(height) { return Math.max(1, Number(height) || 1) }
  width: contentWidth
  height: contentHeight
  Item {
    id: contentHolder
    anchors.fill: parent
    Loader {
      id: contentLoader
      anchors.fill: parent
      active: root.content !== null && (root.open || root.contentRequested)
      sourceComponent: root.content
    }
  }
}
''')

    # --- records: one subscription in the native usage directory, the Local
    # record in its own directory, and a stray usage/local.json that must
    # never be adopted.
    state = fixture/'state'
    usage = state/'omarchy/agents/usage'
    usage.mkdir(parents=True)
    history_case = os.environ.get('ALACARCHY_HISTORY_CASE', 'missing')
    assert history_case in ('missing', 'observed', 'baseline', 'zero', 'empty', 'malformed', 'overflow', 'unsupported', 'controls', 'inventory')
    if ACCOUNTING_CASE != 'missing':
        assert history_case == 'missing', 'accounting cases own their paired history fixture'
        history_case = {'baseline':'baseline', 'zero':'zero'}.get(ACCOUNTING_CASE, 'observed')
    cumulative_path = state/'omarchy/hermes-token-accounting/usage.json'
    expected_accounting = None
    history_fixture = None
    control_fixture = None
    control_data = None
    history_path = None
    if history_case != 'missing':
        history_fixture = load('history_fixture', ROOT/'tests/agents-history-consumer.py')
        producer_case = 'observed' if history_case in ('malformed','overflow','unsupported','controls','inventory') else history_case
        if ACCOUNTING_CASE == 'zero':
            producer_case = 'cumulative-zero'
        history_path = history_fixture.fixture(state, producer_case,
            provider='anthropic' if ACCOUNTING_CASE != 'missing' else 'openai-codex',
            include_local=ACCOUNTING_CASE != 'missing')
        expected_accounting = json.loads(cumulative_path.read_text())
        if history_case in ('controls', 'inventory'):
            control_fixture = load('history_controls_fixture', ROOT/'tests/agents-history-controls.py')
            # The large inventory is the backend projection of the live 313-identity shape.
            control_data = control_fixture.projected() if history_case == 'controls' else control_fixture.inventory()
            history_path.write_text(json.dumps(control_data))
            print('HISTORY_CONTROLS_FIXTURE ' + json.dumps({'sources':len(control_data['sources']),
                  'events':len(control_data['events']),
                  'sha256':hashlib.sha256(history_path.read_bytes()).hexdigest()}), flush=True)
        if history_case == 'malformed':
            history_path.write_text('{bad')
        elif history_case in ('overflow','unsupported'):
            broken = json.loads(history_path.read_text())
            if history_case == 'overflow':
                broken['buckets']['day'][0]['sources'][0]['counts']['total_tokens'] = 9007199254740992
            else:
                broken['contract'] = 'unsupported/1'
            history_path.write_text(json.dumps(broken))
        if ACCOUNTING_CASE == 'missing':
            # History-only cases deliberately omit cumulative counters. This is
            # a negative consumer control, not a second fixture output contract.
            cumulative_path.unlink()
            expected_accounting = None
        elif ACCOUNTING_CASE in ('wrong-path', 'reactive'):
            cumulative_path.rename(cumulative_path.with_name('accounting.json'))
            if ACCOUNTING_CASE == 'wrong-path':
                expected_accounting = None
    # Native reorder support is preserved; pin fixture order before async discovery.

    fixed_calendar = history_case != 'missing'
    calendar_now = '2024-02-29T15:05:30Z' if fixed_calendar else None
    today = dt.date(2024, 2, 29) if fixed_calendar else dt.datetime.now(ZoneInfo('America/New_York')).date()
    days = [str(today - dt.timedelta(days=offset)) for offset in range(6, -1, -1)]
    subscription = {
        'id': 'claude', 'name': 'Claude Code', 'ready': True, 'tierLabel': 'max', 'usageStatusText': '', 'authHelpText': '',
        'limits': [{'label': 'Session (5-hour)', 'percent': 0.25, 'resetsAt': ''}],
        'hasLocalStats': True, 'todayPrompts': 3, 'todaySessions': 1, 'todayTotalTokens': 1000, 'todayTokensByModel': {'claude-opus-4-8': 1000},
        'recentDays': [{'date': day, 'messageCount': 1000 if day == days[-1] else 0} for day in days],
        'totalPrompts': 3, 'totalSessions': 1, 'activeDays': 1,
        'modelUsage': {'claude-opus-4-8': {'inputTokens': 600, 'outputTokens': 400, 'cacheReadInputTokens': 0, 'cacheCreationInputTokens': 0}}}
    subscription['accounts'] = [{'id':'native-' + str(i), 'label':'Native account ' + str(i), 'plan':'max',
                                 'active':i == 1, 'limits':subscription['limits'], 'usageStatusText':''} for i in range(1, 4)]
    if CLOUD_CASE == 'zero':
        subscription.update(todayTotalTokens=0, todayTokensByModel={},
                            recentDays=[{'date':day, 'messageCount':0} for day in days],
                            modelUsage={'claude-opus-4-8':{'inputTokens':0, 'outputTokens':0}})
    elif CLOUD_CASE == 'missing':
        for key in ('hasLocalStats', 'todayTotalTokens', 'todayTokensByModel', 'recentDays', 'modelUsage'):
            subscription.pop(key)
    if ACCOUNTING_CASE != 'missing' and CLOUD_CASE != 'missing':
        for account in subscription['accounts']:
            account.update(hasLocalStats=True, todayTotalTokens=999999,
                           modelUsage={'aggregate-overlap-sentinel':{'inputTokens':999999, 'outputTokens':0}})
    (usage/'claude.json').write_text(json.dumps(subscription))
    # Dev-channel Grok is first-party: retain its real record contract beside Local.
    native_grok = dict(subscription, id='grok', name='Grok', tierLabel='X Premium',
                       limits=[{'label': 'Weekly', 'percent': 0.75, 'resetsAt': (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=7)).isoformat()}],
                       limitsFetchedAt=dt.datetime.now(dt.timezone.utc).timestamp()*1000, limitsStale=False, hasLocalStats=False,
                       todayPrompts=0, todaySessions=0, todayTotalTokens=0, todayTokensByModel={},
                       recentDays=[], modelUsage={}, accounts=[])
    (usage/'grok.json').write_text(json.dumps(native_grok))
    native_codex = dict(subscription, id='codex', name='NATIVE CODEX SENTINEL', tierLabel='team',
                        hasLocalStats=True, todayTotalTokens=0,
                        recentDays=[{'date':day, 'messageCount':0} for day in days],
                        modelUsage={'native-codex-history':{'inputTokens':600,'outputTokens':400}})
    (usage/'codex.json').write_text(json.dumps(native_codex))
    ghost_id = 'codex-hermes-' + hashlib.sha256(b'synthetic-uninventoried-principal').hexdigest()
    (usage/(ghost_id + '.json')).write_text(json.dumps(dict(subscription, id=ghost_id, name='GHOST CODEX SENTINEL')))
    rows = [bridge_fixture.entry(subject='auth0|qml-member-' + str(i)) for i in range(1, 4)]
    # Fresh 1 and matching last-known stale 2, using the real publication adapter.
    # Reset before the freshness age limit so native clock proof distinguishes both gates.
    zero_key = None
    def measured(token, ident):
        return bridge.normalize_quota(bridge_fixture.quota(primary=60, used=0 if ident['key'] == zero_key else 54), bridge_fixture.NOW)
    old = bridge.build_record(rows, {}, measured, bridge_fixture.NOW)
    zero_key = old['accounts'][2]['id']  # A genuine measured-zero bar stays visible.
    def partly_expired(token, ident):
        if ident['key'] == old['accounts'][1]['id']: raise bridge.Unavailable('expired')
        return measured(token, ident)
    synthetic_home = fixture/'profiles/synthetic'
    synthetic_home.mkdir(parents=True)
    auth = synthetic_home/'auth.json'
    auth.write_text(json.dumps({'credential_pool':{'openai-codex':rows, 'openrouter':[
        {'id':'PRIVATE_UNSUPPORTED_ID', 'access_token':'PRIVATE_UNSUPPORTED_SECRET', 'label':'PRIVATE_EMAIL@example.invalid'}]}}))
    auth_before = auth.read_bytes()
    private = fixture/'bridge-state'
    bridge.publish(private, old)
    snapshot_read = bridge.read_json
    def confined_read(path, *args, **kwargs):
        assert Path(path).absolute().is_relative_to(fixture), 'auth/state fixture escaped its synthetic root'
        return snapshot_read(path, *args, **kwargs)
    with patch.dict(os.environ, {'HOME':str(fixture), 'HERMES_HOME':str(synthetic_home),
                                 'XDG_STATE_HOME':str(state)}, clear=True), \
         patch.object(bridge, 'read_json', side_effect=confined_read), \
         patch.object(bridge, 'fetch', side_effect=partly_expired), \
         patch.object(bridge.time, 'time', return_value=bridge_fixture.NOW):
        assert bridge.run_once(synthetic_home, private, usage_dir=usage)
    assert auth.read_bytes() == auth_before
    hermes = json.loads((private/'usage.json').read_text())
    inventory = json.loads((usage/bridge.PUBLIC_INVENTORY).read_text())
    assert len(inventory['entries']) == len(rows) + 1, 'fixture inventory included non-fixture metadata'
    hermes_ids = [e['id'] for e in inventory['entries']]
    codex_ids = ['codex-hermes-' + a['id'] for a in hermes['accounts']]
    hermes_names = {e['id']:e['name'] for e in inventory['entries']}
    expected_ids = ['claude', *hermes_ids, 'grok', 'local']
    (usage.parent/'order.json').write_text(json.dumps(expected_ids))
    # Fixture inventory: use the native aggregate OR its account leaves once,
    # plus each bridge quota identity and the one native Grok record.
    native_cloud_sources = (len(subscription['accounts']) if CLOUD_CASE == 'missing' else 1) + len(inventory['entries']) + 1
    stray_grok = state/'omarchy/grok-usage/agents/grok.json'
    stray_grok.parent.mkdir(parents=True)
    stray_grok.write_text(json.dumps({'id':'grok', 'name':'STRAY CUSTOM GROK', 'ready':False,
                                    'usageStatusText':'Waiting for Grok token metadata · quota unavailable'}))
    (usage/'local.json').write_text(json.dumps({'id': 'local', 'name': 'STRAY', 'ready': True, 'tierLabel': 'stray', 'todayTotalTokens': 999999999}))

    ledger_dir = state/'omarchy/local-router'
    (ledger_dir/'agents').mkdir(parents=True)
    ledger = collector.Ledger(ledger_dir/'tokens.sqlite3')
    noon = dt.datetime.combine(today, dt.time(12)).timestamp()
    ledger.observe('local/model <b>A</b>', 'worker-a', (100, 20), noon)             # baseline, counts nothing
    ledger.observe('local/model <b>A</b>', 'worker-a', (1600, 520), noon + 5)       # +1500 in, +500 out
    ledger.observe('model-b', 'worker-b', (0, 0), noon + 5)
    ledger.observe('model-b', 'worker-b', (30, 12), noon + 10)                      # +42
    record = ledger.record(noon + 10, 'Sampled tokens · 2 model(s) · cache excluded')
    ledger.db.close()
    def idle_live(at):
        # Actual producer envelope of the observed live state: no loaded model,
        # one fresh observation per poll. Only the model listing is mocked.
        with patch.object(collector, 'get', return_value=json.dumps({'data': []})):
            return collector.sample(None, at, sampler=collector.LiveSampler(), clock=lambda: at)
    counted_record = None
    if LIVE_CASE == 'republish':
        # A later real ledger sample (+1000 evaluated input on model-b), taken on
        # a private copy so the published baseline stays the shared fixture.
        counted_ledger = collector.Ledger(shutil.copyfile(ledger_dir/'tokens.sqlite3', fixture/'republish.sqlite3'))
        try:
            assert counted_ledger.record(noon + 10, 'Sampled tokens · 2 model(s) · cache excluded') == record
            counted_ledger.observe('model-b', 'worker-b', (1030, 12), noon + 15)
            counted_record = counted_ledger.record(noon + 15, 'Sampled tokens · 2 model(s) · cache excluded')
        finally:
            counted_ledger.db.close()
        assert counted_record['todayTokensByModel'] == {'local/model <b>A</b>': 2000, 'model-b': 1042}, counted_record
        record['localLive'] = idle_live(dt.datetime.now(dt.timezone.utc).timestamp())
        assert record['localLive']['status'] == 'No loaded model · history retained' and record['localLive']['models'] == []
    elif LIVE_CASE == 'metrics':
        metrics_fixture = load('native_metrics_fixture', ROOT/'tests/local-metrics-summary.py')
        live_now = dt.datetime.now(dt.timezone.utc).timestamp()
        def metrics_worker(model, pid, for_slots=False):
            if for_slots:
                raise ValueError('synthetic unqualified slot transport')
            return ('fixture:20:888', 9001)
        live_ledger = collector.Ledger(fixture/'synthetic-metrics.sqlite3')
        try:
            with patch.object(collector, 'get', side_effect=[metrics_fixture.LISTING, metrics_fixture.METRICS]) as calls, \
                 patch.object(collector, 'worker', side_effect=metrics_worker), patch.object(collector, 'router_pid', return_value=10):
                record['localLive'] = collector.sample(live_ledger, live_now, sampler=collector.LiveSampler())
            assert [call.args[1] for call in calls.call_args_list] == ['/models', '/metrics']
        finally:
            live_ledger.db.close()
        record['localLive']['staleAfterSec'] = 10
    elif LIVE_CASE != 'none':
        # Actual producer/export chain, but all transport/process observations are mocked.
        live_fixture = load('native_rate_fixture', ROOT/'tests/local-live-consumer.py')
        live_ledger = collector.Ledger(fixture/'synthetic-live.sqlite3')
        live_now = dt.datetime.now(dt.timezone.utc).timestamp()
        kind = {'rates':'generation', 'expired':'generation', 'malformed':'malformed',
                'cancelled':'cancelled', 'identity':'changed'}[LIVE_CASE]
        try:
            exported = live_fixture.fixture_live(live_ledger, live_now - (40 if LIVE_CASE == 'expired' else 0), kind)
        finally:
            live_ledger.db.close()
        record['localLive'] = exported['localLive']
        if LIVE_CASE == 'rates': record['localLive']['staleAfterSec'] = 7
    collector.publish(ledger_dir/'agents/local.json', record)
    assert record['todayTotalTokens'] == 2042 and sum(day['recorded'] for day in record['recentDays']) == 1
    if LOCAL_CASE != 'sampled':
        record = dict(record, hasLocalStats=False, todayTotalTokens=0, todayTokensByModel={}, modelUsage={},
                      recentDays=[{'date':d, 'messageCount':None, 'recorded':False} for d in days],
                      usageStatusText={'unrecorded':'Activation pending · not collecting',
                                       'metrics-disabled':'Metrics disabled · history retained',
                                       'no-model':'No loaded model · history retained'}[LOCAL_CASE])
        collector.publish(ledger_dir/'agents/local.json', record)

    # --- PATH: stubs only. The real collectors live in /usr/bin, which is absent.
    bin_dir = fixture/'bin'
    bin_dir.mkdir()
    calls = fixture/'calls.log'
    calls.touch()
    unexpected = fixture/'unexpected.log'
    for name in EXPECTED_STUBS:
        # A bounded synthetic scheduling delay exposes quitting before refresh runs.
        delay = '/usr/bin/sleep 0.3\n' if os.environ.get('ALACARCHY_DELAY_REFRESH') == '1' else ''
        # Only these exact native metadata argv are simulated. Model setup,
        # pairing/sharing and every other argument vector remain forbidden.
        guard = ('case "$#:${1-}:${2-}" in 2:refresh:--quiet|2:network:--write) ;; '
                 '*) printf "unexpected-local-argv\\n" >> "' + str(unexpected) + '"; exit 97 ;; esac\n') if name == 'omarchy-agent-local' else ''
        (bin_dir/name).write_text('#!/bin/sh\n' + guard + delay + 'printf "%%s %%s\\n" "%s" "$*" >> "%s"\nexit 0\n' % (name, calls))
    for name in FORBIDDEN_STUBS:
        (bin_dir/name).write_text('#!/bin/sh\nprintf "%%s %%s\\n" "%s" "$*" >> "%s"\nexit 97\n' % (name, unexpected))
    (bin_dir/'hyprctl').write_text('#!/bin/sh\nprintf \'{"int": 0}\\n\'\n')
    (bin_dir/'fc-match').write_text('#!/bin/sh\nprintf monospace\n')
    for stub in bin_dir.iterdir():
        stub.chmod(0o755)
    # Exact private clock adapter: run only this packaged stdlib helper, with
    # an allowlisted optional timezone. No generic execution escape hatch.
    clock_calls = fixture/'clock-calls.jsonl'
    clock_adapter = bin_dir/'fixture-calendar-clock'
    clock_helper = fixture/'plugin/CalendarClock.py'
    clock_adapter.write_text('#!/usr/bin/python3 -I\n' + '''import json, os, subprocess, sys
from pathlib import Path
helper = HELPER
args = sys.argv[1:]
assert args in (['-I', '-B', helper], ['-I', '-B', helper, '--timezone', 'America/New_York'], ['-I', '-B', helper, '--timezone', 'UTC']), args
command = ['/usr/bin/python3', *args]
if NOW is not None: command += ['--now', str(__import__('datetime').datetime.fromisoformat(NOW.replace('Z', '+00:00')).timestamp())]
child = subprocess.run(command, capture_output=True, timeout=5)
with Path(CALLS).open('a') as stream:
    stream.write(json.dumps({'argv':command, 'exit':child.returncode, 'payload':child.stdout.decode()})+'\\n')
    stream.flush(); os.fsync(stream.fileno())
sys.stdout.buffer.write(child.stdout); sys.stderr.buffer.write(child.stderr)
sys.exit(child.returncode)
'''.replace('HELPER', repr(str(clock_helper))).replace('NOW', repr(calendar_now)).replace('CALLS', repr(str(clock_calls))))
    clock_adapter.chmod(0o700)
    main_path = fixture/'plugin/Main.qml'
    main_text = main_path.read_text()
    clock_anchor = 'readonly property var calendarClockCommand: ["/usr/bin/python3",'
    assert main_text.count(clock_anchor) == 1, 'clock adapter requires the exact maintained argv anchor'
    main_path.write_text(main_text.replace(clock_anchor, 'readonly property var calendarClockCommand: [' + json.dumps(str(clock_adapter)) + ',', 1))
    payloads = {}
    for label, when in {'initial':'2024-02-29T15:05:30Z', 'updated':'2024-02-29T15:10:30Z',
                        'removed':'2024-02-29T15:15:30Z', 'preMidnight':'2024-03-01T04:59:59Z',
                        'nextDay':'2024-03-01T05:00:00Z', 'refreshed':'2024-03-01T15:05:30Z'}.items():
        argv = ['/usr/bin/python3', '-I', '-B', str(clock_helper), '--timezone', control_data['timezone'] if control_data is not None else 'America/New_York',
                '--now', str(dt.datetime.fromisoformat(when.replace('Z','+00:00')).timestamp())]
        clock_child = subprocess.run(argv, env=dict(os.environ, HOME=str(fixture), HERMES_HOME=str(synthetic_home),
                                     TZ='America/New_York'), capture_output=True, text=True, timeout=5)
        assert clock_child.returncode == 0, (argv, clock_child.stderr)
        payloads[label] = json.loads(clock_child.stdout)
        print('CLOCK_CLI ' + json.dumps({'argv':argv, 'exit':clock_child.returncode,
              'payload':payloads[label]}), flush=True)
    (fixture/'clock-payloads.json').write_text(json.dumps(payloads))
    (bin_dir/'find').symlink_to('/usr/bin/find')
    env = {'HOME': str(fixture), 'HERMES_HOME':str(synthetic_home), 'XDG_CONFIG_HOME': str(fixture/'config'), 'XDG_RUNTIME_DIR': str(runtime),
           'XDG_CACHE_HOME': str(fixture/'cache'), 'XDG_STATE_HOME': str(state), 'XDG_DATA_HOME': str(fixture/'data'),
           'PATH': str(bin_dir), 'LANG': os.environ.get('LANG', 'C.UTF-8'), 'QT_QPA_PLATFORM': 'offscreen',
           'TMPDIR': '/home/typhoon/.hermes/cache/scratch', 'TZ':'America/New_York',
           'QT_QPA_PLATFORMTHEME': '', 'QT_STYLE_OVERRIDE': 'Fusion', 'QT_QUICK_CONTROLS_STYLE': 'Basic',
           'QT_QUICK_BACKEND': 'software',  # CPU-only fixture; never allocate a live GPU renderer
           'DBUS_SESSION_BUS_ADDRESS': 'unix:path=' + str(fixture/'no-session-bus'),
           'http_proxy': 'http://127.0.0.1:9', 'https_proxy': 'http://127.0.0.1:9'}
    # r7017 native I18n reads catalogs from OMARCHY_PATH, not the QML import root.
    # Copy only the version-matched English data into the isolated fixture.
    catalogs = fixture/'native/default/i18n'
    (catalogs/'en').mkdir(parents=True)
    for rel in ('en/shell.json', 'locales.json'):
        shutil.copyfile(SHELL.parent/'default/i18n'/rel, catalogs/rel)
    env['OMARCHY_PATH'] = str(fixture/'native')
    bar_case = os.environ.get('ALACARCHY_BAR_CASE', 'horizontal')
    assert bar_case in ('horizontal', 'vertical')
    env['ALACARCHY_CALLS'] = str(calls)
    env['ALACARCHY_BAR_CASE'] = bar_case
    screen_case = os.environ.get('ALACARCHY_SCREEN_CASE', 'short' if LIVE_CASE == 'republish' else 'tall')
    assert screen_case in ('tall', 'short')
    env['ALACARCHY_SCREEN_CASE'] = screen_case
    width_case = os.environ.get('ALACARCHY_WIDTH_CASE', 'normal')
    assert width_case in ('normal', 'narrow')
    env['ALACARCHY_WIDTH_CASE'] = width_case
    env['ALACARCHY_LIVE_CASE'] = LIVE_CASE
    env['ALACARCHY_RECENT_METRICS'] = '1' if RECENT_METRICS else '0'
    env['ALACARCHY_EDIT_PROOF'] = os.environ.get('ALACARCHY_EDIT_PROOF', '0')
    env['ALACARCHY_ACCOUNTING_CASE'] = ACCOUNTING_CASE
    env['ALACARCHY_HISTORY_CASE'] = history_case
    env['ALACARCHY_CALENDAR_NOW'] = calendar_now or ''
    env['ALACARCHY_DATED_FLOW'] = os.environ.get('ALACARCHY_DATED_FLOW', '0')
    env['ALACARCHY_CLOCK_PAYLOADS'] = str(fixture/'clock-payloads.json')
    stimuli = renderer_stimuli() if history_case == 'controls' else []
    if stimuli:
        # The scene renders the exact maintained chart template, which must be
        # the one in the Panel.qml under test (generated or candidate).
        assert (fixture/'plugin/Panel.qml').read_text().count(stager.HISTORY_PANEL) == 1, \
            'Panel.qml under test does not carry the maintained calendar template'
        # The isolated chart must carry the real template's helper closure,
        # not quietly render with missing coverage or calendar bindings.
        import re
        coverage_helpers = re.findall(
            r'^  function historyCoverageText\(bucket\) \{\n(?:    [^\n]*\n)+  \}',
            (fixture/'plugin/Panel.qml').read_text(), re.MULTILINE)
        assert len(coverage_helpers) == 1, 'exact maintained coverage helper required'
        (fixture/'calendar').mkdir()
        (fixture/'calendar/HistoryCalendarStimulus.qml').write_text('''import QtQuick
import QtQuick.Controls
import qs.Commons
import qs.Commons as Commons
import qs.Ui
import qs.Ui as Ui
// Renderer-only synthetic stimulus. NOT production data or a consumer
// contract: the shipped selectHistory() emits at most seven (year: five)
// buckets. The exact generated HISTORY_PANEL text renders hand-built views
// with the real panel's font, colours and count format.
Item {
  id: root
  required property var host
  required property var usage
  readonly property string fontFamily: host.fontFamily
  readonly property color foreground: host.foreground
  readonly property color dim: host.dim
  readonly property color urgent: host.urgent
  readonly property var stimuli: STIMULI
  property string stimulus: ""
  property real contentWidth: 0
  property var observedHistory: ({state:"observed", help:"Synthetic renderer stimulus", data:{timezone:"UTC"}})
  property string historyMetric: "total_tokens"
  property string historyGrain: "day"
  property string historyMode: "combined"
  property var historyView: ({state:"observed", summaries:[], bars:[], sources:[], selectedSources:[], maximum:0, mode:"combined"})
  function recordedCount(n) { return host.recordedCount(n) }
  function historySourceColor(id) { return host.historySourceColor(id) }
COVERAGE_HELPER
  function historySourceLabel(id) {
    for (var i = 0; i < historyView.sources.length; i++)
      if (historyView.sources[i].id === id) return historyView.sources[i].label
    return id.slice(0, 8)
  }
  function show(name, width) {
    var next = stimuli.filter(function(s) { return s.name === name })[0]
    historyGrain = next.view.grain
    historyMode = next.view.mode
    historyView = next.view
    contentWidth = width
    stimulus = name
  }
  width: column.width
  height: column.height
  Column {
    id: column
    width: root.contentWidth
TEMPLATE  }
}
'''.replace('STIMULI', json.dumps(stimuli)).replace('COVERAGE_HELPER', coverage_helpers[0])
   .replace('TEMPLATE', stager.HISTORY_PANEL))
        print('HISTORY_RENDERER_STIMULUS ' + json.dumps({'synthetic':True, 'production_data':False,
              'views':{s['name']:len(s['view']['bars']) for s in stimuli},
              'template_sha256':hashlib.sha256(stager.HISTORY_PANEL.encode()).hexdigest()}), flush=True)
    disconnect = os.environ.get('ALACARCHY_HISTORY_DISCONNECT', '')
    assert disconnect in ('', 'metric')
    if disconnect:
        panel_path = fixture/'plugin/Panel.qml'
        panel_text = panel_path.read_text()
        handler = 'onClicked: root.historyMetric = modelData.id'
        assert panel_text.count(handler) == 1, 'negative control requires the real native metric handler'
        panel_path.write_text(panel_text.replace(handler, 'onClicked: {}'))
    env['ALACARCHY_NATIVE_CLOUD_SOURCES'] = str(native_cloud_sources)
    env['ALACARCHY_HERMES_IDS'] = json.dumps(hermes_ids)
    env['ALACARCHY_CODEX_IDS'] = json.dumps(codex_ids)
    env['ALACARCHY_GHOST_ID'] = ghost_id
    env['ALACARCHY_PROVIDER_COUNT'] = str(len(expected_ids))
    env['ALACARCHY_EXPECTED_IDS'] = json.dumps(expected_ids)
    env['ALACARCHY_ACCOUNTING_VALUE'] = str(expected_accounting['sources'][0]['windows']['history']
                                           if expected_accounting else 0)
    for name in EXPECTED_STUBS + FORBIDDEN_STUBS:
        assert shutil.which(name, path=env['PATH']) == str(bin_dir/name), 'a real ' + name + ' is reachable'

    (fixture/'shell.qml').write_text('''import QtQuick
import QtQuick.Controls
import Quickshell.Io
import Quickshell
import QtTest 1.2 as FixtureTest
import "plugin" as Agents
ShellRoot {
  id: root
  FileView { id: commandLog; path: Quickshell.env("ALACARCHY_CALLS"); printErrors: false }
  FileView { id: clockPayloads; path: Quickshell.env("ALACARCHY_CLOCK_PAYLOADS"); printErrors: false }
  property bool reported: false
  property bool panelOpenedForProof: false
  property bool preOpenHeaderAbsent: false
  property bool datedClockConfigured: false
  property int datedFlowStage: 0
  property int datedFlowWait: 0
  property var datedClockProof: []
  property var datedUndatedIdentity: null
  function datedMain(item) {
    if (!item) return null
    if ("calendarNowMs" in item) return item
    var kids = item.children || []
    for (var i = 0; i < kids.length; i++) { var found = datedMain(kids[i]); if (found) return found }
    return null
  }
  function configureDatedClock() {
    if (datedClockConfigured) return true
    var main = datedMain(panel), fixed = Quickshell.env("ALACARCHY_CALENDAR_NOW")
    if (!main || !main.calendarClock || !admission.calendarClock) return false
    if (fixed) {
      for (var m of [main, admission]) {
        m.calendarTickTimer.running = false
        m.calendarRefreshTimer.running = false
        m.calendarNowMs = Date.parse(fixed)
      }
    }
    datedClockConfigured = true
    return false // settle the actual bindings after clock control
  }
  function setDatedClock(payload) {
    for (var m of [datedMain(panel), admission]) {
      m.calendarTickTimer.running = false
      m.calendarRefreshTimer.running = false
      m.calendarNowMs = Date.parse(payload.observedAt)
      m.calendarClock = payload
    }
  }
  // Refresh fixtures own their clock too: never accept future publications,
  // borrow a same-day stale helper, or race a pending real helper callback.
  function historyClockReady(label) {
    var payload = JSON.parse(clockPayloads.text())[label]
    for (var m of [datedMain(panel), admission]) {
      if (!m || !m.calendarClock || !m.calendarClockStreamDone || !m.calendarClockExitDone
          || m.calendarRequestTimezone !== m.calendarTimezone) return false
    }
    for (var m of [datedMain(panel), admission]) {
      if (JSON.stringify(m.calendarClock) !== JSON.stringify(payload)
          || m.calendarNowMs !== Date.parse(payload.observedAt)) {
        setDatedClock(payload)
        return false // settle the actual reactive view before taking any snapshot
      }
    }
    return true
  }
  function datedClockFlow() {
    if (Quickshell.env("ALACARCHY_DATED_FLOW") !== "1") return true
    if (root.datedFlowStage >= 10) return true
    if (root.datedFlowWait++ % 3 !== 2) return false
    var main = datedMain(panel), payload = JSON.parse(clockPayloads.text())
    if (root.datedFlowStage === 0) root.datedUndatedIdentity = main.undatedTokenTotals
    var labels = ["initial", "fresh-limit", "expired", "stopped", "pre-midnight", "midnight-expired", "next-day", "next-week", "output", "restored"]
    var snap = historySnapshot(labels[root.datedFlowStage])
    var bar = namedItem(panel, "recordedTokenBar")
    snap.bar = bar ? bar.labelText : null
    snap.clock = main.calendarClock
    snap.now = main.calendarNowMs
    snap.sameUndated = main.undatedTokenTotals === root.datedUndatedIdentity
    root.datedClockProof = root.datedClockProof.concat([snap])
    var stage = root.datedFlowStage++
    if (stage === 0) main.calendarNowMs = Date.parse(main.calendarClock.expiresAt) - 1
    else if (stage === 1) main.calendarNowMs = Date.parse(main.calendarClock.expiresAt)
    else if (stage === 2) main.calendarClock = null
    else if (stage === 3) setDatedClock(payload.preMidnight)
    else if (stage === 4) main.calendarNowMs = Date.parse(payload.nextDay.observedAt)
    else if (stage === 5) setDatedClock(payload.nextDay)
    else if (stage === 6) clickHistory("historyGrain_week")
    else if (stage === 7) clickHistory("historyMetric_output_tokens")
    else if (stage === 8) {
      setDatedClock(payload.initial)
      clickHistory("historyMetric_total_tokens")
      clickHistory("historyGrain_day")
    }
    return false
  }
  property int liveProofStage: 0
  property var liveProof: ({})
  property var recentRateItem: null
  property int republishStage: 0
  property int republishSettle: 0
  property string republishParsed: ""
  property var republishEditHead: null
  property var republishEditField: null
  property int republishEditStage: 0
  property var republishEditProof: ({})
  property var republishSections: []
  property var republishDescendants: []
  property int republishAdded: 0
  property int republishRemoved: 0
  property real republishScroll: 0
  property var republishProof: []
  property double republishClock: 0
  property var republishHover: null
  property bool republishHoverSent: false
  property var republishTick: null
  property int republishFrames: 0
  property int republishBlankFrames: 0
  property int republishBadGeometryFrames: 0
  FixtureTest.TestEvent { id: fixturePointer } // sends only to this offscreen Qt window
  Connections {
    target: panel.providerSections
    function onItemAdded(index, item) { if (root.republishStage > 0) root.republishAdded++ }
    function onItemRemoved(index, item) { if (root.republishStage > 0) root.republishRemoved++ }
  }
  FrameAnimation {
    running: Quickshell.env("ALACARCHY_LIVE_CASE") === "republish" && root.republishStage > 0 && root.republishStage <= 5
    onTriggered: {
      root.republishFrames++
      if (panel.blankSlate || !panel.providerSections || panel.providerSections.count !== panel.providers.length)
        root.republishBlankFrames++
      var items = root.republishItems()
      for (var i=0; i<items.length; i++)
        if (items[i].visible && (!(items[i].width > 0) || !(items[i].height > 0))) root.republishBadGeometryFrames++
    }
  }
  property var sequenceProbeRecords: []
  property int quotaProofStage: 0
  property double quotaOriginalClock: 0
  property var quotaProof: ({})
  property int accountingStage: 0
  property var accountingStages: []
  property var historyProof: []
  property int historyAction: -1
  property int historyRefreshStage: 0
  property bool historyPartialCombinedWait: false
  property string historyLayoutSettle: ""
  property string historySettle: ""
  property var historyRefreshProof: []
  property bool historyFlowDone: false
  property int historyResizeStage: 0
  property real historyResizeWidth: 0
  property string historyResizeSettle: ""
  property var historyResizeProof: []
  property int stimulusStage: 0
  property string stimulusSettle: ""
  property var historyRendererProof: []
  readonly property var stimulusWidths: [380, 248, 232]
  readonly property var historyActions: ["output_tokens", "input_tokens", "prompt_tokens",
    "cache_read_tokens", "cache_write_tokens", "reasoning_tokens", "api_call_count", "total_tokens",
    "week", "month", "year", "day"]
  Agents.Main { id: admission; settings: ({ refreshIntervalSec: 3600 }) }
  QtObject {
    id: fixtureBar
    property bool vertical: Quickshell.env("ALACARCHY_BAR_CASE") === "vertical"
    property bool foregroundAnimationEnabled: false
    property int barSize: 32
    property string position: "top"
    property color barForeground: "white"
    property color foreground: "white"
    property color urgent: "red"
    property string fontFamily: "sans-serif"
    property var activePopout: null
    function requestPopout(owner) { activePopout = owner }
    function releasePopout(owner) { activePopout = null }
    function showTooltip(owner, text) {}
    function hideTooltip(owner) {}
    function run(command) { console.log("AGENTS_QML_UNEXPECTED_RUN " + command) }
  }
  FloatingWindow {
    visible: true
    implicitWidth: 800
    implicitHeight: Quickshell.env("ALACARCHY_LIVE_CASE") === "republish" ? 1400 : 80
    Agents.Panel { id: panel; bar: fixtureBar; settings: ({ refreshIntervalSec: 3600 }) }
    // Explicit Qt modelData contrast, separate from the stable-ID provider map.
    Item {
      visible: false
      Repeater {
        id: sequenceProbe
        model: root.sequenceProbeRecords
        Item { required property var modelData }
      }
    }
  }
  FloatingWindow {
    visible: Quickshell.env("ALACARCHY_HISTORY_CASE") === "controls"
    implicitWidth: 400
    implicitHeight: 900
    Loader { id: calendarStimulus }
  }
  // Every visible text the panel shows, in item order.
  function shownTexts(item, out) {
    if (!item || item.visible === false) return out
    if (typeof item.text === "string" && item.text !== "" && item.wrapMode !== undefined) out.push(item.text)
    var kids = item.children || []
    for (var i = 0; i < kids.length; i++) shownTexts(kids[i], out)
    return out
  }
  function plainTexts(item) {
    if (item.textFormat !== undefined && item.textFormat !== Text.PlainText) return false
    var kids = item.children || []
    for (var i = 0; i < kids.length; i++) if (!plainTexts(kids[i])) return false
    return true
  }
  function namedItem(item, name, visibleOnly) {
    if (!item || (visibleOnly && item.visible === false)) return null
    if (item.objectName === name) return item
    var kids = item.children || []
    for (var i = 0; i < kids.length; i++) {
      var found = namedItem(kids[i], name, visibleOnly)
      if (found) return found
    }
    return null
  }
  function accountingAgent(item) {
    if (!item) return null
    if (item.agentId === "hermes-token-accounting") return item
    var kids = item.children || []
    for (var i = 0; i < kids.length; i++) {
      var found = accountingAgent(kids[i])
      if (found) return found
    }
    return null
  }
  function textGeometry(item, out) {
    if (!item || item.visible === false) return out
    if (typeof item.text === "string" && item.text !== "" && item.wrapMode !== undefined)
      out.push({text:item.text, plainText:item.textFormat === Text.PlainText,
                width:item.width, height:item.height})
    var kids = item.children || []
    for (var i = 0; i < kids.length; i++) textGeometry(kids[i], out)
    return out
  }
  function cumulativeProof() {
    var agent = accountingAgent(admission)
    var header = namedItem(panel, "recordedTokenHeader", true)
    var bar = namedItem(panel, "recordedTokenBar", true)
    var heading = header && header.children.length ? header.children[0] : null
    return {path:agent ? agent.path : null, record:agent ? agent.record : null,
            parsedText:agent ? agent.parsedText : null, totals:panel.recordedTotals,
            texts:shownTexts(header, []), plainText:plainTexts(header),
            textGeometry:textGeometry(header, []), coverage:bar ? bar.tooltipText : null,
            headerCoverage:heading ? heading.ToolTip.text : null,
            geometry:header ? {visible:header.visible, width:header.width, height:header.height} : null}
  }
  function recordAccountingStage(label) {
    root.accountingStages = root.accountingStages.concat([{stage:label, proof:cumulativeProof()}])
    console.log("ACCOUNTING_NATIVE_READY " + label)
  }
  function historyBars(item, out) {
    if (!item || item.visible === false) return out
    if (item.objectName === "observedHistoryBar") out.push({width:item.width, height:item.height,
      x:item.x, y:item.y, parentWidth:item.parent.width, parentHeight:item.parent.height,
      value:item.observedValue === undefined ? null : item.observedValue,
      source:item.sourceId === undefined ? null : item.sourceId,
      key:item.bucketKey === undefined ? null : item.bucketKey})
    var kids = item.children || []
    for (var i=0;i<kids.length;i++) historyBars(kids[i],out)
    return out
  }
  // Any instantiated source-picker remnant, visible or not.
  function selectorNames(item, out) {
    if (!item) return out
    if (String(item.objectName).indexOf("historySource") === 0) out.push(item.objectName)
    var kids = item.children || []
    for (var i = 0; i < kids.length; i++) selectorNames(kids[i], out)
    return out
  }
  // Extent of the chart and of every bucket, bar and label in it, mapped into
  // the panel body's viewport. Labels report their natural width, the shown
  // text and the unbroken label it must preserve.
  function historyFit() {
    var chart = namedItem(panel, "historyChart"), body = namedItem(panel, "recordedTokenBody")
    var popup = namedItem(panel, "keyboardPanel")
    if (!chart || !body || !chart.visible) return null
    return chartFit(chart, body, popup ? popup.contentWidth : null)
  }
  function chartFit(chart, frame, contentWidth) {
    var origin = chart.mapToItem(frame, 0, 0), items = []
    function walk(item) {
      if (!item || item.visible === false) return
      var name = String(item.objectName)
      if (["historyBucket", "observedHistoryBar", "historyDate", "historySeriesValue"].indexOf(name) >= 0) {
        var p = item.mapToItem(frame, 0, 0), label = name === "historyDate" || name === "historySeriesValue"
        items.push({name:name, left:p.x, right:p.x + item.width, width:item.width, top:p.y, bottom:p.y + item.height,
          natural:label ? item.implicitWidth : null, text:label ? item.text : null,
          fullText:label && item.fullText !== undefined ? item.fullText : null,
          key:item.bucketKey === undefined ? null : item.bucketKey})
      }
      var kids = item.children || []
      for (var i = 0; i < kids.length; i++) walk(kids[i])
    }
    walk(chart)
    return {popup:contentWidth, viewport:frame.width, left:origin.x, right:origin.x + chart.width,
      scrolls:chart.contentWidth !== undefined && chart.contentWidth > chart.width + 0.5,
      flickable:chart.flickableDirection !== undefined && chart.flickableDirection !== Flickable.VerticalFlick, items:items}
  }
  // Unbroken history labels in item order; shown text may add fitted breaks.
  function historyLabels(item, out) {
    if (!item || item.visible === false) return out
    var name = String(item.objectName)
    if (name === "historyDate" || name === "historySeriesValue") out.push(item.fullText === undefined ? null : item.fullText)
    var kids = item.children || []
    for (var i = 0; i < kids.length; i++) historyLabels(kids[i], out)
    return out
  }
  function historyVerticalTail(chart) {
    if (!chart || chart.contentY === undefined) return null
    var oldY = chart.contentY
    chart.contentY = Math.max(0, chart.contentHeight - chart.height)
    var bars = []
    function visit(item) {
      if (!item || item.visible === false) return
      if (item.objectName === "observedHistoryBar") {
        var p = item.mapToItem(chart, 0, 0)
        bars.push({key:item.bucketKey, top:p.y, bottom:p.y + item.height})
      }
      var kids = item.children || []
      for (var i=0; i<kids.length; i++) visit(kids[i])
    }
    visit(chart)
    var result = {direction:chart.flickableDirection === Flickable.VerticalFlick,
      maxHeight:chart.maximumViewportHeight, height:chart.height, contentHeight:chart.contentHeight,
      contentY:chart.contentY, interactive:chart.interactive,
      last:bars.length ? bars[bars.length - 1] : null, count:bars.length}
    chart.contentY = oldY
    return result
  }
  function historySnapshot(label) {
    var section = namedItem(panel, "observedHistory")
    var chart = namedItem(panel, "historyChart")
    var body = namedItem(panel, "recordedTokenBody")
    var header = namedItem(panel, "recordedTokenHeader")
    return {stage:label, view:panel.historyView, selectors:selectorNames(panel, []),
      section:section ? {width:section.width, height:section.height} : null,
      cards:body ? providerGeometry(panel, body, []) : [],
      fitting:body && header ? {popup:header.parent.height, body:body.height, content:body.contentHeight,
        scrolling:body.interactive, header:header.height, bodyY:body.y} : null,
      observation:panel.observedHistory.data ? panel.observedHistory.data.observedAt : null,
      state:panel.observedHistory.state,
      texts:shownTexts(section, []), labels:historyLabels(section, []), plainText:plainTexts(section),
      bars:historyBars(section, []), totals:panel.recordedTotals,
      pinned:shownTexts(namedItem(panel, "recordedTokenHeader"), []),
      chart:chart ? {width:chart.width, height:chart.height, contentWidth:chart.contentWidth === undefined ? null : chart.contentWidth,
                    interactive:chart.interactive === undefined ? null : chart.interactive} : null,
      fit:historyFit(), vertical:historyVerticalTail(chart)}
  }
  function clickHistory(name) {
    var control = namedItem(panel, name, true)
    if (!control || !control.focusable || !control.activeFocusOnTab || control.width <= 0 || control.height <= 0)
      throw new Error("native history control unavailable: " + name)
    control.clicked() // the real Ui.Button signal, never assign the tested state
  }
  function historyInteractions() {
    if (!("historyView" in panel) || panel.historyView.sources.length !== 2) return false
    if (root.historyAction < 0 && !historyLayoutSettled()) return false
    var label = root.historyAction < 0 ? "initial" : root.historyActions[root.historyAction]
    root.historyProof = root.historyProof.concat([historySnapshot(label)])
    root.historyAction++
    if (root.historyAction >= root.historyActions.length) return true
    var action = root.historyActions[root.historyAction]
    if (["day", "week", "month", "year"].indexOf(action) >= 0) clickHistory("historyGrain_" + action)
    else clickHistory("historyMetric_" + action)
    return false // wait for instantiated Text/Repeater and layout bindings
  }
  function recordHistoryRefresh(label) {
    root.historyRefreshProof = root.historyRefreshProof.concat([historySnapshot(label)])
    console.log("HISTORY_NATIVE_READY " + label)
  }
  // FileView data rebuilds the chart delegates between timer ticks and the
  // Grid positions them on its next polish: a data-driven snapshot waits for
  // the same view laid out identically on two ticks, never unpositioned
  // delegates. (A click runs inside a tick, so its polish precedes the next.)
  function historyLayoutSettled() {
    var shape = JSON.stringify([panel.historyView, historyFit()])
    var settled = shape === root.historyLayoutSettle
    root.historyLayoutSettle = shape
    return settled
  }
  function historyRefresh() {
    var data = panel.observedHistory.data
    if (root.historyRefreshStage > 0 && !historyLayoutSettled()) return false
    if (root.historyRefreshStage === 0) {
      clickHistory("historyMetric_output_tokens")
      clickHistory("historyGrain_week")
    } else if (root.historyRefreshStage === 1) recordHistoryRefresh("configured")
    else if (root.historyRefreshStage === 2) {
      if (!data || data.observedAt !== "2024-02-29T15:10:00Z") return false
      if (!historyClockReady("updated")) return false
      recordHistoryRefresh("refreshed")
    } else if (root.historyRefreshStage === 3) {
      if (!data || data.sources.length !== 1) return false
      if (!historyClockReady("removed")) return false
      recordHistoryRefresh("removed")
    } else if (root.historyRefreshStage === 4) {
      if (!data || data.sources.length !== 3) return false
      if (!historyClockReady("refreshed")) return false
      recordHistoryRefresh("expanded")
    } else if (root.historyRefreshStage === 5) {
      if (panel.observedHistory.state !== "malformed") return false
      recordHistoryRefresh("invalid")
      clickHistory("historyGrain_day")
    } else if (root.historyRefreshStage === 6) {
      if (!data || data.observedAt !== "2024-03-01T15:05:00Z"
          || !data.sources.some(function(s) { return !s.present })) return false
      if (!historyClockReady("refreshed")) return false
      if (!root.historyPartialCombinedWait) {
        root.historyRefreshProof = root.historyRefreshProof.concat([historySnapshot("partial")])
        root.historyPartialCombinedWait = true
        return false
      }
      recordHistoryRefresh("partial-combined")
    } else if (root.historyRefreshStage === 7) {
      if (!data || !data.sources.every(function(s) { return s.present })) return false
      if (!historyClockReady("refreshed")) return false
      recordHistoryRefresh("zero")
      clickHistory("historyMetric_total_tokens")
    } else {
      if (!data || data.observedAt !== "2024-02-29T15:05:00Z") return false
      if (!historyClockReady("initial")) return false
      recordHistoryRefresh("restored")
      return true
    }
    root.historyRefreshStage++
    return false
  }
  // Two equal timer ticks with every bar instantiated: delegates, positioners
  // and bindings have settled before a large-inventory snapshot.
  function historySettled(sources) {
    var section = namedItem(panel, "observedHistory"), chart = namedItem(panel, "historyChart")
    var bars = historyBars(section, []).length
    var shape = JSON.stringify([section ? section.height : -1, chart ? chart.height : -1, bars])
    var stable = shape === root.historySettle && bars === 7 * (panel.historyView.mode === "multiple" ? sources : 1)
    root.historySettle = shape
    return stable
  }
  // Large-inventory control: the same compact chart for 313 and 2 sources.
  function historyInventory() {
    var data = panel.observedHistory.data
    var stage = root.historyRefreshStage
    var sources = stage < 1 ? 313 : 2
    if (!data || data.sources.length !== sources) { root.historySettle = ""; return false }
    if (!historySettled(sources)) return false
    root.historyRefreshProof = root.historyRefreshProof.concat(
      [historySnapshot(["inventory", "two"][stage])])
    if (stage === 0) console.log("HISTORY_NATIVE_READY inventory")
    if (stage === 1) return true
    root.historySettle = ""
    root.historyRefreshStage++
    return false
  }
  // Narrow the popup as a smaller screen would, close and reopen the lazy
  // panel, then restore its width; each width must lay the same chart out.
  // Done after stage 3: later ticks (the controls stimulus) pass through.
  function historyResize() {
    if (root.historyResizeStage > 3) return true
    var popup = namedItem(panel, "keyboardPanel")
    if (!popup) throw new Error("fixture popup stand-in unavailable")
    var stage = root.historyResizeStage
    if (stage === 0 || stage === 2) {
      if (stage === 0) root.historyResizeWidth = popup.availableCardWidth
      popup.availableCardWidth = stage === 0 ? 232 : root.historyResizeWidth
      panel.close()
      panel.open()
      root.historyResizeSettle = ""
      root.historyResizeStage++
      return false
    }
    var shape = JSON.stringify(historyFit())
    var settled = shape === root.historyResizeSettle
    root.historyResizeSettle = shape
    if (!settled) return false
    root.historyResizeProof = root.historyResizeProof.concat(
      [historySnapshot(stage === 1 ? "narrowed-reopened" : "restored-reopened")])
    root.historyResizeStage++
    return stage === 3
  }
  // Renderer-only synthetic stimulus (controls case): the exact generated
  // chart template, fed 7-, 8- and 14-bucket views at each popup width and
  // resized in place. Never production data; see renderer_stimuli().
  function historyStimulus() {
    if (calendarStimulus.status === Loader.Null) {
      calendarStimulus.setSource("calendar/HistoryCalendarStimulus.qml", {host: panel, usage: datedMain(panel)})
      return false
    }
    if (calendarStimulus.status === Loader.Error) throw new Error("renderer stimulus failed to load")
    var scene = calendarStimulus.item
    if (!scene) return false
    var widths = root.stimulusWidths, stage = root.stimulusStage
    if (stage >= scene.stimuli.length * widths.length) return true
    var stimulus = scene.stimuli[Math.floor(stage / widths.length)], width = widths[stage % widths.length]
    if (scene.stimulus !== stimulus.name || scene.contentWidth !== width) {
      scene.show(stimulus.name, width)
      root.stimulusSettle = ""
      return false
    }
    var chart = namedItem(scene, "historyChart"), section = namedItem(scene, "observedHistory")
    var fit = chart && chart.visible ? chartFit(chart, scene, scene.contentWidth) : null
    var shape = JSON.stringify(fit)
    var settled = shape === root.stimulusSettle
    root.stimulusSettle = shape
    if (!settled) return false
    root.historyRendererProof = root.historyRendererProof.concat([{stage:stimulus.name + "@" + width,
      name:stimulus.name, width:width, view:scene.historyView, texts:shownTexts(section, []),
      labels:historyLabels(section, []), plainText:plainTexts(section), bars:historyBars(section, []),
      fit:fit, vertical:historyVerticalTail(chart)}])
    root.stimulusStage++
    return false
  }
  function providerGeometry(item, body, out) {
    if (!item || item.visible === false) return out
    if (typeof item.providerIndex === "number" && item.provider && item.windows !== undefined) {
      var p = item.mapToItem(body, 0, 0)
      out.push({id: item.provider.providerId, y: p.y, height: item.height})
    }
    var children = item.children || []
    for (var i = 0; i < children.length; i++) providerGeometry(children[i], body, out)
    return out
  }
  function credentialVisibleProof(item, body, out) {
    if (!item || item.visible === false) return out
    if (typeof item.providerIndex === "number" && item.provider && item.provider.credentialProvider) {
      var point = item.mapToItem(body, 0, 0)
      body.contentY = Math.max(0, Math.min(body.contentHeight - body.height, point.y + body.contentY))
      point = item.mapToItem(body, 0, 0)
      out.push({id:item.provider.providerId, width:item.width, height:item.height,
                inViewport:point.y < body.height && point.y + item.height > 0,
                textGeometry:textGeometry(item, []), meters:shownMeters(item, [])})
    }
    var kids = item.children || []
    for (var i=0; i<kids.length; i++) credentialVisibleProof(kids[i], body, out)
    return out
  }
  // Inspect the instantiated provider delegate, not a plain JS provider copy:
  // Repeater modelData converts nested limits to a Qt sequence.
  function shownMeters(item, out) {
    if (!item || item.visible === false) return out
    if (typeof item.value === "number" && item.thickness !== undefined && item.markers !== undefined)
      out.push({value: item.value, width: item.width, height: item.height})
    var kids = item.children || []
    for (var i = 0; i < kids.length; i++) shownMeters(kids[i], out)
    return out
  }
  function quotaSection(item, id) {
    if (!item) return null
    if (typeof item.providerIndex === "number" && item.provider && item.provider.providerId === id
        && item.windows !== undefined) return item
    var kids = item.children || []
    for (var i=0; i<kids.length; i++) {
      var found = quotaSection(kids[i], id)
      if (found) return found
    }
    return null
  }
  function quotaClockProof(p) {
    var section = quotaSection(panel, p.providerId)
    if (!section) return false
    var reset = Date.parse(p.limits[0].resetsAt) + 1
    if (root.quotaProofStage === 0) {
      if (root.sequenceProbeRecords.length === 0) {
        root.sequenceProbeRecords = [p, panel.providers.filter(function(row) { return row.providerId === "grok" })[0]]
        return false
      }
      if (sequenceProbe.count !== 2 || !sequenceProbe.itemAt(0) || !sequenceProbe.itemAt(1)) return false
      root.quotaOriginalClock = panel.nowMs
      panel.nowMs = p.limitsFetchedAt
      root.quotaProofStage = 1
      return false
    }
    if (root.quotaProofStage === 1) {
      if (panel.nowMs !== p.limitsFetchedAt) { panel.nowMs = p.limitsFetchedAt; return false }
      var meters = shownMeters(section, [])
      if (meters.length < 2 || meters[0].width <= 0 || meters[0].height <= 0) return false
      var contrast = []
      for (var i = 0; i < sequenceProbe.count; i++) {
        var seq = sequenceProbe.itemAt(i).modelData
        var windows = panel.limitWindows(seq)
        contrast.push({providerId:seq.providerId, limitsIsArray:Array.isArray(seq.limits),
          windows:JSON.parse(JSON.stringify(windows)),
          legacyArrayOnlyCount:Array.isArray(seq.limits) ? windows.length : 0})
      }
      root.quotaProof = {before:{visible:section.visible, windows:JSON.parse(JSON.stringify(section.windows)), meters:meters,
        limitsIsArray:Array.isArray(section.provider.limits)}, rawSample:p.limits[0].percent, qtSequenceContrast:contrast}
      panel.nowMs = reset
      root.quotaProofStage = 2
      return false
    }
    if (root.quotaProofStage === 2) {
      if (panel.nowMs !== reset) { panel.nowMs = reset; return false }
      var status = namedItem(section, "hermesQuotaStatus", true)
      root.quotaProof = Object.assign({}, root.quotaProof, {after:{
        visible:section.visible, windows:JSON.parse(JSON.stringify(section.windows)), meters:shownMeters(section, []),
        status:status ? {text:status.text, plain:status.textFormat === Text.PlainText,
          width:status.width, height:status.height} : null},
        originalSample:p.limits[0].percent, originalFetchedAt:p.limitsFetchedAt,
        reset:reset, ageMs:reset-p.limitsFetchedAt})
      panel.nowMs = root.quotaOriginalClock
      root.quotaProofStage = 3
      return false // restore ordinary native state and allow a layout tick
    }
    return true
  }
  function shownGrokSections(item, out) {
    if (!item || item.visible === false) return out
    if (typeof item.providerIndex === "number" && item.provider && item.provider.providerId === "grok"
        && item.windows !== undefined) {
      out.push({texts: shownTexts(item, []), meters: shownMeters(item, []),
                windows: item.windows.length, limitsIsArray: Array.isArray(item.provider.limits)})
    }
    var kids = item.children || []
    for (var i = 0; i < kids.length; i++) shownGrokSections(kids[i], out)
    return out
  }
  function accountEditHead(item, id) {
    if (!item) return null
    if (typeof item.startRename === "function" && item.account && item.account.id === id) return item
    var kids = item.children || []
    for (var i = 0; i < kids.length; i++) { var found = accountEditHead(kids[i], id); if (found) return found }
    return null
  }
  function accountEditField(item) {
    if (!item) return null
    if (typeof item.selectAll === "function" && typeof item.deselect === "function" && typeof item.text === "string") return item
    var kids = item.children || []
    for (var i = 0; i < kids.length; i++) { var found = accountEditField(kids[i]); if (found) return found }
    return null
  }
  function republishEditFlow() {
    if (Quickshell.env("ALACARCHY_EDIT_PROOF") !== "1" || root.republishEditStage >= 2) return true
    if (root.republishEditStage === 0) {
      if (!root.republishEditHead || !root.republishEditHead.editing || root.republishEditField.text !== "UNSAVED_SYNTHETIC_EDIT")
        throw new Error("Native account editor lost its original draft before replacement")
      root.republishEditStage = 1
      console.log("EDIT_NATIVE_READY replace")
      return false
    }
    var replacement = accountEditHead(panel, "native-new")
    if (replacement && !root.republishEditProof.replacementObserved) {
      root.republishEditProof = {replacementObserved:true}
      console.log("EDIT_REPLACEMENT_STATE " + JSON.stringify({id:replacement.account.id,
        oldAbsent:accountEditHead(panel,"native-2") === null,
        editing:replacement.editing, globalRenaming:panel.renaming}))
    }
    if (!replacement || panel.renaming) return false
    root.republishEditProof = {replacementId:replacement.account.id,
      ownerId:replacement.owner.providerId, replacementEditing:replacement.editing,
      oldAbsent:accountEditHead(panel,"native-2") === null,
      draftNotReused:accountEditField(replacement).text !== "UNSAVED_SYNTHETIC_EDIT",
      globalRenaming:panel.renaming}
    root.republishEditStage = 2
    console.log("EDIT_NATIVE_DONE")
    return false
  }
  function republishItems() {
    var out = [], repeater = panel.providerSections
    if (repeater) for (var i=0; i<repeater.count; i++) out.push(repeater.itemAt(i))
    return out
  }
  function republishTree(item, out) {
    if (!item || item.visible === false) return out
    out.push(item)
    var kids = item.children || []
    for (var i=0; i<kids.length; i++) republishTree(kids[i], out)
    return out
  }
  function republishLimit(item) {
    if (!item || item.visible === false) return null
    if (item.resetMs !== undefined && item.window && item.scoped !== undefined) return item
    var kids = item.children || []
    for (var i=0; i<kids.length; i++) {
      var found = republishLimit(kids[i])
      if (found) return found
    }
    return null
  }
  function republishHoverHandler(item) {
    var data = item ? item.data || [] : []
    for (var i=0; i<data.length; i++)
      if (typeof data[i].hovered === "boolean" && data[i].point !== undefined) return data[i]
    return null
  }
  function republishSnapshot(label) {
    var items = republishItems(), descendants = []
    for (var i=0; i<items.length; i++) republishTree(items[i], descendants)
    var localSection = quotaSection(panel, "local"), grokSection = quotaSection(panel, "grok")
    var limit = republishLimit(grokSection), hover = republishHoverHandler(limit)
    var body = namedItem(panel, "recordedTokenBody"), rates = namedItem(localSection, "localTaskRates", true)
    var snapshot = {stage:label, ids:items.map(function(s) { return s && s.provider ? s.provider.providerId : null }),
      indices:items.map(function(s) { return s ? s.providerIndex : -1 }),
      sameSections:items.length === root.republishSections.length && items.every(function(s,i) { return s === root.republishSections[i] }),
      edit:Quickshell.env("ALACARCHY_EDIT_PROOF") === "1" ? {
        sameHead:accountEditHead(panel,"native-2") === root.republishEditHead,
        id:root.republishEditHead.account.id, owner:root.republishEditHead.owner.providerId,
        editing:root.republishEditHead.editing, draft:root.republishEditField.text} : null,
      descendantCount:descendants.length, retainedDescendants:root.republishDescendants.filter(function(s) { return descendants.indexOf(s) >= 0 }).length,
      added:root.republishAdded, removed:root.republishRemoved,
      scroll:body.contentY, expectedScroll:root.republishScroll,
      hover:!!hover && hover.hovered, sameHover:!!hover && hover === root.republishHover,
      geometry:items.filter(function(s) { return s && s.visible }).map(function(s) { return {id:s.provider.providerId,width:s.width,height:s.height,y:s.y} }),
      localText:shownTexts(localSection, []), claudeMeters:shownMeters(quotaSection(panel, "claude"), []),
      totals:JSON.parse(JSON.stringify(panel.recordedTotals)),
      rates:rates ? {text:rates.text,width:rates.width,height:rates.height,plain:rates.textFormat === Text.PlainText} : null,
      localDays:localSection ? JSON.parse(JSON.stringify(localSection.provider.recentDays)) : [],
      clock:panel.nowMs, tick:root.republishTick, frames:root.republishFrames,blankFrames:root.republishBlankFrames,badGeometryFrames:root.republishBadGeometryFrames}
    root.republishProof = root.republishProof.concat([snapshot])
    console.log("REPUBLISH_OBSERVED " + JSON.stringify(snapshot))
    console.log("REPUBLISH_NATIVE_READY " + label)
  }
  function republishFlow(local) {
    if (root.republishStage === 9) return true // sticky completion before final refresh/quit
    var body = namedItem(panel, "recordedTokenBody"), items = republishItems()
    if (!body || !items.length || items.some(function(s) { return !s || (s.visible && (!(s.width > 0) || !(s.height > 0))) })) return false
    var ids = items.map(function(s) { return s.provider.providerId })
    var expected = JSON.parse(Quickshell.env("ALACARCHY_EXPECTED_IDS"))
    var claude = panel.providers.filter(function(p) { return p.providerId === "claude" })[0]
    if (root.republishStage === 0) {
      var limit = republishLimit(quotaSection(panel, "grok")), handler = republishHoverHandler(limit)
      if (!limit || !handler || !body.interactive) return false
      if (!root.republishHoverSent) {
        body.contentY = Math.max(1, Math.min(body.contentHeight-body.height, limit.mapToItem(body,0,0).y+body.contentY-25))
        root.republishScroll = body.contentY
        root.republishHoverSent = fixturePointer.mouseMove(limit, limit.width/2, limit.height/2, 0, Qt.NoButton, Qt.NoModifier)
        return false
      }
      if (!handler.hovered) return false
      if (++root.republishSettle < 3) return false
      if (Quickshell.env("ALACARCHY_EDIT_PROOF") === "1" && !root.republishEditHead) {
        var head = accountEditHead(panel, "native-2")
        if (!head) return false
        root.republishEditHead = head
        root.republishEditField = accountEditField(head)
        if (!root.republishEditField) throw new Error("Native rename TextField not found")
        head.startRename()
        root.republishEditField.text = "UNSAVED_SYNTHETIC_EDIT"
        return false // settle actual focus/edit state before the first snapshot
      }
      root.republishSections = items
      var descendants = []
      for (var i=0; i<items.length; i++) republishTree(items[i], descendants)
      root.republishDescendants = descendants
      root.republishHover = handler
      root.republishClock = panel.nowMs
      root.republishParsed = String(local.localLive.observedAt)
      root.republishStage = 1
      root.republishSettle = 0
      republishSnapshot("baseline")
      return false
    }
    var labels = ["", "clock", "idle-1", "idle-2", "counter", "value", "reordered", "disabled", "restored"]
    var stage = root.republishStage
    if (stage === 1 && panel.nowMs === root.republishClock) {
      // The native display clock repeats every 30 s, past this 12 s budget:
      // emit that running Timer's own tick once instead of waiting for it.
      if (root.republishTick === null) {
        var clocks = [], owned = panel.resources || []
        for (var c=0; c<owned.length; c++)
          if (owned[c] && owned[c].interval === 30000 && owned[c].repeat === true && owned[c].running === true
              && typeof owned[c].triggered === "function") clocks.push(owned[c])
        root.republishTick = {timers:clocks.length, interval:clocks.length ? clocks[0].interval : null, before:panel.nowMs}
        if (clocks.length === 1) clocks[0].triggered()
        else console.log("REPUBLISH_NATIVE_CLOCK_UNRESOLVED " + clocks.length)
      }
      return false
    }
    if (stage >= 2 && stage <= 4 && String(local.localLive.observedAt) === root.republishParsed) return false
    if (stage === 4 && local.todayTotalTokens !== 3042) return false
    if (stage === 5 && claude.accounts[0].limits[0].percent !== 0.5) return false
    if (stage === 6 && ids.join() !== expected.slice().reverse().join()) return false
    if (stage === 7 && ids.join() !== expected.slice().reverse().filter(function(id) { return id !== "grok" }).join()) return false
    if (stage === 8 && (ids.join() !== expected.join() || local.todayTotalTokens !== 2042 || claude.accounts[0].limits[0].percent !== 0.25)) return false
    // Permit both binding delivery and native layout polish before observing.
    if (++root.republishSettle < 3) return false
    root.republishSettle = 0
    root.republishParsed = String(local.localLive.observedAt)
    republishSnapshot(labels[stage])
    if (stage === 6) panel.settings = ({refreshIntervalSec:3600,providers:{grok:{enabled:false}}})
    if (stage === 7) panel.settings = ({refreshIntervalSec:3600})
    root.republishStage++
    return false
  }
  Timer {
    interval: 50; repeat: true; running: !root.reported
    onTriggered: {
      var list = panel.providers
      var count = Number(Quickshell.env("ALACARCHY_PROVIDER_COUNT"))
      var republishing = Quickshell.env("ALACARCHY_LIVE_CASE") === "republish"
      if (!list || (list.length !== count && !(republishing && root.republishStage > 0)) || admission.enabledProviders.length !== count) return
      if (!configureDatedClock()) return
      if (!root.panelOpenedForProof) {
        root.preOpenHeaderAbsent = namedItem(panel, "recordedTokenHeader") === null
        root.panelOpenedForProof = true
        panel.open()
        return // build the native lazy content and allow one layout tick
      }
      var local = null
      for (var i = 0; i < list.length; i++) if (list[i].providerId === "local") local = list[i]
      var texts = shownTexts(panel, [])
      if (!local || texts.indexOf("Local") < 0) return // sections are still being built
      // Wait for the actual version-matched refresh stub, not an arbitrary sleep.
      commandLog.reload()
      if (commandLog.text().indexOf("omarchy-agent-usage-update") < 0) return
      // Finish clock characterization before one-shot history refresh handshakes;
      // waiting afterward would re-emit their final stage on every layout tick.
      var quotaId = JSON.parse(Quickshell.env("ALACARCHY_CODEX_IDS"))[0]
      var quotaProvider = list.filter(function(p) { return p.providerId === quotaId })[0]
      if (!root.quotaClockProof(quotaProvider)) return
      if (!datedClockFlow()) return
      if (republishing && !root.republishFlow(local)) return
      if (republishing && !root.republishEditFlow()) return
      var ac = Quickshell.env("ALACARCHY_ACCOUNTING_CASE")
      var ap = cumulativeProof()
      var ah = ap.record && ap.record.sources.length ? ap.record.sources[0].windows.history : null
      var target = Number(Quickshell.env("ALACARCHY_ACCOUNTING_VALUE"))
      var nativeSources = Number(Quickshell.env("ALACARCHY_NATIVE_CLOUD_SOURCES"))
      // Capture only after both the native FileView and the instantiated Text
      // bindings have caught up; never assign a mock record/totals to Main.
      if (ap.texts.indexOf(panel.recordedCloudTotalsText()) < 0
          || ap.texts.indexOf(panel.recordedCombinedTotalsText()) < 0) return
      if (ac === "reactive") {
        if (root.accountingStage === 0) {
          if (ap.record !== null || ap.totals.cloud.history.sources !== nativeSources) return
          recordAccountingStage("wrong-path")
          root.accountingStage = 1
          return
        }
        if (root.accountingStage === 1) {
          if (ah !== target || ap.totals.cloud.history.known !== 2 || ap.totals.cloud.history.sources !== nativeSources + 1) return
          recordAccountingStage("observed")
          root.accountingStage = 2
          return
        }
        if (root.accountingStage === 2) {
          if (ah !== 0 || ap.totals.cloud.history.known !== 2 || ap.totals.cloud.history.sources !== nativeSources + 1) return
          recordAccountingStage("zero")
          root.accountingStage = 3
          return
        }
        if (root.accountingStage === 3) {
          if (ap.record !== null || ap.totals.cloud.history.sources !== nativeSources) return
          recordAccountingStage("removed")
          root.accountingStage = 4
        }
      } else if (ac !== "missing" && ac !== "wrong-path") {
        if (ah !== target || ap.totals.cloud.history.sources !== nativeSources + 1) return
      }
      var rates = namedItem(panel, "localTaskRates", true)
      if (Quickshell.env("ALACARCHY_LIVE_CASE") !== "none") {
        if (!rates || !rates.visible || rates.width <= 0 || rates.height <= 0) return
        if (root.liveProofStage === 0) {
          root.liveProof = {first: rates.text, plainText: rates.textFormat === Text.PlainText,
                            width: rates.width, height: rates.height}
          root.liveProofStage = 1
          root.recentRateItem = rates
          console.log("LOCAL_LIVE_INITIAL " + JSON.stringify(root.liveProof))
        }
        if (Quickshell.env("ALACARCHY_LIVE_CASE") === "metrics") {
          if (Quickshell.env("ALACARCHY_RECENT_METRICS") === "1" && !root.liveProof.recent) {
            var localNow = list.filter(function(p) { return p.providerId === "local" })[0]
            var liveNow = localNow ? localNow.localLive : null
            var sampleNow = liveNow && liveNow.models && liveNow.models.length ? liveNow.models[0].engineMetrics : null
            // The label no longer changes for retained values: observe the empty-phase publication itself.
            if (!sampleNow || sampleNow.promptTokPerSec !== null || sampleNow.generationTokPerSec !== null) return
            if (rates.text.indexOf("prompt 1234.5 tok/s · generation 32.25 tok/s") < 0) return
            if (rates !== root.recentRateItem) throw new Error("Local rate Text rebuilt during empty-phase publication")
            root.liveProof = Object.assign({}, root.liveProof, {recent: rates.text, retainedItem: true})
          }
          if (rates.text.indexOf("stale") < 0) return
          root.liveProof = Object.assign({}, root.liveProof, {fullyExpired: rates.text})
        }
        if (Quickshell.env("ALACARCHY_LIVE_CASE") === "rates") {
          if (root.liveProofStage === 1) {
            if (rates.text.indexOf("prompt — · generation 2 tok/s") < 0) return
            root.liveProof = Object.assign({}, root.liveProof, {phaseExpired: rates.text})
            root.liveProofStage = 2
            console.log("LOCAL_LIVE_PHASE_EXPIRED " + JSON.stringify(root.liveProof))
          }
          if (rates.text.indexOf("stale") < 0) return
          root.liveProof = Object.assign({}, root.liveProof, {fullyExpired: rates.text})
        }
      }
      if (Quickshell.env("ALACARCHY_HISTORY_CASE") === "controls" && !root.historyFlowDone) {
        if (root.historyAction < root.historyActions.length && !historyInteractions()) return
        if (!historyRefresh()) return
        root.historyFlowDone = true
      }
      if (Quickshell.env("ALACARCHY_HISTORY_CASE") === "inventory" && !root.historyFlowDone) {
        if (!historyInventory()) return
        root.historyFlowDone = true
      }
      if (["controls", "inventory"].indexOf(Quickshell.env("ALACARCHY_HISTORY_CASE")) >= 0 && !historyResize()) return
      if (Quickshell.env("ALACARCHY_HISTORY_CASE") === "controls" && !historyStimulus()) return
      root.reported = true
      var header = namedItem(panel, "recordedTokenHeader")
      panel.close()
      panel.open()
      var headerRetainedOnReopen = header !== null && namedItem(panel, "recordedTokenHeader") === header
      var tokenBar = namedItem(panel, "recordedTokenBar")
      var body = namedItem(panel, "recordedTokenBody")
      var pinnedBefore = header ? shownTexts(header, []) : []
      panel.shownPhrase = "Mostly rotating sentinel"
      panel.phraseIndex++
      if (body) body.contentY = Math.max(0, body.contentHeight - body.height)
      var credentialProof = body ? credentialVisibleProof(panel, body, []) : []
      var hermesIds = JSON.parse(Quickshell.env("ALACARCHY_HERMES_IDS"))
      var codexIds = JSON.parse(Quickshell.env("ALACARCHY_CODEX_IDS"))
      var h1 = list.filter(function(p) { return p.providerId === codexIds[0] })[0]
      var h2 = list.filter(function(p) { return p.providerId === codexIds[1] })[0]
      var syntheticAccount = {id:"synthetic", active:false, usageStatusText:"Waiting for auth"}
      var guarded = {providerId:h2.providerId, readOnly:true, accounts:[syntheticAccount, {id:"second"}], accountSwitch:{mode:"manual"}}
      panel.signInAgain(guarded, syntheticAccount)
      panel.useAccount(guarded, syntheticAccount)
      panel.renameAccount(guarded, syntheticAccount, "SYNTHETIC")
      panel.setSwitchMode(guarded, "auto")
      admission.syncEnabled = true
      admission.syncDir = admission.home + "/synthetic-sync"
      admission.aggregateData = {providers:{codex:{providerName:"SYNC NATIVE CODEX", todayPrompts:99},
                                            local:{providerName:"SYNC LOCAL", todayPrompts:99},
                                            "codex-hermes-3":{providerName:"SYNC GHOST CODEX", todayPrompts:99}}}
      admission.syncRevision++
      var snapshot = admission.localSnapshot()
      admission.agents = [] // Exercise remote-only slots, not just local-id suppression.
      var remote = JSON.parse(JSON.stringify(snapshot))
      remote.deviceId = "synthetic-remote"
      remote.providers[Quickshell.env("ALACARCHY_GHOST_ID")] = {providerName:"SYNC GHOST", todayPrompts:1}
      remote.providers.codex = {providerName:"SYNC NATIVE", todayPrompts:1}
      remote.providers.local = {providerName:"REMOTE LOCAL", todayPrompts:1}
      for (var h=0;h<hermesIds.length;h++) remote.providers[hermesIds[h]].providerName = "WRONG SYNC LABEL"
      admission.aggregateData = admission.aggregateSnapshots([remote])
      admission.syncRevision++
      console.log("AGENTS_QML_RESULT " + JSON.stringify({
        hermes: list.filter(function(p) { return hermesIds.indexOf(p.providerId) >= 0 }),
        credentialVisibleProof: credentialProof,
        credentialInventory: admission.hermesInventory,
        quotaClockProof: root.quotaProof,
        recordedTotals: panel.recordedTotals,
        datedClock: {now:datedMain(panel).calendarNowMs, payload:datedMain(panel).calendarClock},
        datedClockProof: root.datedClockProof,
        accounting: accountingAgent(admission) ? {path:accountingAgent(admission).path,
                    record:accountingAgent(admission).record, parsedText:accountingAgent(admission).parsedText} : null,
        tokenHelp: tokenBar ? tokenBar.tooltipText : null,
        accountingProof: cumulativeProof(),
        accountingStages: root.accountingStages,
        history: panel.observedHistory,
        historyProof: root.historyProof,
        historyRefreshProof: root.historyRefreshProof,
        historyResizeProof: root.historyResizeProof,
        historyRendererProof: root.historyRendererProof,
        historyControls: {view: "historyView" in panel ? panel.historyView : null,
          metric: namedItem(panel,"historyMetric_output_tokens") !== null,
          sources: namedItem(panel,"historySourcesControl") !== null,
          selectors: selectorNames(panel, []),
          mode: namedItem(panel,"historyMode_multiple") !== null,
          grain: namedItem(panel,"historyGrain_week") !== null},
        historyTexts: shownTexts(namedItem(panel, "observedHistory"), []),
        historyPlainText: plainTexts(namedItem(panel, "observedHistory")),
        historyHelp: namedItem(panel, "observedHistoryHelp") ? {tab:namedItem(panel, "observedHistoryHelp").activeFocusOnTab,
                      text:namedItem(panel, "observedHistoryHelp").helpText} : null,
        historyBars: historyBars(namedItem(panel, "observedHistory"), []),
        historyGeometry: namedItem(panel, "observedHistory") ? {width:namedItem(panel, "observedHistory").width, height:namedItem(panel, "observedHistory").height} : null,
        preOpenHeaderAbsent: root.preOpenHeaderAbsent,
        headerRetainedOnReopen: headerRetainedOnReopen,
        pinnedBefore: pinnedBefore,
        pinnedAfter: header ? shownTexts(header, []) : [],
        pinnedPlainText: header ? plainTexts(header) : false,
        pinnedGeometry: header ? {visible:header.visible, y:header.y, width:header.width, height:header.height,
                                  bodyY:body ? body.y : -1, scrollY:body ? body.contentY : -1,
                                  bodyHeight:body ? body.height : -1, bodyContent:body ? body.contentHeight : -1,
                                  popupHeight:header.parent.height, inset:0} : null,
        providerGeometry: body ? providerGeometry(panel, body, []) : [],
        barTokens: tokenBar ? {text:tokenBar.labelText, glyph:tokenBar.text, vertical:tokenBar.vertical,
                              paintedWidth:tokenBar.glyphPaintedWidth, texts:shownTexts(tokenBar, []),
                              visible:tokenBar.visible, width:tokenBar.width, height:tokenBar.height} : null,
        signInHermes: panel.needsSignIn({providerId:h2.providerId, readOnly:true, usageStatusText:"Waiting for auth"}),
        accountsHermes: panel.providerAccounts(guarded),
        switchHermes: h2.accountSwitch,
        normalSignIn: panel.needsSignIn({providerId:"claude", usageStatusText:"Waiting for auth"}),
        normalAccounts: panel.providerAccounts({providerId:"claude", accounts:[syntheticAccount, {id:"second"}]}),
        iconDark: panel.iconCandidatesForProvider(h2, Qt.rgba(0,0,0,1)),
        iconLight: panel.iconCandidatesForProvider(h2, Qt.rgba(1,1,1,1)),
        keyRows: panel.keyRows,
        providerIds: list.map(function(p) { return p.providerId }),
        syncedIds: admission.enabledProviders.map(function(p) { return p.providerId }),
        localSnapshot: snapshot,
        syncOnlyHermes: admission.enabledProviders.filter(function(p) { return hermesIds.indexOf(p.providerId) >= 0 }),
        texts: texts,
        ids: list.map(function(p) { return p.providerId }),
        local: local,
        grok: list.filter(function(p) { return p.providerId === "grok" })[0],
        grokWindows: panel.limitWindows(list.filter(function(p) { return p.providerId === "grok" })[0]),
        grokRenderedSections: shownGrokSections(panel, []),
        grokText: panel.grokUsageText(list.filter(function(p) { return p.providerId === "grok" })[0]),
        allModelsText: panel.localRouterText({ todayTokensByModel: {"model-1": 1, "model-2": 2, "model-3": 3, "model-4": 4, "model-5": 5, "model-6": 6} }),
        localText: local ? panel.localRouterText(local) : null,
        localRateProof: root.liveProof,
        republishProof: root.republishProof,
        republishEditProof: root.republishEditProof,
        localRateText: rates ? rates.text : null,
        localTrouble: local ? (typeof panel.usageNote === "function" ? panel.usageNote(local) : panel.otherTrouble(local)) : null,
        subscriptionTrouble: typeof panel.usageNote === "function" ? panel.usageNote({ providerId: "claude", usageStatusText: "Endpoint down" }) : panel.otherTrouble({ providerId: "claude", usageStatusText: "Endpoint down" }),
        accountTrouble: typeof panel.usageNote === "function" ? panel.usageNote({ usageStatusText: "Endpoint down" }) : panel.otherTrouble({ usageStatusText: "Endpoint down" }),
        signIn: panel.needsSignIn(local),
        alarming: panel.alarming,
        blankSlate: panel.blankSlate,
        phrases: panel.summaryPhrases,
        unknownTodayText: panel.localRouterText({ todayTotalTokens: 0, recentDays: [{ messageCount: 7, recorded: true }, { messageCount: null, recorded: false }] }),
        emptyText: panel.localRouterText({ providerId: "local", usageStatusText: "Activation pending · not collecting",
                                           recentDays: [{ date: "2026-01-01", messageCount: null, recorded: false }], todayTotalTokens: 0 })
      }))
      console.log("AGENTS_QML_QT_QUIT")
      Qt.quit()
    }
  }
}
''')
    native_argv = [str(QS), '-p', str(fixture), '--no-color']
    # Native handshakes before each real history-file replacement.
    HISTORY_STAGES = {'controls': ('configured','refreshed','removed','expanded','invalid','partial-combined','zero','restored'),
                      'inventory': ('inventory',)}
    reactive_records = {}
    REPUBLISH_STAGES = ('baseline', 'clock', 'idle-1', 'idle-2', 'counter', 'value', 'reordered', 'disabled', 'restored')
    try:
        if not RECENT_METRICS and ACCOUNTING_CASE != 'reactive' and history_case not in ('controls', 'inventory') and LIVE_CASE != 'republish':
            # Metrics must outlive its10-second independent expiry assertion plus setup.
            run = subprocess.run(native_argv, env=env, text=True, capture_output=True, timeout=15 if LIVE_CASE == 'metrics' else 8)
        else:
            # Wait on native observations before each real CLI/file mutation.
            # No fixed-delay writer, background worker, or fabricated QML ledger.
            process = subprocess.Popen(native_argv, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if LIVE_CASE == 'republish' or RECENT_METRICS:
                assert process.stdout is not None
            else:
                assert process.stdout is not None and history_fixture is not None
            chunks, pending, actions = [], b'', []
            deadline = time.monotonic() + (20 if RECENT_METRICS else 12)
            try:
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout, selectors.EVENT_READ)
                    while selector.get_map():
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise subprocess.TimeoutExpired(native_argv, 12, output=b''.join(chunks))
                        for key, _ in selector.select(min(0.2, remaining)):
                            chunk = os.read(key.fd, 65536)
                            if not chunk:
                                selector.unregister(key.fileobj)
                                continue
                            chunks.append(chunk)
                            pending += chunk
                            while b'\n' in pending:
                                line, pending = pending.split(b'\n', 1)
                                if RECENT_METRICS and b'LOCAL_LIVE_INITIAL ' in line:
                                    assert not actions, actions
                                    at = dt.datetime.now(dt.timezone.utc).timestamp()
                                    missing = json.loads(json.dumps(record))
                                    live = missing['localLive']
                                    live['observedAt'] = at
                                    for row in live['models']:
                                        row['observedAt'] = at
                                        row['engineMetrics'].update(observedAt=at, promptTokPerSec=None, generationTokPerSec=None)
                                    collector.publish(ledger_dir/'agents/local.json', missing)
                                    actions.append('recent-empty')
                                    continue
                                if b'EDIT_NATIVE_READY replace' in line:
                                    assert LIVE_CASE == 'republish' and env['ALACARCHY_EDIT_PROOF'] == '1'
                                    replacement = json.loads(json.dumps(subscription))
                                    replacement['accounts'][1]['id'] = 'native-new'
                                    replacement['accounts'][1]['label'] = 'Synthetic replacement'
                                    collector.publish(usage/'claude.json', replacement)
                                    assert json.loads((usage/'claude.json').read_text()) == replacement
                                    print('EDIT_IDENTITY_MUTATION same-count account replacement', flush=True)
                                    continue
                                if b'REPUBLISH_NATIVE_READY ' in line:
                                    assert LIVE_CASE == 'republish'
                                    stage = line.split(b'REPUBLISH_NATIVE_READY ', 1)[1].decode().strip()
                                    assert len(actions) < len(REPUBLISH_STAGES) and stage == REPUBLISH_STAGES[len(actions)], (stage, actions)
                                    actions.append(stage)
                                    publications = []
                                    if stage in ('clock', 'idle-1', 'idle-2', 'disabled'):
                                        assert counted_record is not None
                                        published = json.loads(json.dumps(counted_record if stage == 'idle-2' else record))
                                        published['localLive'] = idle_live(dt.datetime.now(dt.timezone.utc).timestamp())
                                        target = ledger_dir/'agents/local.json'
                                        collector.publish(target, published)
                                        assert json.loads(target.read_text()) == published
                                        publications.append({'path':str(target), 'record':published})
                                    if stage in ('counter', 'disabled'):
                                        published = json.loads(json.dumps(subscription))
                                        if stage == 'counter':
                                            for account in published['accounts']:
                                                account['limits'][0]['percent'] = 0.5
                                        collector.publish(usage/'claude.json', published)
                                        assert json.loads((usage/'claude.json').read_text()) == published
                                        publications.append({'path':str(usage/'claude.json'), 'record':published})
                                    if stage in ('value', 'disabled'):
                                        order = list(reversed(expected_ids)) if stage == 'value' else expected_ids
                                        collector.publish(usage.parent/'order.json', order)
                                        assert json.loads((usage.parent/'order.json').read_text()) == order
                                        publications.append({'path':str(usage.parent/'order.json'), 'record':order})
                                    print('REPUBLISH_MUTATION ' + json.dumps({'after_native_stage':stage,
                                        'publications':publications}), flush=True)
                                    continue
                                if b'HISTORY_NATIVE_READY ' in line:
                                    assert control_fixture is not None and history_path is not None
                                    stage = line.split(b'HISTORY_NATIVE_READY ', 1)[1].decode().strip()
                                    expected_stages = HISTORY_STAGES[history_case]
                                    assert stage == expected_stages[len(actions)], (stage, actions)
                                    actions.append(stage)
                                    case = {'configured':'updated', 'refreshed':'removed', 'removed':'expanded',
                                            'invalid':'partial', 'partial-combined':'zero', 'zero':'initial',
                                            'inventory':'initial'}.get(stage)
                                    if case:
                                        data = control_fixture.projected(case)
                                        temporary = history_path.with_suffix('.next')
                                        with temporary.open('w') as stream:
                                            json.dump(data, stream)
                                            stream.flush()
                                            os.fsync(stream.fileno())
                                        temporary.replace(history_path)
                                    elif stage == 'expanded':
                                        history_path.write_text('{bad')
                                    print('HISTORY_MUTATION ' + json.dumps({'after_native_stage':stage,
                                          'projection_case':case,
                                          'path':str(history_path),
                                          'sha256':hashlib.sha256(history_path.read_bytes()).hexdigest()}), flush=True)
                                    continue
                                if b'ACCOUNTING_NATIVE_READY ' not in line:
                                    continue
                                stage = line.split(b'ACCOUNTING_NATIVE_READY ', 1)[1].decode().strip()
                                expected_stage = ('wrong-path', 'observed', 'zero', 'removed')[len(actions)]
                                assert stage == expected_stage, (stage, actions)
                                actions.append(stage)
                                if stage in ('wrong-path', 'observed'):
                                    assert history_fixture is not None
                                    path = history_fixture.fixture(state,
                                        'observed' if stage == 'wrong-path' else 'cumulative-zero',
                                        provider='anthropic', include_local=True, history=False)
                                    reactive_records['observed' if stage == 'wrong-path' else 'zero'] = json.loads(path.read_text())
                                elif stage == 'zero':
                                    cumulative_path.unlink()
                                print('ACCOUNTING_MUTATION ' + json.dumps({'after_native_stage':stage,
                                    'cumulative_exists':cumulative_path.exists(),
                                    'cumulative_sha256':hashlib.sha256(cumulative_path.read_bytes()).hexdigest()
                                        if cumulative_path.exists() else None}), flush=True)
                run = subprocess.CompletedProcess(native_argv, process.wait(timeout=1),
                                                  b''.join(chunks).decode(), '')
                if RECENT_METRICS:
                    assert actions == ['recent-empty'], actions
                elif LIVE_CASE == 'republish':
                    assert actions == list(REPUBLISH_STAGES), actions
                elif history_case in HISTORY_STAGES:
                    assert actions == list(HISTORY_STAGES[history_case]), actions
                else:
                    assert actions == ['wrong-path', 'observed', 'zero', 'removed'], actions
                    expected_accounting = None  # Final native checkpoint follows deletion.
            except BaseException:
                print(b''.join(chunks).decode(errors='replace'), flush=True)
                process.kill()
                try:
                    rest = process.communicate(timeout=1)[0]
                except subprocess.TimeoutExpired:
                    raise AssertionError('native fixture cleanup unresolved; SIGKILL requested but child not reaped: pid=' + str(process.pid)) from None
                print(rest.decode(errors='replace'), flush=True)
                raise
            finally:
                process.stdout.close()
        output = run.stdout + run.stderr
        assert run.returncode == 0, (run.returncode, output)
    except subprocess.TimeoutExpired as exc:
        output = (exc.stdout or b'').decode() + (exc.stderr or b'').decode()
        if os.environ.get('ALACARCHY_PROOF_ARTIFACTS'):
            failed = Path(os.environ['ALACARCHY_PROOF_ARTIFACTS'])
            failed.mkdir(parents=True, exist_ok=True)
            (failed/'native-output.log').write_text(output)
            (failed/'native-child.json').write_text(json.dumps({'argv':native_argv, 'exit':None, 'timeout':True, 'killed_reaped':True, 'environment':env}, indent=2))
            for path in (calls, clock_calls, unexpected):
                if path.exists(): shutil.copyfile(path, failed/path.name)
            shutil.copytree(fixture/'plugin', failed/'generated')
        raise AssertionError('native fixture timeout; child killed/reaped\n' + output) from None
    print('NATIVE_CHILD ' + json.dumps({'argv':native_argv, 'exit':run.returncode,
                                     'environment':env,
                                     'generated_hashes':{n:hashlib.sha256((fixture/'plugin'/n).read_bytes()).hexdigest() for n in ('Main.qml','Panel.qml','Agent.qml','History.js')},
                                     'harness_sha256':hashlib.sha256((fixture/'shell.qml').read_bytes()).hexdigest(),
                                     'stub_hashes':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in bin_dir.iterdir() if p.is_file() and not p.is_symlink()},
                                     'native_fingerprint':stager.fingerprint(STOCK)}))
    print(output)
    artifacts = os.environ.get('ALACARCHY_PROOF_ARTIFACTS')
    if artifacts:
        artifacts = Path(artifacts)
        artifacts.mkdir(parents=True, exist_ok=True)
        (artifacts/'native-output.log').write_text(output)
        (artifacts/'native-child.json').write_text(json.dumps({'argv':native_argv, 'exit':run.returncode, 'environment':env}, indent=2))
        for path in (calls, clock_calls, unexpected):
            if path.exists(): shutil.copyfile(path, artifacts/path.name)
        shutil.copytree(fixture/'plugin', artifacts/'generated')
    marker = [line for line in output.splitlines() if 'AGENTS_QML_RESULT ' in line]
    assert len(marker) == 1, 'the patched panel never produced its provider list'
    assert output.count('AGENTS_QML_QT_QUIT') == 1, 'native fixture did not reach Qt.quit()'
    result = json.loads(marker[0].split('AGENTS_QML_RESULT ', 1)[1])
    if artifacts:
        (artifacts/'native-result.json').write_text(json.dumps(result, indent=2))
    if os.environ.get('ALACARCHY_DATED_FLOW') == '1':
        stages = {p['stage']:p for p in result['datedClockProof']}
        assert list(stages) == ['initial', 'fresh-limit', 'expired', 'stopped', 'pre-midnight',
                                'midnight-expired', 'next-day', 'next-week', 'output', 'restored']
        assert all(p['sameUndated'] for p in stages.values()), 'clock ticks must not rescan legacy model histories'
        assert all(p['totals']['total']['history'] == 3215 for p in stages.values()), 'clock expiry must preserve the synthetic undated accounting total'
        for label in ('initial', 'fresh-limit', 'pre-midnight', 'restored'):
            p = stages[label]
            assert p['totals']['total']['today'] == p['view']['bars'][-1]['value'] == 2060, label
            assert p['totals']['cloud']['today']['value'] == 18 and p['totals']['local']['today'] == 2042
            assert p['bar'] == 'Tokens Today 2.1K' and 'Total · today 2.1K · week 2.1K · recorded 3.2K' in p['pinned']
        for label in ('expired', 'stopped', 'midnight-expired'):
            p = stages[label]
            assert p['totals']['total']['today'] is None and p['view']['bars'] == [], label
            assert p['bar'] == 'Tokens Today —' and 'Total · today — · week — · recorded 3.2K' in p['pinned']
        p = stages['next-day']
        assert p['view']['bars'][-1]['key'] == '2024-03-01' and p['view']['bars'][-1]['value'] is None
        assert p['totals']['total']['today'] is None and p['totals']['total']['week'] == 2060
        assert p['totals']['total']['month'] is None and p['totals']['total']['year'] == 2060
        assert stages['next-week']['view']['bars'][-1]['value'] == 2060
        output_view = stages['output']['view']
        assert output_view['metric'] == 'output_tokens' and output_view['bars'][-1]['value'] == 5
        assert output_view['bars'][-1]['local']['sources'] == 0 and stages['output']['totals']['total']['week'] == 2060
        assert any(t.endswith('5 tokens') for t in stages['output']['texts'])
        print('PASS native dated clock: 10 observed stages; expiry/stopped helper/midnight/renewal; Cloud-only output units; unchanged legacy snapshot identity')
    if os.environ.get('ALACARCHY_DATED_UI') == '1':
        view = result['historyControls']['view']
        assert view, 'dated calendar view must be present'
        # The current-clock bar only renders when a dated roll-up report exists
        # (history file present and valid).  Negative cases (history missing or
        # invalid) legitimately have no bars; the header must stay unknown.
        if view.get('bars'):
            last = view['bars'][-1]
            assert result['recordedTotals']['total']['today'] == last['value'], (
                'dated header/calendar mismatch', result['recordedTotals']['total']['today'], last)
    if LIVE_CASE == 'republish':
        proof = result['republishProof']
        assert [p['stage'] for p in proof] == list(REPUBLISH_STAGES), proof
        stable = proof[:6]
        for p in stable:
            assert p['sameSections'] and p['added'] == p['removed'] == 0, \
                'Republish replaced native provider delegates: ' + json.dumps(p)
            assert p['retainedDescendants'] == p['descendantCount'] == proof[0]['descendantCount'], \
                'Republish/clock replaced nested native delegates: ' + json.dumps(p)
            assert p['hover'] and p['sameHover'], 'Native hover lost across republish: ' + json.dumps(p)
            assert p['scroll'] == p['expectedScroll'] > 0, p
            assert p['ids'] == expected_ids and p['indices'] == list(range(len(expected_ids))), p
            assert all(g['width'] > 0 and g['height'] > 0 for g in p['geometry']), p
            assert p['blankFrames'] == p['badGeometryFrames'] == 0, p
            assert p['rates'] and p['rates']['plain'] and p['rates']['width'] > 0 and p['rates']['height'] > 0, p
            assert 'tok/s' not in p['rates']['text'] and 'No loaded model' in p['rates']['text'], p
            assert len(p['localDays']) == 7 and p['localDays'][0]['messageCount'] is None and not p['localDays'][0]['recorded'], p
        assert stable[-1]['frames'] > 0 and proof[1]['clock'] > proof[0]['clock'], proof
        assert [p['totals']['local']['today'] for p in stable] == [2042]*4 + [3042]*2
        assert 'Today: local/model <b>A</b> 2.0K · model-b 1.0K' in proof[4]['localText'], proof[4]
        assert [m['value'] for m in proof[4]['claudeMeters']] == [0.25]*3
        assert [m['value'] for m in proof[5]['claudeMeters']] == [0.5]*3, proof[5]
        for p, ids in ((proof[6], list(reversed(expected_ids))),
                       (proof[7], [i for i in reversed(expected_ids) if i != 'grok']),
                       (proof[8], expected_ids)):
            assert p['ids'] == ids and p['indices'] == list(range(len(ids))), p
            assert p['totals']['local']['today'] == (2042 if p['stage'] == 'restored' else 3042), p
        if os.environ.get('ALACARCHY_EDIT_PROOF') == '1':
            for p in proof:
                edit = p['edit']
                assert edit == {'sameHead':True, 'id':'native-2', 'owner':'claude',
                                'editing':True, 'draft':'UNSAVED_SYNTHETIC_EDIT'}, (p['stage'], edit)
            assert result['republishEditProof'] == {'replacementId':'native-new', 'ownerId':'claude',
                'replacementEditing':False, 'oldAbsent':True, 'draftNotReused':True, 'globalRenaming':False}, result['republishEditProof']
            print('PASS native edit identity: draft/target retained through value refresh and reorder; same-count replacement cancels without dispatch')
        print('PASS native republish: clock, repeated timestamp-only publish, counter and quota in-place, nested identities, frame geometry, hover/scroll, reorder/membership and restored values')
    if ACCOUNTING_CASE in ('observed', 'baseline', 'zero'):
        assert result['accounting'] and result['accounting']['record'], \
            'actual CLI cumulative usage.json did not reach the native Agent/FileView'
    for bad in ('Failed to load', 'TypeError', 'ReferenceError', 'is not defined', 'is not a type', 'AGENTS_QML_UNEXPECTED_RUN'):
        assert bad not in output, bad
    # Local supplies both native surface variants; missing-mark warnings are regressions.
    plugin_noise = [line for line in output.splitlines() if '@plugin/' in line]
    assert 'Failed to start IPC server' not in output, 'fixture socket path is too long'
    assert not plugin_noise, plugin_noise
    allowed_platform_warnings = ('$HYPRLAND_INSTANCE_SIGNATURE is unset', 'This plugin does not support setting window masks')
    assert not [line for line in output.splitlines() if 'WARN' in line and not any(note in line for note in allowed_platform_warnings)], output

    # Discovery: the subscription from the native directory, Local from its own
    # record. The stray usage/local.json is ignored, not adopted.
    assert result['ids'] == expected_ids, result['ids']
    assert result['syncedIds'] == result['ids'], result['syncedIds']
    assert not any(ident in result['localSnapshot']['providers'] for ident in ('codex', 'local', ghost_id))
    assert [p['providerId'] for p in result['syncOnlyHermes']] == hermes_ids
    assert all(p['readOnly'] is True and p['accountSwitch'] is None and p['accounts'] == [] for p in result['syncOnlyHermes'])
    assert [p['providerName'] for p in result['syncOnlyHermes']] == [hermes_names[i] for i in hermes_ids]
    admitted = {p['providerId']:p for p in result['hermes']}
    h1, h2 = [admitted[i] for i in codex_ids[:2]]
    assert h1['readOnly'] is True and h2['readOnly'] is True
    assert h1['providerName'] == hermes_names[codex_ids[0]] and h2['providerName'] == hermes_names[codex_ids[1]]
    assert result['credentialInventory'] == inventory
    visible_credentials = {p['id']:p for p in result['credentialVisibleProof']}
    fresh_codex_ids = {i for i in codex_ids if admitted[i]['limitsStale'] is False}
    assert set(visible_credentials) == fresh_codex_ids, 'Only Hermes cards with rendered bars belong in the view'
    display_names = {}
    ordinals = {}
    for entry in inventory['entries']:
        provider = entry['provider']
        ordinals[provider] = ordinals.get(provider, 0) + 1
        display_names[entry['id']] = bridge.PROVIDER_LABELS[provider] + ' ' + str(ordinals[provider]) + ' (Hermes)'
    assert len(codex_ids) >= 3
    for ident, proof in visible_credentials.items():
        assert proof['width'] > 0 and proof['height'] > 0 and proof['inViewport'], proof
        labels = [t for t in proof['textGeometry'] if t['text'] == display_names[ident]]
        assert labels and all(t['plainText'] and t['width'] > 0 and t['height'] > 0 for t in labels), proof
    assert not any('PRIVATE_' in str(p) for p in result['hermes'])
    qp = result['quotaClockProof']
    assert qp['before']['windows'][0]['percent'] == qp['rawSample'] == 0.54, qp
    keyed_provider_records = 'readonly property var modelData: root.providerRowsByKey[providerKey]' in (fixture/'plugin/Panel.qml').read_text()
    assert qp['before']['limitsIsArray'] is keyed_provider_records, qp
    sequence_contrast = qp['qtSequenceContrast']
    assert len(sequence_contrast) == 2 and [len(p['windows']) for p in sequence_contrast] == [2, 1], sequence_contrast
    assert all(p['limitsIsArray'] is False and p['legacyArrayOnlyCount'] == 0 for p in sequence_contrast), sequence_contrast
    assert sequence_contrast[0]['windows'][0]['percent'] == 0.54 and sequence_contrast[1]['windows'][0]['percent'] == 0.75
    assert len(qp['before']['meters']) == 2 and qp['before']['meters'][0]['value'] == 0.54, qp
    assert qp['before']['meters'][0]['width'] > 0 and qp['before']['meters'][0]['height'] > 0, qp
    assert 0 < qp['ageMs'] < 120000, qp  # Reset, not the independently tested maximum age gate.
    assert qp['after']['windows'] == [] and qp['after']['meters'] == [], \
        'Unobserved Hermes reset must remove meters, not create a visible zero: ' + repr(qp)
    assert qp['before']['visible'] and not qp['after']['visible'], qp
    assert qp['after']['status'] is None, 'Expired Hermes card must disappear, not leave a stale-heading row'
    assert any(m['value'] == 0 and m['width'] > 0 and m['height'] > 0
               for proof in visible_credentials.values() for m in proof['meters']), 'Measured zero must remain a real visible meter'
    assert qp['originalSample'] == qp['rawSample'] and qp['originalFetchedAt'] == h1['limitsFetchedAt'], qp
    assert h1['limitsStale'] is False and h2['limitsStale'] is True
    assert h1['limitsFetchedAt'] == h2['limitsFetchedAt'] == bridge_fixture.NOW*1000
    assert h2['usageStatusText'] == bridge.REASONS['expired']
    assert result['signInHermes'] is False and result['accountsHermes'] == []
    assert result['switchHermes'] is None
    assert result['normalSignIn'] is True and len(result['normalAccounts']) == 2
    assert result['iconDark'][0].endswith('/assets/codex.svg')
    assert result['iconLight'][0].endswith('/assets/codex-light.svg')
    for row in result['keyRows']:
        for target in row:
            if target['kind'] == 'providerSignin':
                assert not result['providerIds'][target['index']].startswith('codex-hermes-')
    assert not any('--reauth' in line for line in (calls.read_text().splitlines() if calls.exists() else [])), 'Hermes auth command executed'
    assert result['grok']['providerName'] == 'Grok' and result['grok']['tierLabel'] == 'X Premium'
    assert result['grok']['limits'] == native_grok['limits'], result['grok']['limits']
    assert result['grok']['todayTotalTokens'] == 0
    assert len(result['grokWindows']) == 1 and result['grokWindows'][0]['percent'] == 0.75
    sections = result['grokRenderedSections']
    assert len(sections) == 1, 'Expected one instantiated Grok provider section: ' + repr(sections)
    rendered = sections[0]
    assert rendered['limitsIsArray'] is keyed_provider_records, 'Actual provider binding kind drifted; Qt-sequence contrast is independently exercised above'
    assert rendered['windows'] == 1 and rendered['texts'].count('Weekly') == 1, \
        'Fresh Grok Weekly row missing after Qt Repeater/modelData: ' + repr(rendered)
    assert len(rendered['meters']) == 1, 'Grok Weekly meter missing: ' + repr(rendered)
    meter = rendered['meters'][0]
    assert meter['value'] == result['grokWindows'][0]['percent'] and meter['width'] > 0 and meter['height'] > 0, meter
    assert result['grokText'] == '', 'A fresh quota-only Grok card needs no extra disclaimer'
    assert 'Today: model-6 6 · model-5 5 · model-4 4 · model-3 3 · model-2 2 · model-1 1' in result['allModelsText'], result['allModelsText']
    assert '+2 more' not in result['allModelsText']
    assert result['unknownTodayText'] == 'No token data', result['unknownTodayText']
    local = result['local']
    assert local['providerName'] == 'Local' and local['tierLabel'] == 'llama.cpp router'
    assert local['todayTotalTokens'] == record['todayTotalTokens'], local['todayTotalTokens']
    assert local['syncEnabled'] is False
    assert len(local['recentDays']) == 7 and local['recentDays'][0]['recorded'] is False

    # Display: status is information for Local, still trouble for everyone else.
    assert result['localTrouble'] == '' and result['signIn'] is False
    assert result['subscriptionTrouble'] == 'Endpoint down' and result['accountTrouble'] == 'Endpoint down'
    assert result['alarming'] is False and result['blankSlate'] is False
    expected_local = {'sampled': 'Today: local/model <b>A</b> 2.0K · model-b 42',
                      'unrecorded': 'Activation pending', 'metrics-disabled': 'Metrics disabled',
                      'no-model': 'No loaded model'}
    assert result['localText'] == expected_local[LOCAL_CASE], result['localText']
    # Rendering: the Local section shows its own lines, and its caveat text is
    # never drawn as (urgent) trouble the way a subscription's would be.
    texts = result['texts']
    assert texts.count('Grok') == 1, texts
    if result['grokText']: assert texts.count(result['grokText']) == 1, texts
    assert texts.count(result['localText']) == 1, texts
    assert 'Local' in texts and 'Llama.cpp router' in texts and 'Claude Code' in texts and texts.count('· max') == 3, texts
    assert 'Grok' in texts and 'X Premium' in texts, texts
    assert all(display_names[i] in texts for i in fresh_codex_ids), texts
    assert all(hermes_names[i] not in texts for i in hermes_ids), texts
    assert bridge.REASONS['expired'] not in texts, texts
    # Unsupported credential records stay admitted for tracking, not as empty cards.
    unsupported_ids = [i for i in hermes_ids if i not in codex_ids]
    assert unsupported_ids, 'fixture must retain an unsupported Hermes credential record'
    for ident in unsupported_ids:
        assert admitted[ident]['usageStatusText'] == admitted[ident]['authHelpText'] == bridge.REASONS['unsupported']
        assert ident not in visible_credentials, ident
    assert not any('Quota unavailable' in t for t in texts), texts
    assert not any('CODEX SENTINEL' in t or 'CUSTOM GROK' in t for t in texts), texts
    assert local['authHelpText'] and local['authHelpText'] not in texts, texts
    assert not any('STRAY' in text or 'Stray' in text for text in texts), texts
    assert result['emptyText'] == 'Activation pending', result['emptyText']

    if LIVE_CASE != 'none':
        proof = result['localRateProof']
        assert proof['plainText'] and proof['width'] > 0 and proof['height'] > 0, proof
        if LIVE_CASE == 'rates':
            assert 'task/model <b>A</b> · prompt 4 tok/s · generation 2 tok/s' in proof['first'], proof
            assert 'prompt — · generation 2 tok/s' in proof['phaseExpired'], proof
            assert 'stale' in proof['fullyExpired'] and 'tok/s' not in proof['fullyExpired'], proof
        elif LIVE_CASE == 'metrics':
            assert 'Engine averages' in proof['first'] and 'prompt 1234.5 tok/s' in proof['first'], proof
            assert 'generation 32.25 tok/s' in proof['first'] and '1 processing (metrics)' in proof['first'], proof
            assert 'Sampled wall-clock' not in proof['first'] and 'not current-task rates' in proof['first'], proof
            if RECENT_METRICS:
                assert proof['retainedItem'] and proof['recent'].count('Engine averages') == 1, proof
                assert 'Recent' not in proof['recent'] and proof['first'].count('Engine averages') == 1, proof
                assert 'prompt 1234.5 tok/s' in proof['recent'] and 'generation 32.25 tok/s' in proof['recent'], proof
            assert 'stale' in proof['fullyExpired'] and 'tok/s' not in proof['fullyExpired'], proof
        elif LIVE_CASE == 'expired':
            assert 'stale' in result['localRateText'] and 'tok/s' not in result['localRateText']
        elif LIVE_CASE == 'malformed':
            assert 'unavailable' in result['localRateText'] and 'tok/s' not in result['localRateText']
        else:
            assert 'tok/s' not in result['localRateText'] and '0 tok/s' not in result['localRateText']
        print('PASS native ' + ('engine-metrics averages/expiry' if LIVE_CASE == 'metrics' else 'current-task rate') + ' Text/PlainText/geometry: ' + LIVE_CASE)

    # Pinned totals are independent of rotation and remain above the scrolled body.
    assert result['pinnedBefore'], 'Persistent token header missing (totals still rotate away)'
    assert result['pinnedAfter'] == result['pinnedBefore'], 'Rotation or scrolling hid the fixed header'
    fixed = '\n'.join(result['pinnedAfter'])
    expected_value = {'recorded':1000, 'zero':0, 'missing':None}[CLOUD_CASE]
    expected_count = {'recorded':'1.0K', 'zero':'0', 'missing':'—'}[CLOUD_CASE]
    accounting_value = expected_accounting['sources'][0]['windows']['history'] if expected_accounting else None
    history_values = [n for n in (expected_value, accounting_value) if n is not None]
    expected_history = sum(history_values) if history_values else None
    def count_text(n):
        return '—' if n is None else (f'{n / 1000:.1f}K' if n >= 1000 else str(n))
    legacy_value, legacy_count = expected_value, expected_count
    history_data = None
    if history_path and history_case not in ('malformed', 'unsupported', 'overflow'):
        history_data = json.loads(history_path.read_text())
    current_key = result['datedClock']['payload']['keys']['day']
    current_bucket = next((b for b in history_data['buckets']['day'] if b['key'] == current_key), None) if history_data else None
    expected_value = sum(s['counts']['total_tokens'] for s in current_bucket['sources']) if current_bucket else None
    expected_count = count_text(expected_value)
    assert fixed.splitlines()[0] == 'Recorded tokens', fixed
    assert ' · partial' not in fixed, fixed
    for expected in ['Recorded tokens', 'Cloud · today ' + expected_count,
                     'Cloud · today ' + expected_count + ' · week ',
                     'Local · today', 'Total · today']:
        assert expected in fixed, (expected, fixed)
    if 'content: PanelKeyCatcher {' in (fixture/'plugin/Panel.qml').read_text():
        assert result['preOpenHeaderAbsent'], "Native lazy content must not be built at startup"
    else:
        assert not result['preOpenHeaderAbsent'], 'Supported eager content must be instantiated at startup'
    assert result['headerRetainedOnReopen'], "Cached native content must survive closing and reopening"
    assert 'Codex CLI' not in fixed, 'Shared-account CLI history must not be a separate summary row'
    combined = totals = result['recordedTotals']
    local_value = 2042 if LOCAL_CASE == 'sampled' and result['datedClock']['payload']['localCalendarAligned'] else None
    known = [n for n in (expected_value, local_value) if n is not None]
    assert combined['total']['today'] == (sum(known) if known else None)
    geometry = result['pinnedGeometry']
    assert geometry['visible'] and geometry['width'] > 0 and 0 < geometry['height'] <= 140, geometry
    for redundant in ['not all-account', 'not subscription lifetime', 'unattributed', 'In + Out +', 'cache excluded', 'quota-only']:
        assert redundant not in fixed, (redundant, fixed)
    assert geometry['bodyY'] >= geometry['y'] + geometry['height'], geometry
    available = 450 if screen_case == 'short' else 1400
    natural = geometry['bodyY'] + geometry['bodyContent'] + geometry['inset']
    assert abs(geometry['popupHeight'] - min(available, natural)) <= 2, geometry
    if natural <= available:
        assert geometry['scrollY'] == 0 and geometry['bodyHeight'] + 1 >= geometry['bodyContent'], geometry
        cards = result['providerGeometry']
        assert {c['id'] for c in cards} == (set(result['ids']) - set(hermes_ids)) | fresh_codex_ids, cards
        assert all(c['y'] >= -1 and c['y'] + c['height'] <= geometry['bodyHeight'] + 1 for c in cards), cards
    else:
        assert geometry['scrollY'] > 0 and geometry['bodyContent'] > geometry['bodyHeight'], geometry
    n = combined['total']['today']
    combined_count = '—' if n is None else (f'{n / 1000:.1f}K' if n >= 1000 else str(n))
    bar = result['barTokens']
    assert bar['visible'] and bar['height'] > 0, bar
    if os.environ.get('ALACARCHY_BAR_CASE') == 'vertical':
        assert bar['vertical'] and bar['width'] <= 32 and bar['text'] == '', bar
        assert bar['glyph'] == combined_count and bar['glyph'] in bar['texts'], bar
        assert 0 < bar['paintedWidth'] <= bar['width'], bar
    else:
        assert bar['text'] == 'Tokens Today ' + combined_count and bar['width'] > 100, bar
    totals = result['recordedTotals']
    assert totals['cloud']['today']['value'] == expected_value
    assert totals['cloud']['today']['known'] == (len(current_bucket['sources']) if current_bucket else 0)
    assert totals['cloud']['today']['sources'] == (len(history_data['sources']) if history_data else None)
    assert totals['cloud']['history']['value'] == expected_history and totals['codex']['history'] is None
    assert totals['cloud']['history']['known'] == len(history_values)
    assert totals['cloud']['history']['sources'] == native_cloud_sources + (expected_accounting is not None)
    assert totals['undated']['cloud']['week']['value'] is None, 'Claude merged message counts are never dated tokens'
    assert totals['cloud']['week']['value'] == expected_value
    assert result['pinnedPlainText'], 'Counter labels must use plain-text sinks'
    def assert_accounting_proof(proof, expected):
        assert proof['path'] == str(cumulative_path), proof
        assert proof['record'] == expected, (proof['record'], expected)
        assert (json.loads(proof['parsedText']) if expected is not None else proof['parsedText']) == (expected if expected is not None else '')
        assert proof['plainText'] and proof['geometry']['visible'], proof
        assert proof['geometry']['width'] > 0 and proof['geometry']['height'] > 0, proof
        assert len(proof['textGeometry']) == 4
        assert all(t['plainText'] and t['width'] > 0 and t['height'] > 0 for t in proof['textGeometry']), proof
        assert [t['text'] for t in proof['textGeometry']] == proof['texts'], proof
        cloud = proof['totals']['cloud']
        population = '?' if cloud['today']['sources'] is None else str(cloud['today']['sources'])
        assert f"Cloud today: {cloud['today']['known']}/{population} sources recorded." in proof['coverage'], proof
        assert proof['coverage'] == proof['headerCoverage'], proof
        assert 'Missing counters are not zero; quota is not tokens.' in proof['coverage'], proof
        assert 'partial' not in proof['texts'][0].lower()
        assert all(proof['totals']['byProvider'][i] == '' for i in hermes_ids)
        if expected is not None:
            assert len(expected['sources']) == 1  # The synthetic Local-route row was excluded.
            source = expected['sources'][0]
            assert source['provider'] == 'anthropic' and source['attribution'] == 'unattributed'
            assert source['windows']['today'] is source['windows']['week'] is None
            assert source['id'] not in ('native-1', 'native-2', 'native-3', *hermes_ids)
            assert 'PRIVATE_' not in proof['parsedText']
    assert_accounting_proof(result['accountingProof'], expected_accounting)
    assert result['accounting']['record'] == expected_accounting
    if ACCOUNTING_CASE == 'wrong-path':
        assert cumulative_path.with_name('accounting.json').is_file() and not cumulative_path.exists()
    if ACCOUNTING_CASE == 'reactive':
        stages = result['accountingStages']
        assert [s['stage'] for s in stages] == ['wrong-path', 'observed', 'zero', 'removed'], stages
        for stage in stages:
            proof = stage['proof']
            expected = reactive_records.get(stage['stage'])
            assert_accounting_proof(proof, expected)
            h = proof['totals']['cloud']['history']
            value = 0 if expected is None else expected['sources'][0]['windows']['history']
            assert h == dict(value=1000 + value, known=1 + (expected is not None),
                             sources=native_cloud_sources + (expected is not None), overflow=False), proof
            assert proof['totals']['local']['history'] == 2042
            assert proof['totals']['total']['history'] == sum((1000, 2042, value))
            assert proof['totals']['total']['today'] == 2060  # 18 dated Cloud + 2042 Local, never undated 1000.
            assert proof['totals']['cloud']['today']['value'] == 18
            assert 'Cloud · today 18 · week 18' in proof['texts']
            assert 'Total · today 2.1K · week 2.1K' in proof['texts']
        assert stages[0]['proof']['coverage'] == stages[1]['proof']['coverage'], 'undated publication cannot change dated coverage'
        assert stages[1]['proof']['coverage'] == stages[2]['proof']['coverage']
        assert stages[0]['proof']['coverage'] == stages[3]['proof']['coverage']
        assert reactive_records['observed']['sources'][0]['windows']['history'] == sum((110,25,32,6))
        assert reactive_records['zero']['sources'][0]['windows']['history'] == 0
    print('PASS actual accounting CLI -> native Agent/FileView -> cumulative Text/PlainText/geometry/coverage: ' + ACCOUNTING_CASE)
    assert totals['local']['today'] == local_value
    assert all(totals['byProvider'][i] == '' for i in hermes_ids)
    assert not any('token counters unavailable (quota-only)' in text for text in texts)
    if CLOUD_CASE != 'missing':
        assert legacy_count + ' today' in totals['byProvider']['claude']
    else:
        assert totals['byProvider']['claude'] == ''
    print('PASS persistent bar/header, scroll/rotation independence, cloud/Local/Codex scopes and quota-only coverage')

    assert result['historyGeometry'] and result['historyGeometry']['width'] > 0 and result['historyGeometry']['height'] > 0
    assert result['historyHelp']['tab'] and result['historyHelp']['text']
    assert result['historyPlainText']
    def history_groups(fit):
        """Each dated bucket with the bars and labels drawn inside it, in item order."""
        groups = []
        for item in fit['items']:
            if item['name'] == 'historyBucket':
                groups.append((item, []))
            else:
                assert groups, ('chart item outside a dated bucket', item)
                groups[-1][1].append(item)
        return groups
    def assert_fits(proof):
        # The whole calendar fits the popup's content width: no sideways
        # flick, and every dated bucket, bar and label lies in the viewport.
        if not proof['view']['bars']:
            return
        stage, fit = proof['stage'], proof['fit']
        assert fit and not fit['flickable'] and not fit['scrolls'], (stage, fit and {k:fit[k] for k in fit if k != 'items'})
        assert -0.01 <= fit['left'] and fit['right'] <= fit['viewport'] + 0.01, (stage, fit['left'], fit['right'], fit['viewport'])
        groups = history_groups(fit)
        buckets = [bucket for bucket, _ in groups]
        bars = [i for i in fit['items'] if i['name'] == 'observedHistoryBar']
        assert [b['key'] for b in buckets] == [b['key'] for b in proof['view']['bars']], (stage, 'date buckets', len(buckets))
        # A calendar is seven equal columns, not a Flow whose label floors can
        # move the seventh date onto a second row: up to seven dates share one
        # row, oldest first, and only an eighth starts the next row.
        assert max(b['width'] for b in buckets) - min(b['width'] for b in buckets) <= 0.01, (stage, 'column widths')
        rows = [buckets[i:i + 7] for i in range(0, len(buckets), 7)]
        for r, row in enumerate(rows):
            assert all(abs(b['top'] - row[0]['top']) <= 0.5 for b in row), (stage, 'date row', r, [b['top'] for b in row])
            assert all(b['left'] >= a['right'] - 0.01 for a, b in zip(row, row[1:])), (stage, 'date column', r, [b['left'] for b in row])
            assert abs(row[0]['left'] - buckets[0]['left']) <= 0.5, (stage, 'row start', r, row[0]['left'])
            if r:
                assert row[0]['top'] >= max(b['bottom'] for b in rows[r - 1]) - 0.01, (stage, 'next row', r)
        assert len(bars) == len(proof['bars']) and len(bars) > 0, stage
        assert [b['key'] for b in bars if b['key']][-1] == proof['view']['bars'][-1]['key'], stage
        for item in fit['items']:
            assert fit['left'] - 0.01 <= item['left'] and item['right'] <= fit['right'] + 0.01, (stage, item, fit['left'], fit['right'])
        multiple = proof['view']['mode'] == 'multiple'
        for (bucket, inside), dated in zip(groups, proof['view']['bars']):
            cells = [i for i in inside if i['name'] == 'observedHistoryBar']
            assert len(cells) == (len(dated['series']) if multiple else 1), (stage, bucket['key'], len(cells))
            if multiple:
                # Source mode stacks every source at full date-column width
                # (the former side-by-side 50px floor narrowed the dates).
                inset = cells[0]['left'] - bucket['left']
                assert all(abs(c['left'] - cells[0]['left']) <= 0.01 and abs(bucket['right'] - c['right'] - inset) <= 0.01
                           for c in cells), (stage, 'stacked cell span', bucket['key'])
                assert all(b['top'] > a['top'] for a, b in zip(cells, cells[1:])), (stage, 'stacked cells', bucket['key'])
            for item in inside:
                assert bucket['top'] - 0.01 <= item['top'] and item['bottom'] <= bucket['bottom'] + 0.01, (stage, 'outside date', item)
                assert bucket['left'] - 0.01 <= item['left'] and item['right'] <= bucket['right'] + 0.01, (stage, 'outside date', item)
            ordered = sorted(inside, key=lambda i: (i['top'], i['bottom']))
            assert all(b['top'] >= a['bottom'] - 0.5 for a, b in zip(ordered, ordered[1:])), (stage, 'overlap', bucket['key'])
            for label in (i for i in inside if i['natural'] is not None):
                # Fitted, never clipped: every line fits, every character
                # stays in order, and a number is never split across lines.
                assert label['natural'] <= label['width'] + 0.5, (stage, label)
                lines = fitted_lines(label['text'], label['fullText'] or '')
                assert label['fullText'] is not None and lines is not None, (stage, 'label text', label)
                assert not any(re.search('[0-9.,]$', a) and re.match('[0-9]', b) for part in lines for a, b in zip(part, part[1:])), (stage, 'split number', label)
                if fit['popup'] is not None and fit['popup'] >= 380:
                    assert label['text'] == label['fullText'], (stage, 'normal popup breaks a label', label)
    def assert_layout(proof, invariant=False):
        assert proof['plainText']
        if invariant:
            assert proof['totals'] == result['recordedTotals'] and proof['pinned'] == result['pinnedBefore']
        p = proof['totals']
        assert ('Total · today ' + count_text(p['total']['today'])
                + ' · week ' + count_text(p['total']['week'])
                + ' · recorded ' + count_text(p['total']['history'])) in proof['pinned']
        if proof['view']['bars'] and proof['view']['metric'] == 'total_tokens':
            key = {'day':'today', 'week':'week', 'month':'month', 'year':'year'}[proof['view']['grain']]
            assert proof['view']['bars'][-1]['value'] == p['total'][key]
        assert proof['selectors'] == [], proof['selectors']
        assert not proof['view']['selectionEmpty'] or not proof['view']['sources'], proof['view']
        fitting = proof['fitting']
        assert fitting and fitting['body'] > 0 and fitting['popup'] <= (450 if screen_case == 'short' else 1400)
        assert fitting['scrolling'] == (fitting['content'] > fitting['body'])
        assert fitting['bodyY'] > fitting['header'] > 0
        for bar in proof['bars']:
            assert bar['width'] > 0 and bar['height'] >= 0
            assert 0 <= bar['x'] and bar['x'] + bar['width'] <= bar['parentWidth'] + 0.01
            assert 0 <= bar['y'] and bar['y'] + bar['height'] <= bar['parentHeight'] + 0.01
        assert_fits(proof)
    controls = result['historyControls']
    if history_case in ('controls', 'inventory'):
        assert all(controls[key] for key in ('metric', 'grain')), 'native history metric/grain controls missing'
        assert not controls['mode'], 'redundant Combined/Per-source controls must not be instantiated'
        assert controls['sources'] is False and controls['selectors'] == [], 'source picker must not be instantiated'
    if history_case == 'controls':
        # Every admitted source is charted implicitly: 18 (anthropic) + 32 (codex).
        assert controls['view'] and len(controls['view']['selectedSources']) == 2
        assert controls['view']['bars'][-1]['value'] == sum((10,5,2,1,20,7,3,2))
        stages = {p['stage']:p for p in result['historyProof']}
        assert len(stages) == len(result['historyProof']) == 13
        assert stages['initial']['view']['bars'][-1]['value'] == 50
        for metric, value in [('output_tokens',12), ('input_tokens',30), ('prompt_tokens',38),
                              ('cache_read_tokens',5), ('cache_write_tokens',3),
                              ('reasoning_tokens',3), ('api_call_count',2), ('total_tokens',50)]:
            view = stages[metric]['view']
            assert view['metric'] == metric and view['bars'][-1]['value'] == value, 'native metric handler disconnected: ' + metric
            assert stages[metric]['bars'][-1]['value'] == value
            suffix = '+' if view['bars'][-1]['partial'] else ''
            assert '02-29\n' + count_label(value) + suffix in stages[metric]['labels']
        for grain, key in [('day','2024-02-29'), ('week','2024-02-26'), ('month','2024-02'), ('year','2024')]:
            view = stages[grain]['view']
            assert view['grain'] == grain and view['bars'][-1]['key'] == key
            assert view['bars'][-1]['value'] == 50
            assert len(stages[grain]['bars']) == (5 if grain == 'year' else 7)
            assert stages[grain]['bars'][-1]['key'] == key and stages[grain]['bars'][-1]['value'] == 50
            display_key = key[5:] if grain in ('day','week') else key
            assert display_key + '\n50+' in stages[grain]['labels']
            assert stages[grain]['labels'] == expected_labels(view), (grain, stages[grain]['labels'])
        assert all(p['view']['mode'] == 'combined' for p in stages.values())
        for proof in stages.values():
            assert_layout(proof, invariant=True)
            assert len(proof['view']['selectedSources']) == 2
            assert proof['chart'] and proof['chart']['width'] > 0
            if proof['view']['bars']:
                assert any(('2024' in t or '02-29' in t) for t in proof['texts'])
        refresh = {p['stage']:p for p in result['historyRefreshProof']}
        assert len(refresh) == len(result['historyRefreshProof']) == 9
        assert refresh['refreshed']['observation'] == '2024-02-29T15:10:00Z'
        for name in ('configured','refreshed','removed','expanded','invalid'):
            if name != 'invalid':
                proof = refresh[name]
                assert (proof['view']['metric'],proof['view']['grain'],proof['view']['mode']) == ('output_tokens','week','combined')
        # Membership follows the current publication; nothing is remembered or defaulted.
        for name, count, value in (('configured',2,12), ('refreshed',2,12), ('removed',1,7), ('expanded',3,12)):
            view = refresh[name]['view']
            assert len(view['sources']) == len(view['selectedSources']) == count, (name, view)
            assert view['bars'][-1]['value'] == value and not view['selectionEmpty'], (name, view)
            assert len(refresh[name]['bars']) == 7, name
        # All admitted sources share one cell per date, with seven readable columns.
        expanded = history_groups(refresh['expanded']['fit'])
        assert [sum(i['name'] == 'observedHistoryBar' for i in inside) for _, inside in expanded] == [1] * 7
        assert len({round(bucket['top'], 1) for bucket, _ in expanded}) == 1, 'per-source mode moved a date to a second row'
        assert refresh['invalid']['state'] == 'malformed'
        assert all(b['value'] is None for b in refresh['invalid']['view']['bars'])
        assert refresh['restored']['view']['bars'][-1]['value'] == 50
        assert 'Current calendar · UTC' in stages['initial']['texts']
        partial_bars = [bar for bar in refresh['partial']['bars'] if bar['key'] == '2024-03-01']
        assert len(partial_bars) == 1 and partial_bars[0]['value'] == 0 and partial_bars[0]['height'] == 1
        partial_bucket = refresh['partial']['view']['bars'][-1]
        assert partial_bucket['cloud']['knownSources'] == 1 and partial_bucket['cloud']['sources'] == 2
        assert any(t.endswith('\n?') for t in refresh['partial']['labels']), 'an absent source stays unknown, never zero'
        combined_partial = refresh['partial-combined']
        assert combined_partial['view']['mode'] == 'combined'
        assert combined_partial['view']['bars'][-1]['partial']
        assert '03-01\n0+' in combined_partial['labels'], 'combined partial counts must visibly remain a lower bound'
        zero_bars = [bar for bar in refresh['zero']['bars'] if bar['key'] == '2024-03-01']
        assert len(zero_bars) == 1 and all(b['value'] == 0 and b['height'] == 1 for b in zero_bars)
        for proof in refresh.values():
            assert_layout(proof)
            assert proof['labels'] == expected_labels(proof['view']), (proof['stage'], proof['labels'])
        print('PASS native history controls: 13 interaction + 9 FileView refresh snapshots; no source/mode controls, zero/unknown coverage and shared pinned totals')
        # Renderer-only synthetic stimulus, kept apart from the production
        # proofs above: the exact chart template with 7, 8 and 14 buckets.
        rendered = {p['stage']:p for p in result['historyRendererProof']}
        widths = (380, 248, 232)
        assert list(rendered) == [s['name'] + '@' + str(w) for s in stimuli for w in widths], list(rendered)
        for stimulus in stimuli:
            view = stimulus['view']
            for width in widths:
                proof = rendered[stimulus['name'] + '@' + str(width)]
                assert proof['view'] == view and proof['plainText'] and proof['fit']['popup'] == width, proof['stage']
                assert_fits(proof)
                assert proof['labels'] == expected_labels(view), (proof['stage'], proof['labels'])
                # Long counts stay whole: 133.4M+ (combined) / 133.4M (per source).
                assert any(l.endswith('\n133.4M' + ('' if view['mode'] == 'multiple' else '+')) for l in proof['labels']), proof['stage']
                labels = [i for i in proof['fit']['items'] if i['natural'] is not None]
                if width < 380:
                    assert any(l['text'] != l['fullText'] for l in labels), (proof['stage'], 'long labels must break to fit, not overflow')
                vertical = proof['vertical']
                assert vertical and vertical['direction'] and 0 < vertical['height'] <= vertical['maxHeight'] <= 320, (proof['stage'], vertical)
                assert vertical['count'] == len(proof['bars']) and vertical['last']['key'] == view['bars'][-1]['key'], (proof['stage'], vertical)
                assert -0.01 <= vertical['last']['top'] <= vertical['last']['bottom'] <= vertical['height'] + 0.01, (proof['stage'], vertical)
                if vertical['contentHeight'] > vertical['height']:
                    assert vertical['interactive'] and vertical['contentY'] > 0, (proof['stage'], vertical)
        assert rendered['week-14-multiple@232']['vertical']['interactive'], 'stacked sources must use the bounded vertical viewport'
        print('PASS renderer-only calendar stimulus (synthetic, not production data): 7/8/14 buckets, day/week/month/year, '
              'combined/per-source at 380/248/232 px; seven per row, wrap only after the seventh, 133.4M+ labels fitted unclipped')
    if history_case == 'inventory':
        assert control_data is not None
        inventory_proof = {p['stage']:p for p in result['historyRefreshProof']}
        assert list(inventory_proof) == ['inventory', 'two'], list(inventory_proof)
        latest = control_data['buckets']['day'][-1]
        expected = sum(cell['counts']['total_tokens'] for cell in latest['sources'])
        for name, sources in (('inventory',313), ('two',2)):
            proof = inventory_proof[name]
            assert_layout(proof)
            view = proof['view']
            assert len(view['sources']) == len(view['selectedSources']) == sources, (name, len(view['sources']))
            assert len(proof['bars']) == 7 and view['mode'] == 'combined', name
        last = inventory_proof['inventory']['view']['bars'][-1]
        assert (last['value'], last['known'], last['partial']) == (expected, len(latest['sources']), True), last
        assert inventory_proof['inventory']['bars'][-1]['value'] == expected
        assert '02-29\n' + count_text(expected) + '+' in inventory_proof['inventory']['labels']
        for proof in inventory_proof.values():
            assert proof['labels'] == expected_labels(proof['view']), proof['stage']

        # Source count cannot make the viewport unbounded. Reflow may add
        # rows, but the calendar viewport has a strict height cap, and every
        # source/date remains reachable by vertical scrolling, never clipping.
        for name in ('inventory', 'two'):
            proof = inventory_proof[name]
            vertical = proof['vertical']
            assert vertical and vertical['direction'], (name, vertical)
            assert 0 < vertical['height'] <= vertical['maxHeight'] <= 320, (name, vertical)
            assert vertical['count'] == len(proof['bars'])
            assert vertical['last']['key'] == proof['view']['bars'][-1]['key']
            assert -0.01 <= vertical['last']['top'] <= vertical['last']['bottom'] <= vertical['height'] + 0.01, (name, vertical)
        large = inventory_proof['inventory']['vertical']
        assert not large['interactive'], 'source count must not expand the main aggregate calendar'
        cards = inventory_proof['inventory']['cards']
        assert {c['id'] for c in cards} == (set(result['ids']) - set(hermes_ids)) | fresh_codex_ids, cards
        first = min(cards, key=lambda c: c['y'])
        assert 0 <= first['y'] and first['y'] + first['height'] <= inventory_proof['inventory']['fitting']['body'], (first, inventory_proof['inventory']['fitting'])
        print('PASS native large inventory: 313 sources/512 events, no source controls, bounded vertical viewport, every source/date retained and final bar reachable')
    if history_case in ('controls', 'inventory'):
        resized = {p['stage']:p for p in result['historyResizeProof']}
        assert list(resized) == ['narrowed-reopened', 'restored-reopened'], list(resized)
        before = result['historyRefreshProof'][-1]
        narrow, restored = resized['narrowed-reopened'], resized['restored-reopened']
        opened = (result['historyProof'] or result['historyRefreshProof'])[0]['fit']['popup']
        assert narrow['fit']['popup'] == 232 < opened == restored['fit']['popup'] == before['fit']['popup'], (narrow['fit']['popup'], opened, restored['fit']['popup'])
        for proof in (narrow, restored):
            assert_layout(proof)
            assert proof['view'] == before['view'] and len(proof['bars']) == len(before['bars']), proof['stage']
            # Width may only add fitted label breaks: every label and text survives.
            assert proof['labels'] == before['labels'], proof['stage']
            assert [t.replace('\n', '') for t in proof['texts']] == [t.replace('\n', '') for t in before['texts']], proof['stage']
        assert restored['texts'] == before['texts'], 'reopening at the same width changed the calendar'
        assert narrow['fit']['right'] - narrow['fit']['left'] < restored['fit']['right'] - restored['fit']['left']
        print('PASS history calendar fits %s width %s, resized to 232 and reopened, then restored: every date, bar and label in view, no sideways scroll'
              % (width_case, opened))
    if history_case == 'baseline':
        assert all(s['value'] is None for s in result['history']['summaries'])
        assert all(b['cloud']['value'] is None for b in controls['view']['bars']), 'Undated baseline must not become Cloud dates'
    assert result['history']['state'] == {'missing':'unavailable','observed':'observed','baseline':'baseline','zero':'zero','empty':'empty','malformed':'malformed','overflow':'malformed','unsupported':'malformed','controls':'observed','inventory':'observed'}[history_case], result['history']
    if history_case in ('observed','zero'):
        status = 'Measured zero' if totals['total']['today'] == 0 else 'Dated sampled totals'
        assert status in result['historyTexts'], 'history status must be compact and informative'
        assert not any('partial' in text.lower() for text in result['historyTexts']), 'coverage caveats belong in the history tooltip'
        assert result['historyHelp'] and 'Dated windows use the shared clock' in result['historyHelp']['text']
    if history_case in ('observed','zero'):
        assert len(result['historyBars']) == 7
        assert all(b['width'] > 0 for b in result['historyBars'])
        assert sum(b['height'] > 0 for b in result['historyBars']) == 1
        assert result['historyBars'][-1]['height'] <= 40
        assert all(b['y'] >= 0 and b['y']+b['height'] <= b['parentHeight'] for b in result['historyBars']), result['historyBars']
        suffix = '+' if controls['view']['bars'][-1]['partial'] and totals['total']['today'] is not None else ''
        assert '02-29\n' + count_label(totals['total']['today']) + suffix in result['historyTexts'], result['historyTexts']
    if history_case == 'observed':
        assert result['history']['state'] == 'observed'
        assert [x['value'] for x in result['history']['summaries']] == [18]*4
        assert 'Day · 2024-02-29 · ' + count_label(totals['total']['today']) + ' tokens' in result['historyTexts'], result['historyTexts']

    # The legacy summary's cloud accounting still excludes sampled Local tokens.
    if CLOUD_CASE == 'recorded':
        assert '1.0K tokens today' in result['phrases'] and '1.0K tokens this week' in result['phrases'], result['phrases']
    assert not any('3.0K' in phrase or '2.0K' in phrase or 'model' in phrase.lower() for phrase in result['phrases']), result['phrases']

    # Commands: the panel's refresh reached the stub; nothing else ran.
    assert not unexpected.exists(), unexpected.read_text()
    ran = calls.read_text().splitlines() if calls.exists() else []
    assert any(line.startswith('omarchy-agent-usage-update') for line in ran), ran
    assert all(line.split()[0] in EXPECTED_STUBS for line in ran), ran
    clock_runs = [json.loads(line) for line in clock_calls.read_text().splitlines()]
    assert len(clock_runs) >= 2 and all(c['exit'] == 0 for c in clock_runs), clock_runs
    assert any(json.loads(c['payload'])['timezone'] == result['datedClock']['payload']['timezone'] for c in clock_runs)
    print('NATIVE_COMMAND_LOG ' + json.dumps({'expected':ran, 'unexpected':[],
          'sha256':hashlib.sha256(calls.read_bytes()).hexdigest()}))
    assert sorted(p.name for p in usage.iterdir() if p.suffix == '.json') == sorted(
        [i + '.json' for i in hermes_ids] + [ghost_id + '.json', 'claude.json','codex.json','grok.json','local.json'])
    assert json.loads((usage/'grok.json').read_text()) == native_grok, 'native Grok record was touched'
    assert json.loads((usage/'codex.json').read_text()) == native_codex, 'native Codex was touched'
    assert json.loads(stray_grok.read_text())['name'] == 'STRAY CUSTOM GROK', 'stray custom Grok metadata was touched'
    print('Native primitives loaded; patched %s-layout Agents clone listed Local beside a subscription.' % layout)
print('Headless fixture only; rendered/live acceptance not observed.')
