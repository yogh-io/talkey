"""Exercise the actual inotify adapter with a fake renderer, without a desktop."""
import json
import os
from pathlib import Path
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

    def test_translation_keeps_the_hud_open_and_feeds_subtitles(self):
        with tempfile.TemporaryDirectory(prefix='talkey-hud-test-') as tmp:
            root = Path(tmp)
            state = root/'state'; state.mkdir()
            run = state/'translate.abc'; run.mkdir()
            (state/'translate.current').symlink_to(run, target_is_directory=True)
            (run/'worker.pid').write_text(f'{os.getpid()}\n')
            (run/'worker.pid.start').write_text(self.identity(os.getpid()) + '\n')
            (state/'translate.subtitles').write_text(json.dumps({'lines': [
                {'dutch': 'Ik bel je morgen op.', 'english': 'I will call you tomorrow.', 'live': False},
                {'dutch': 'Tot', 'english': '', 'live': True}]}))
            # The worker's latest microphone audio, at about -6 dBFS.
            (state/'translate.raw').write_bytes((16000).to_bytes(2, 'little', signed=True) * 2048)
            env = dict(os.environ, TALKEY_STATE_DIR=str(state), TALKEY_CONFIG=str(root/'config'),
                       TALKEY_DATA_DIR=str(ROOT), TALKEY_LABEL_MEMO='Square')
            feed = subprocess.Popen([str(ROOT/'integrations/eww/eww-feed')], env=env,
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
            try:
                frame = json.loads(feed.stdout.readline())
            finally:
                feed.kill(); feed.wait(); feed.stdout.close()
            self.assertEqual(frame['header'], 'translating')
            self.assertEqual(frame['headertext'], 'NL → EN · HEARING')
            self.assertEqual(frame['transcript'].splitlines(),
                             ['» Ik bel je morgen op.', '→ I will call you tomorrow.', '» Tot', '→ …'])
            self.assertEqual(frame['footertext'], 'double-tap Square to stop')
            self.assertEqual(frame['zone'], 'loud')
            self.assertGreater(frame['level'], 0)

            calls = root/'calls'
            fake = root/'eww'
            fake.write_text('''#!/usr/bin/env python3
import json, os, sys
with open(os.environ['EWW_CALLS'], 'a') as output:
    output.write(json.dumps(sys.argv[1:]) + '\\n')
''')
            fake.chmod(0o755)
            env.update(TALKEY_EWW=str(fake), EWW_CALLS=str(calls), TALKEY_EWW_LINGER='.1')

            def count(action):
                if not calls.exists():
                    return 0
                return sum(args[2:4] == [action, 'talkeyhud']
                           for args in map(json.loads, calls.read_text().splitlines()))

            with (root/'stderr').open('w') as stderr:
                process = subprocess.Popen([str(ROOT/'bin/talkey-hud')], env=env,
                                           stdout=subprocess.DEVNULL, stderr=stderr)
                try:
                    self.wait_for(lambda: self.watching(process), 'HUD watcher never started')
                    (state/'translate.showing').touch()
                    self.wait_for(lambda: count('open') >= 1, 'hearing did not open the HUD')
                    (state/'session.changed').write_text('changed\n')
                    time.sleep(.3)
                    self.assertEqual(count('close'), 0, 'a state change closed a showing HUD')
                    (state/'translate.showing').unlink()
                    self.wait_for(lambda: count('close') >= 1, 'the HUD stayed after translation went quiet')
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
