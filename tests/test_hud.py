"""Exercise the actual inotify adapter with a fake renderer, without a desktop."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('inotifywait'), 'inotify-tools required')
class HudLifecycleTests(unittest.TestCase):
    def wait_for(self, predicate, message, timeout=3):
        deadline = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() >= deadline:
                self.fail(message)
            time.sleep(.01)

    def watching(self, process):
        # Wait for a real kernel watch, not an arbitrary startup delay.
        try:
            children = Path(f'/proc/{process.pid}/task/{process.pid}/children').read_text().split()
            for child in children:
                for info in Path(f'/proc/{child}/fdinfo').iterdir():
                    if 'inotify wd:' in info.read_text():
                        return True
        except (FileNotFoundError, ProcessLookupError):
            pass
        return False

    def identity(self, pid):
        return Path(f'/proc/{pid}/stat').read_text().rsplit(') ', 1)[1].split()[19]

    def translate_state(self, root, lines):
        state = root/'state'; state.mkdir()
        run = state/'translate.abc'; run.mkdir()
        (state/'translate.current').symlink_to(run, target_is_directory=True)
        (run/'worker.pid').write_text(f'{os.getpid()}\n')
        (run/'worker.pid.start').write_text(self.identity(os.getpid()) + '\n')
        (state/'translate.subtitles').write_text(json.dumps({'lines': lines}))
        return state

    def frame(self, env, *args):
        feed = subprocess.Popen([str(ROOT/'integrations/eww/eww-feed'), *args], env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        try:
            return json.loads(feed.stdout.readline())
        finally:
            feed.kill(); feed.wait(); feed.stdout.close()

    def spans(self, row):
        """(attributes, text) of each span in a row of the translate window, after the gutter."""
        gutter, rest = row.split('</span>', 1)
        return [(attrs, text) for attrs, text in re.findall(r'<span ([^>]*)>([^<]*)</span>', rest)]

    def phrases(self, count):
        return [{'n': n, 'dutch': f'zin {n}', 'english': f'sentence {n}', 'live': False, 'done': True,
                 'pieces': [{'text': f'sentence {n}', 'start': 1.0, 'end': 2.0}]} for n in range(1, count + 1)]

    def test_translate_window_shows_what_is_on_air(self):
        with tempfile.TemporaryDirectory(prefix='talkey-hud-test-') as tmp:
            root = Path(tmp)
            now = time.time()
            state = self.translate_state(root, [
                {'n': 1, 'dutch': 'Ik bel je morgen op, want ik heb nu geen tijd.', 'live': False, 'done': True,
                 'english': "I'll call you tomorrow, because I don't have time now.",
                 'pieces': [{'text': "I'll call you tomorrow,", 'start': now - 3, 'end': now - 1.5},
                            {'text': "because I don't have time now.", 'start': now - 1.2, 'end': now - .2}]},
                {'n': 2, 'dutch': 'Tot morgen & zo', 'english': 'See you tomorrow, then <3', 'live': True, 'done': False,
                 'pieces': [{'text': 'See you tomorrow,', 'start': now - .1, 'end': now + 30},
                            {'text': 'then <3', 'start': now + 30, 'end': None}]}])
            # The worker's latest microphone audio, at about -6 dBFS.
            (state/'translate.raw').write_bytes((16000).to_bytes(2, 'little', signed=True) * 2048)
            env = dict(os.environ, TALKEY_STATE_DIR=str(state), TALKEY_CONFIG=str(root/'config'),
                       TALKEY_DATA_DIR=str(ROOT), TALKEY_LABEL_MEMO='Square')
            frame = self.frame(env, 'translate')
            self.assertEqual(frame['headertext'], 'NL → EN · HEARING')
            self.assertEqual(frame['status'], '▶ ON AIR')
            self.assertEqual(frame['footertext'], 'double-tap Square to stop')
            self.assertEqual(frame['zone'], 'loud')
            self.assertGreater(frame['level'], 0)
            rows = frame['rows'].split('\n')
            # A fixed number of rows, newest at the bottom, so the window keeps its size.
            self.assertEqual(len(rows), 14)
            self.assertEqual([self.spans(row) for row in rows[:10]], [[]] * 10)
            dutch, said, hearing, on_air = (self.spans(row) for row in rows[10:])
            self.assertEqual(dutch, [('foreground="#9aa3ab"', 'Ik bel je morgen op, want ik heb nu geen tijd.')])
            self.assertEqual([text for _, text in said], ["I'll call you tomorrow, because I don't have time now."])
            self.assertNotIn('background', said[0][0])
            self.assertEqual(hearing, [('foreground="#7f868c" style="italic"', 'Tot morgen &amp; zo')])
            self.assertIn('background', on_air[0][0])
            self.assertEqual(on_air[0][1], 'See')
            self.assertIn('background', on_air[1][0])
            self.assertEqual(on_air[1][1], ' you tomorrow,')
            # Not made yet or not yet due: queued, not on air.
            self.assertEqual(on_air[2], ('foreground="#6b737a" style="italic"', 'then &lt;3'))
            self.assertEqual(on_air[3][1], '…')
            self.assertIn('» ', rows[-2].split('</span>')[0])
            self.assertIn('→ ', rows[-1].split('</span>')[0])

    def test_the_wheel_scrolls_back_through_the_session(self):
        with tempfile.TemporaryDirectory(prefix='talkey-hud-test-') as tmp:
            root = Path(tmp)
            state = self.translate_state(root, self.phrases(12))
            env = dict(os.environ, TALKEY_STATE_DIR=str(state), TALKEY_CONFIG=str(root/'config'),
                       TALKEY_DATA_DIR=str(ROOT))

            def wheel(frame, direction):
                subprocess.run([str(ROOT/'integrations/eww/eww-feed'), 'scroll', direction, frame['up'], frame['down']],
                               env=env, check=True)
                return self.frame(env, 'translate')

            def bottom(frame):
                return self.spans(frame['rows'].split('\n')[-1])[0][1]

            live = self.frame(env, 'translate')
            self.assertEqual(bottom(live), 'sentence 12')
            self.assertEqual((live['up'], live['status']), ('11', ''))
            self.assertIn('scroll up', live['footertext'])
            back = wheel(wheel(live, 'up'), 'up')
            self.assertEqual((state/'translate.scroll').read_text(), '10\n')
            self.assertEqual(bottom(back), 'sentence 10')
            self.assertEqual(back['status'], '↓ 2 NEWER')
            self.assertIn('scroll down', back['footertext'])
            # A new phrase does not move a view that was scrolled back.
            (state/'translate.subtitles').write_text(json.dumps({'lines': self.phrases(13)}))
            self.assertEqual(bottom(self.frame(env, 'translate')), 'sentence 10')
            for _ in range(10):
                back = wheel(back, 'up')
            # The top of the session stops the wheel.
            self.assertEqual(back['up'], 'stay')
            self.assertIn('zin 1', back['rows'].split('\n')[0])
            for _ in range(20):
                back = wheel(back, 'down')
            self.assertFalse((state/'translate.scroll').exists())
            self.assertEqual(bottom(back), 'sentence 13')

    def test_translation_opens_its_own_window_and_dictation_takes_over(self):
        with tempfile.TemporaryDirectory(prefix='talkey-hud-test-') as tmp:
            root = Path(tmp)
            state = self.translate_state(root, self.phrases(1))
            calls = root/'calls'
            fake = root/'eww'
            fake.write_text('''#!/usr/bin/env python3
import json, os, pathlib, sys
args = sys.argv[3:]
opened = pathlib.Path(os.environ['EWW_CALLS']).with_name('open')
windows = set(opened.read_text().split()) if opened.exists() else set()
if args[:1] == ['active-windows']:
    print(''.join(f'{name}: {name}\\n' for name in sorted(windows)), end='')
elif args[:1] in (['open'], ['close']):
    with open(os.environ['EWW_CALLS'], 'a') as output:
        output.write(json.dumps(args[:2]) + '\\n')
    (windows.add if args[0] == 'open' else windows.discard)(args[1])
    opened.write_text(' '.join(windows))
''')
            fake.chmod(0o755)
            env = dict(os.environ, TALKEY_STATE_DIR=str(state), TALKEY_CONFIG=str(root/'config'),
                       TALKEY_DATA_DIR=str(ROOT), TALKEY_EWW=str(fake), EWW_CALLS=str(calls),
                       TALKEY_EWW_LINGER='.1')

            def shown():
                path = root/'open'
                return set(path.read_text().split()) if path.exists() else set()

            with (root/'stderr').open('w') as stderr:
                process = subprocess.Popen([str(ROOT/'bin/talkey-hud')], env=env,
                                           stdout=subprocess.DEVNULL, stderr=stderr)
                try:
                    self.wait_for(lambda: self.watching(process), 'HUD watcher never started')
                    (state/'translate.scroll').write_text('1\n')
                    (state/'translate.showing').touch()
                    self.wait_for(lambda: shown() == {'talkeytranslate'}, 'hearing did not open the translate window')
                    self.assertFalse((state/'translate.scroll').exists(), 'a new window opened scrolled back')
                    (state/'session.changed').write_text('changed\n')
                    time.sleep(.3)
                    self.assertEqual(shown(), {'talkeytranslate'}, 'a state change closed a showing window')
                    # Push-to-talk while translating: the dictation HUD takes the place.
                    (state/'dictate.pid').write_text('1\n')
                    (state/'session.changed').write_text('changed\n')
                    self.wait_for(lambda: shown() == {'talkeyhud'}, 'dictation did not replace the translate window')
                    (state/'dictate.pid').unlink()
                    self.wait_for(lambda: shown() == {'talkeytranslate'}, 'translation did not come back')
                    (state/'translate.showing').unlink()
                    self.wait_for(lambda: shown() == set(), 'the window stayed after translation went quiet')
                finally:
                    process.terminate(); process.wait()

    def test_take_events_open_keep_and_close_hud(self):
        with tempfile.TemporaryDirectory(prefix='talkey-hud-test-') as tmp:
            root = Path(tmp)
            state = root/'state'; state.mkdir()
            take = state/'takes'/'one'; take.mkdir(parents=True)
            (state/'dictate.current').symlink_to(take, target_is_directory=True)
            for name in ['dictate.pid', 'dictate.pending']:
                (state/name).symlink_to(f'dictate.current/{name}')
            # Subsequent events must wake the watcher too, not just file creation.
            (state/'session.changed').touch()
            calls = root/'calls'
            fake = root/'eww'
            fake.write_text('''#!/usr/bin/env python3
import json, os, sys
with open(os.environ['EWW_CALLS'], 'a') as output:
    output.write(json.dumps(sys.argv[1:]) + '\\n')
''')
            fake.chmod(0o755)
            env = dict(os.environ, TALKEY_STATE_DIR=str(state), TALKEY_CONFIG=str(root/'config'),
                       TALKEY_DATA_DIR=str(ROOT), TALKEY_EWW=str(fake), EWW_CALLS=str(calls),
                       TALKEY_EWW_LINGER='.1')

            def count(action):
                if not calls.exists():
                    return 0
                return sum(args[2:4] == [action, 'talkeyhud']
                           for args in map(json.loads, calls.read_text().splitlines()))

            def changed():
                result = subprocess.run(['bash', '-c',
                    '. "$1/lib/session.sh"; STATE_DIR="$2"; state_changed',
                    'test', str(ROOT), str(state)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)

            with (root/'stderr').open('w') as stderr:
                process = subprocess.Popen([str(ROOT/'bin/talkey-hud')], env=env,
                                           stdout=subprocess.DEVNULL, stderr=stderr)
                try:
                    self.wait_for(lambda: self.watching(process), 'HUD watcher never started')
                    (take/'dictate.pid').write_text(str(os.getpid()))
                    changed()
                    self.wait_for(lambda: count('open') >= 1, 'recording did not open the HUD')
                    (take/'dictate.pending').touch()
                    (take/'dictate.pid').unlink()
                    changed()
                    # Inference outlasts the close delay; pending must keep the HUD open.
                    time.sleep(.2)
                    self.assertEqual(count('close'), 0)
                    (take/'dictate.pending').unlink()
                    changed()
                    self.wait_for(lambda: count('close') >= 1, 'finished take did not close the HUD')
                    opens = count('open')
                    (take/'dictate.pid').write_text(str(os.getpid()))
                    changed()
                    self.wait_for(lambda: count('open') > opens, 'next take did not reopen the HUD')
                finally:
                    process.terminate()
                    process.wait(timeout=5)
