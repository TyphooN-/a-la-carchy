#!/usr/bin/env python3
"""Supersonic fixtures: only a private UNIX socket, no desktop/server credentials."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socketserver
import subprocess
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlsplit, parse_qs

BASE = Path(__file__).resolve().parents[1] / 'extras/supersonic'

class IPC(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.calls = []
        self.status = {'Type': 0, 'ID': 'a&? /日本', 'Name': '<b>Song</b>', 'Artists': ['Artist'], 'Album': 'Album', 'AlbumID': 'album'}
        self.response = None
        self.responses = {}
        self.delay = 0
        self.dribble = False
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args): pass
            def do_GET(self):
                parsed = urlsplit(self.path)
                owner.calls.append((parsed.path, parse_qs(parsed.query)))
                data = owner.status if parsed.path == '/current_track' else None
                payload = owner.responses.get(parsed.path, owner.response) or (200, {'data': data, 'error': ''})
                time.sleep(owner.delay)
                try:
                    self.send_response(payload[0]); self.end_headers()
                except BrokenPipeError: return
                raw = payload[1]
                try:
                    raw = raw if isinstance(raw, bytes) else json.dumps(raw).encode()
                    if owner.dribble:
                        for byte in raw:
                            self.wfile.write(bytes([byte])); self.wfile.flush(); time.sleep(0.1)
                    else: self.wfile.write(raw)
                except BrokenPipeError: pass
        class Server(socketserver.ThreadingUnixStreamServer):
            daemon_threads = True
        self.server = Server(str(self.root/'supersonic.sock'), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.env = os.environ.copy() | {'HOME': str(self.root), 'XDG_RUNTIME_DIR': str(self.root)}
    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(); self.tmp.cleanup()
    def run_cli(self, payload):
        run = subprocess.run(['python3', str(BASE/'ipc.py')], input=json.dumps(payload), text=True, capture_output=True, env=self.env, timeout=5)
        self.assertEqual(run.stderr, '')
        return run.returncode, json.loads(run.stdout)
    def test_current_metadata_is_real_and_year_unknown(self):
        rc, out = self.run_cli({'op': 'status'})
        self.assertEqual(rc, 0)
        self.assertEqual(out['state'], 'ready')
        self.assertEqual(out['track']['title'], '<b>Song</b>')
        self.assertEqual(out['track']['artist'], 'Artist')
        self.assertEqual(out['track']['album'], 'Album')
        self.assertIsNone(out['track']['year'])
        self.assertIsNone(out['playing'])
        self.assertEqual(self.calls, [('/current_track', {})])

    def test_mpris_track_id_matches_supersonic_base32_contract(self):
        self.status['ID'] = '423JbWQ4D5JUbrlremKtiV'
        rc, out = self.run_cli({'op': 'status'})
        self.assertEqual(rc, 0)
        self.assertEqual(out['track']['mprisTrackId'], '/Supersonic/Track/GQZDGSTCK5ITIRBVJJKWE4TMOJSW2S3UNFLA0000')

    def test_mpris_timing_uses_exact_track_and_microseconds(self):
        item_id = '423JbWQ4D5JUbrlremKtiV'
        snapshot = {'type':'a{sv}', 'data':[{
            'Metadata': {'type':'a{sv}', 'data':{
                'mpris:trackid': {'type':'o','data':'/Supersonic/Track/GQZDGSTCK5ITIRBVJJKWE4TMOJSW2S3UNFLA0000'},
                'mpris:length': {'type':'x','data':217000000}}},
            'Position': {'type':'x','data':61800000},
            'PlaybackStatus': {'type':'s','data':'Paused'}}]}
        binary = self.root/'bin'
        binary.mkdir()
        busctl = binary/'busctl'
        busctl.write_text('#!/usr/bin/env python3\nimport json\nprint(' + repr(json.dumps(snapshot)) + ')\n')
        busctl.chmod(0o755)
        self.env['PATH'] = str(binary) + ':' + self.env['PATH']
        rc, out = self.run_cli({'op':'timing','id':item_id})
        self.assertEqual((rc, out), (0, {'state':'ready','id':item_id,'elapsed':61.8,'duration':217.0,'playing':False}))
        rc, out = self.run_cli({'op':'timing','id':'different-song'})
        self.assertEqual(out['state'], 'timing-unknown')
        self.assertEqual(self.calls, [])
        # Typed signed counters, unknown lengths, clamping, malformed replies,
        # output cap and whole-call timeout all fail closed without IPC writes.
        props = snapshot['data'][0]
        props['Position']['data'] = 500000000
        busctl.write_text('#!/usr/bin/env python3\nprint(' + repr(json.dumps(snapshot)) + ')\n')
        _, out = self.run_cli({'op':'timing','id':item_id})
        self.assertEqual(out['elapsed'], 217.0)
        props['Position']['data'] = -1
        props['Metadata']['data']['mpris:length']['data'] = 0
        busctl.write_text('#!/usr/bin/env python3\nprint(' + repr(json.dumps(snapshot)) + ')\n')
        _, out = self.run_cli({'op':'timing','id':item_id})
        self.assertIsNone(out['elapsed'])
        self.assertIsNone(out['duration'])
        for script in ["print('not-json')", "print('x' * (512 * 1024 + 1))", "import time; time.sleep(10)", "print('{}'); raise SystemExit(1)"]:
            with self.subTest(script=script):
                busctl.write_text('#!/usr/bin/env python3\n' + script + '\n')
                start = time.monotonic()
                rc, out = self.run_cli({'op':'timing','id':item_id})
                self.assertEqual((rc, out), (0, {'state':'timing-unknown','id':item_id}))
                self.assertLess(time.monotonic() - start, 3)

    def test_search_and_actual_play_ids(self):
        item_id = 'odd &?=/;$(touch nope) 日本'
        self.response = (200, {'data': [{'ID': item_id, 'Title': 'Song', 'ArtistNames': ['Artist'], 'Album': 'Album', 'Year': 2004}], 'error': ''})
        rc, out = self.run_cli({'op': 'search', 'kind': 'song', 'query': 'odd &? 日本'})
        self.assertEqual(rc, 0)
        self.assertEqual(out['results'][0], {'id': item_id, 'kind': 'song', 'title': 'Song', 'artist': 'Artist', 'album': 'Album', 'year': 2004})
        self.assertEqual(self.calls[-1], ('/transport/search-track', {'s': ['odd &? 日本']}))
        self.response = (200, {'data': None, 'error': ''})
        for op, endpoint in [('playpause', 'playpause'), ('next', 'next'), ('previous', 'previous')]:
            rc, out = self.run_cli({'op': op})
            self.assertEqual((rc, out['state']), (0, 'requested'))
            self.assertEqual(self.calls[-1], ('/transport/' + endpoint, {}))
        rc, out = self.run_cli({'op': 'play', 'kind': 'song', 'id': item_id})
        self.assertEqual((rc, out['state']), (0, 'requested'))
        self.assertEqual(self.calls[-1], ('/transport/play-track', {'id': [item_id]}))
        rc, out = self.run_cli({'op': 'play', 'kind': 'album', 'id': item_id})
        self.assertEqual((rc, out['state']), (0, 'requested'))
        self.assertEqual(self.calls[-1], ('/transport/play-album', {'id': [item_id], 't': ['0'], 's': ['false']}))

    def test_unified_search_queries_both_and_keeps_typed_ids(self):
        self.responses = {
            '/transport/search-track': (200, {'data': [
                {'ID':'same','Title':'Song','ArtistNames':['Band'],'Album':'Album','Year':2001},
                {'ID':'same','Title':'Duplicate'}, {'ID':'other','Title':'Other'}], 'error':''}),
            '/transport/search-album': (200, {'data': [
                {'ID':'same','Name':'Album','ArtistNames':['Band'],'Date':{'Year':2001}}], 'error':''})}
        rc, out = self.run_cli({'op':'search','query':'Band'})
        self.assertEqual(rc, 0)
        self.assertEqual(out['state'], 'ready')
        self.assertEqual([(r['kind'],r['id']) for r in out['results']], [('song','same'),('album','same'),('song','other')])
        self.assertEqual(set((p,tuple(q['s'])) for p,q in self.calls), {('/transport/search-track',('Band',)),('/transport/search-album',('Band',))})
        self.assertFalse(out['partial'])
        self.assertFalse(out['truncated'])

    def test_unified_partial_failure_is_explicit_and_sanitized(self):
        self.responses = {
            '/transport/search-track': (500, {'data':None,'error':'private-server-secret /private/path'}),
            '/transport/search-album': (200, {'data':[{'ID':'album','Name':'Album'}],'error':''})}
        rc, out = self.run_cli({'op':'search','query':'Band'})
        self.assertEqual((rc,out['state']), (0,'ready'))
        self.assertEqual([r['kind'] for r in out['results']], ['album'])
        self.assertTrue(out['partial'])
        self.assertEqual(out['failedKinds'], ['song'])
        self.assertNotIn('private', json.dumps(out))

    def test_unified_cap_does_not_starve_album_matches(self):
        self.responses = {
            '/transport/search-track': (200, {'data':[{'ID':str(i),'Title':'Song'} for i in range(41)],'error':''}),
            '/transport/search-album': (200, {'data':[{'ID':str(i),'Name':'Album'} for i in range(41)],'error':''})}
        rc, out = self.run_cli({'op':'search','query':'Band'})
        self.assertEqual(rc, 0)
        self.assertEqual(len(out['results']), 40)
        self.assertEqual([r['kind'] for r in out['results'][:2]], ['song','album'])
        self.assertTrue(out['truncated'])
        self.assertFalse(out['partial'])

    def test_unified_timeouts_share_one_bounded_read_window(self):
        self.delay = 3
        start = time.monotonic()
        rc, out = self.run_cli({'op':'search','query':'Band'})
        self.assertEqual((rc,out), (1,{'state':'timeout'}))
        self.assertLess(time.monotonic()-start, 2.8)
        self.assertEqual(len(self.calls), 2)

    def test_year_exact_id_not_first_title_match(self):
        self.response = (200, {'data': [{'ID': 'other', 'Year': 1999}, {'ID': self.status['ID'], 'Year': 2008}], 'error': ''})
        rc, out = self.run_cli({'op': 'year', 'id': self.status['ID'], 'query': 'Song'})
        self.assertEqual((rc, out), (0, {'state': 'ready', 'id': self.status['ID'], 'year': 2008}))
        self.response = (200, {'data': [{'ID': 'other', 'Year': 1999}], 'error': ''})
        rc, out = self.run_cli({'op': 'year', 'id': self.status['ID'], 'query': 'Song'})
        self.assertEqual(rc, 0)
        self.assertIsNone(out['year'])

    def test_error_absent_and_payload_limits(self):
        for code, body, expected in [(500, {'data': None, 'error': 'nothing is playing right now'}, 'idle'), (500, {'data': None, 'error': 'secret-server /private/song'}, 'ipc-error'), (200, b'not json', 'invalid-response'), (200, b'x' * (512*1024+1), 'too-large')]:
            self.response = code, body
            rc, out = self.run_cli({'op': 'status'})
            self.assertEqual(out, {'state': expected})
            self.assertEqual(rc, 0 if expected == 'idle' else 1)
        self.env['XDG_RUNTIME_DIR'] = str(self.root/'missing')
        rc, out = self.run_cli({'op': 'status'})
        self.assertEqual(out['state'], 'absent')
    def test_invalid_input_never_reaches_socket(self):
        for payload in [{'op': 'search', 'kind': 'song', 'query': ''}, {'op': 'search', 'kind': 'song', 'query': 'x'}, {'op': 'search', 'kind': 'song', 'query': 'x'*257}, {'op': 'play', 'kind': 'song', 'id': ''}, {'op': 'play', 'kind': 'album', 'id': 'a\nb'}, {'op': 'search', 'kind': 'server', 'query': 'music'}, {'op': 'quit'}]:
            rc, out = self.run_cli(payload)
            self.assertEqual(rc, 1)
        self.assertEqual(self.calls, [])
    def test_album_dates_and_result_cap(self):
        self.response = 200, {'data': [{'ID': str(i), 'Name': 'Album', 'ArtistNames': ['日本'], 'Date': {'Year': 1990}} for i in range(41)], 'error': ''}
        rc, out = self.run_cli({'op': 'search', 'kind': 'album', 'query': 'Album'})
        self.assertEqual(rc, 0)
        self.assertEqual(len(out['results']), 40)
        self.assertTrue(out['truncated'])
        self.assertEqual(out['results'][0]['year'], 1990)
        self.assertEqual(self.calls[-1][0], '/transport/search-album')
    def test_timeout_and_dribble_have_total_deadline(self):
        for delay, dribble in [(3, False), (0, True)]:
            self.delay, self.dribble = delay, dribble
            start = time.monotonic()
            rc, out = self.run_cli({'op':'status'})
            self.assertEqual((rc,out), (1, {'state':'timeout'}))
            self.assertLess(time.monotonic()-start, 2.8)
    def test_runtime_permissions_refused(self):
        self.root.chmod(0o777)
        self.assertEqual(self.run_cli({'op':'status'})[1]['state'], 'unsafe-socket')
        self.assertEqual(self.calls, [])
        self.root.chmod(0o700)
    def test_socket_type_and_symlink_refused(self):
        fake = self.root/'unsafe'
        fake.mkdir(mode=0o700)
        sock = fake/'supersonic.sock'
        sock.write_text('not socket')
        self.env['XDG_RUNTIME_DIR'] = str(fake)
        self.assertEqual(self.run_cli({'op':'status'})[1]['state'], 'unsafe-socket')
        sock.unlink(); sock.symlink_to(self.root/'supersonic.sock')
        self.assertEqual(self.run_cli({'op':'status'})[1]['state'], 'unsafe-socket')
        self.assertEqual(self.calls, [])

class Stage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.target = self.root/'plugins/alacarchy.supersonic'
        self.config = self.root/'shell.json'
        self.original = {'bar': {'layout': {'left': [{'id': 'custom', 'keep': True}], 'center': [{'id': 'omarchy.media', 'custom': 'preserve'}, {'id': 'omarchy.clock'}], 'right': []}}, 'other': 7}
        self.config.write_text(json.dumps(self.original))
    def tearDown(self): self.tmp.cleanup()
    def run_stage(self, mode):
        return subprocess.run(['python3', str(BASE/'stage_plugin.py'), '--source', '/usr/share/omarchy/shell/plugins/services/media', '--shell', str(self.config), '--'+mode, str(self.target)], text=True, capture_output=True)
    def test_enable_repeat_disable_preserves_settings(self):
        run = self.run_stage('enable')
        self.assertEqual(run.returncode, 0, run.stderr)
        enabled = json.loads(self.config.read_text())
        self.assertEqual(enabled['bar']['layout']['center'][0]['id'], 'alacarchy.supersonic')
        self.assertEqual(enabled['bar']['layout']['center'][0]['custom'], 'preserve')
        self.assertEqual(self.run_stage('check-enabled').returncode, 0)
        before = self.config.read_bytes()
        self.assertEqual(self.run_stage('enable').returncode, 0)
        self.assertEqual(self.config.read_bytes(), before)
        (self.target/'unrelated').write_text('keep')
        self.assertEqual(self.run_stage('disable').returncode, 0)
        self.assertEqual(json.loads(self.config.read_text()), self.original)
        self.assertEqual((self.target/'unrelated').read_text(), 'keep')
        self.assertEqual(self.run_stage('disable').returncode, 0)
        self.assertEqual(self.run_stage('check-disabled').returncode, 0)

    def test_custom_and_modified_plugin_preserved(self):
        self.target.mkdir(parents=True)
        (self.target/'manifest.json').write_text('{"id":"foreign"}')
        before = self.config.read_bytes()
        self.assertNotEqual(self.run_stage('enable').returncode, 0)
        self.assertEqual(self.config.read_bytes(), before)
        self.assertEqual((self.target/'manifest.json').read_text(), '{"id":"foreign"}')
        shutil.rmtree(self.target)
        self.assertEqual(self.run_stage('enable').returncode, 0)
        widget = self.target/'BarWidget.qml'
        widget.write_text(widget.read_text() + '\n// custom edit\n')
        modified = widget.read_bytes()
        self.assertNotEqual(self.run_stage('check-enabled').returncode, 0)
        self.assertNotEqual(self.run_stage('enable').returncode, 0)
        self.assertEqual(widget.read_bytes(), modified)
        self.assertEqual(self.run_stage('disable').returncode, 0)
        self.assertEqual(widget.read_bytes(), modified)
    def test_no_stock_widget_add_remove(self):
        self.original['bar']['layout']['center'] = [{'id':'custom.media', 'custom':'keep'}]
        self.config.write_text(json.dumps(self.original))
        self.assertEqual(self.run_stage('enable').returncode, 0)
        self.assertEqual(self.run_stage('disable').returncode, 0)
        self.assertEqual(json.loads(self.config.read_text()), self.original)
    def test_invalid_config_and_unmanaged_entries_refused(self):
        for config in [{'bar': None}, {'bar': {'layout': {'center': ['not object']}}}, {'bar': {'layout': {'center': [{'id':'alacarchy.supersonic'}]}}}]:
            with self.subTest(config=config):
                self.config.write_text(json.dumps(config))
                before = self.config.read_bytes()
                for mode in ('enable','disable'):
                    run = self.run_stage(mode)
                    self.assertNotEqual(run.returncode, 0)
                    self.assertNotIn('Traceback', run.stderr)
                    self.assertEqual(self.config.read_bytes(), before)
                self.assertFalse(self.target.exists())
    def test_symlink_config_refused(self):
        original = self.root/'original.json'
        self.config.rename(original)
        self.config.symlink_to(original)
        before = original.read_bytes()
        self.assertNotEqual(self.run_stage('enable').returncode, 0)
        self.assertEqual(original.read_bytes(), before)
        self.assertFalse(self.target.exists())

    def test_unsupported_source_and_plugin_parent_symlink(self):
        source = self.root/'source'
        source.mkdir()
        (source/'manifest.json').write_text('{"id":"foreign"}')
        run = subprocess.run(['python3', str(BASE/'stage_plugin.py'), '--source', str(source), str(self.target)], text=True, capture_output=True)
        self.assertNotEqual(run.returncode, 0)
        self.assertFalse(self.target.exists())
        outside = self.root/'outside'
        outside.mkdir()
        self.target.parent.symlink_to(outside, target_is_directory=True)
        self.assertNotEqual(self.run_stage('enable').returncode, 0)
        self.assertEqual(list(outside.iterdir()), [])
        self.assertEqual(json.loads(self.config.read_text()), self.original)

if __name__ == '__main__': unittest.main()
