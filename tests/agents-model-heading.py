#!/usr/bin/env python3
"""Execute the actual generated hero's model-mix expression."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('model_heading_stage', ROOT / 'extras/local-router-stats/stage_plugin.py')
s = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s)
NATIVE = Path(os.environ.get('ALACARCHY_AGENTS_SOURCE', '/usr/share/omarchy/shell/plugins/agents'))


class ModelHeading(unittest.TestCase):
    def test_cloud_only_model_mix_is_explicitly_cloud_history(self):
        panel = s.render(NATIVE, 'fixture.modelscope')['Panel.qml']
        expression = panel.split('  readonly property var summaryPhrases: {', 1)[1].split('  property int phraseIndex:', 1)[0]
        script = '''var usage={dataRevision:1,friendlyModelName:function(x){return x==='opus'?'Opus':x;},formatTokenCount:String};
var Commons={I18n:{t:function(k){return {arg:function(x){return k==='agents.mostlyModel'?'Mostly '+x:k+' '+x;}};},tn:function(k,n){return {arg:function(x){return k+' '+x;}};}}};
var providers=[{providerId:'claude',hasPromptStats:false,recentDays:[],modelUsage:{opus:{inputTokens:500,outputTokens:5}}},
 {providerId:'local',hasPromptStats:false,recentDays:[],modelUsage:{heretic:{inputTokens:1000000,outputTokens:10000}}}];
usage.enabledProviders=providers;
function actualSummary(){''' + expression + '\nconsole.log(JSON.stringify(actualSummary()));'
        result = subprocess.run([shutil.which('node'), '-'], input=script, text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        phrases = json.loads(result.stdout)
        self.assertIn('Cloud history · Mostly Opus', phrases)
        self.assertNotIn('Mostly Opus', phrases, 'cloud-only history must not imply the whole current workload')
        self.assertFalse(any('Mostly heretic' in x for x in phrases), 'do not mix sampled local totals into cloud history')


if __name__ == '__main__':
    unittest.main(verbosity=2)
