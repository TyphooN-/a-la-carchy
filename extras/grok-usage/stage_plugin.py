#!/usr/bin/env python3
"""Stage a new Agents + Local + Grok-observed-tokens clone. Never activate it."""
import argparse
import importlib.util
import json
from pathlib import Path

spec = importlib.util.spec_from_file_location('local_stage', Path(__file__).resolve().parents[1]/'local-router-stats/stage_plugin.py')
assert spec is not None and spec.loader is not None
local = importlib.util.module_from_spec(spec)
spec.loader.exec_module(local)

NAME = 'Agents + Local + Grok tokens'
MARK = local.MARK + ';grok-observed-stats/1'


class NativeGrok(ValueError):
    """The packaged Agents plugin already collects Grok usage itself."""


def transform_main(text):
    text = local.replace_once(text, 'name !== "local.json"', 'name !== "local.json" && name !== "grok.json"')
    text = local.replace_once(text, '  Agent {\n    id: localRouterAgent',
        '  // a-la-carchy: Grok observed counters are local-only, not subscription quota.\n'
        '  readonly property string grokObservedRecordPath: (Quickshell.env("XDG_STATE_HOME") || home + "/.local/state") + "/omarchy/grok-usage/agents/grok.json"\n'
        '  Agent {\n    id: grokObservedAgent\n    agentId: "grok"\n    path: root.grokObservedRecordPath\n  }\n\n'
        '  Agent {\n    id: localRouterAgent')
    text = local.replace_once(text, '    var localRouterRecord = localRouterAgent.record',
        '    var grokObservedRecord = grokObservedAgent.record\n'
        '    if (grokObservedRecord && String(grokObservedRecord.id) === "grok" && providerEnabled("grok"))\n'
        '      result.push(displayProvider(grokObservedRecord))\n'
        '    var localRouterRecord = localRouterAgent.record')
    text = local.replace_once(text, 'if (syncedId === "local") continue', 'if (syncedId === "local" || syncedId === "grok") continue')
    text = local.replace_once(text, 'if (String(record.id) === "local") continue', 'if (String(record.id) === "local" || String(record.id) === "grok") continue')
    return local.replace_once(text, 'if (providerId === "local") return null', 'if (providerId === "local" || providerId === "grok") return null')


def transform_panel(text):
    text = local.replace_once(text, 'return p && p.providerId === "local" ? rows : rows.slice(0, 4)',
                             'return p && (p.providerId === "local" || p.providerId === "grok") ? rows : rows.slice(0, 4)')
    text = local.replace_once(text, 'name: p && p.providerId === "local" ? id : usage.friendlyModelName(id),',
                             'name: p && (p.providerId === "local" || p.providerId === "grok") ? id : usage.friendlyModelName(id),')
    return local.replace_once(text, '    return "In "',
        '    if (provider && provider.providerId === "grok")\n'
        '      return "Observed uncached input " + usage.formatTokenCount(row.input) + " · output " + usage.formatTokenCount(row.output) + " · cache read " + usage.formatTokenCount(row.cacheRead) + " · cache creation " + usage.formatTokenCount(row.cacheWrite)\n'
        '    return "In "')


def render(source, plugin_id):
    """Like local.render, plus the observed-Grok tab. Writes nothing.

    Refuses a packaged plugin that ships its own Grok collector: hiding the
    first-party record (real limits and its own ledger) behind sampled
    status-line counters would be a downgrade, and showing both would count
    the same sessions twice.
    """
    local.source_files(source)
    manifest = json.loads(local.source_bytes(Path(source)/'manifest.json'))
    if 'grok' in local.native_providers(manifest):
        raise NativeGrok('this Omarchy release shows Grok usage natively in the Agents panel')
    rendered = local.render(source, plugin_id)
    if rendered['layout'] != 'tabs':
        raise ValueError(local.UNSUPPORTED)
    rendered['Main.qml'] = transform_main(rendered['Main.qml'])
    rendered['Panel.qml'] = transform_panel(rendered['Panel.qml'])
    rendered['manifest']['name'] = NAME
    if isinstance(rendered['manifest'].get('barWidget'), dict):
        rendered['manifest']['barWidget']['displayName'] = NAME
    rendered['manifest']['omarchy']['alaCarchy'] = MARK
    return rendered


def stage(source, target, plugin_id):
    if Path(target).exists() or Path(target).is_symlink():
        raise ValueError('target must be a new directory')
    local.write(source, target, render(source, plugin_id))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path('/usr/share/omarchy/shell/plugins/agents'))
    parser.add_argument('--id', default='alacarchy.agents')
    parser.add_argument('target', type=Path)
    args = parser.parse_args()
    try:
        stage(args.source, args.target, args.id)
    except (OSError, ValueError) as exc:
        parser.exit(1, str(exc)+'\n')
