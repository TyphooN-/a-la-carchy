#!/usr/bin/env python3
"""Display-only Hermes Codex seat-label regression; no auth or collectors."""
import json
import shutil
import types
import os
from pathlib import Path
import re
import subprocess
import unittest


class BusinessPlanLabels(unittest.TestCase):
    def test_stable_seat_labels_and_unchanged_provider_records(self):
        if os.environ.get('ALC_PLAN_PANEL'):
            panel = Path(os.environ['ALC_PLAN_PANEL']).read_text()
        else:
            source = Path(__file__).resolve().parents[1]/'extras/local-router-stats/stage_plugin.py'
            module = types.ModuleType('business_label_stage')
            module.__file__ = str(source)
            exec(compile(source.read_bytes(), str(source), 'exec'), module.__dict__)
            native = '  function planLabel(p) {\n    var tier = String(p && p.tierLabel || "")\n    if (tier === "Prepaid") return Commons.I18n.t("agents.tierPrepaid")\n    return tier === "" ? "" : tier.charAt(0).toUpperCase() + tier.slice(1)\n  }'
            panel = module.transform_hermes_plan_labels(native)
        starts = list(re.finditer(r'^  function planLabel\(p\) \{\n', panel, re.M))
        self.assertEqual(len(starts), 1)
        start = starts[0].start()
        end = panel.index('\n  }', starts[0].end()) + len('\n  }')
        function = panel[start:end]
        script = r'''
const fs = require('fs');
const vm = require('vm');
const assert = require('assert/strict');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const first = 'codex-hermes-' + '1'.repeat(64);
const second = 'codex-hermes-' + '2'.repeat(64);
const third = 'codex-hermes-' + '3'.repeat(64);
let labels = {[first]: 'Business Standard', [second]: 'Business Premium'};
const context = {usage: {setting: (key, fallback) => key === 'hermesPlanLabels' ? labels : fallback},
                 Commons: {I18n: {t: key => 'translated:' + key}}};
vm.createContext(context);
vm.runInContext(input.function + '\nthis.formatPlan = planLabel;', context);
const records = [
 {providerId:first, credentialProvider:'openai-codex', readOnly:true, tierLabel:'team', limits:[{percent:0,resetsAt:'fixture'}], totalTokens:123},
 {providerId:second, credentialProvider:'openai-codex', readOnly:true, tierLabel:'Hermes', limits:[{percent:0.25,resetsAt:'fixture'}], totalTokens:456}
];
const before = JSON.stringify(records);
assert.deepEqual(records.map(context.formatPlan), ['Business Standard','Business Premium']);
assert.deepEqual(records.slice().reverse().map(context.formatPlan), ['Business Premium','Business Standard']);
assert.equal(context.formatPlan(records[1]), 'Business Premium'); // hiding the first does not renumber the second
assert.equal(JSON.stringify(records), before); // never modify quota/history/identity fields
assert.equal(context.formatPlan({providerId:third,credentialProvider:'openai-codex',readOnly:true,tierLabel:'plus'}), 'Plus');
assert.equal(context.formatPlan({providerId:first,credentialProvider:'anthropic',readOnly:true,tierLabel:'team'}), 'Team');
assert.equal(context.formatPlan({providerId:first,credentialProvider:'openai-codex',readOnly:false,tierLabel:'team'}), 'Team');
assert.equal(context.formatPlan({providerId:'codex',tierLabel:'team'}), 'Team');
assert.equal(context.formatPlan({tierLabel:'Prepaid'}), 'translated:agents.tierPrepaid');
assert.equal(context.formatPlan(null), '');
labels = {[second]:'<img src="https://invalid.example/">'};
assert.equal(context.formatPlan(records[1]), 'Hermes');
labels = {[second]:false};
assert.equal(context.formatPlan(records[1]), 'Hermes');
labels = Object.create({[second]:'Business Premium'});
assert.equal(context.formatPlan(records[1]), 'Hermes');
labels = null;
assert.equal(context.formatPlan(records[0]), 'Business Standard');
assert.equal(context.formatPlan(records[1]), 'Hermes');
console.log('BUSINESS_PLAN_LABELS_OK');
'''
        node = os.environ.get('ALC_NODE') or shutil.which('node')
        if node is None:
            raise RuntimeError('Node is required for the business-label regression')
        result = subprocess.run(
            [node, '--max-old-space-size=128', '-e', script],
            input=json.dumps({'function': function}), capture_output=True,
            text=True, timeout=15, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('BUSINESS_PLAN_LABELS_OK', result.stdout)


if __name__ == '__main__':
    unittest.main(verbosity=2)
