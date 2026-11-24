"""Black-box key events with fake devices; no microphone, desktop, or model needed."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
FAKE = r'''#!/usr/bin/env python3
import json, os, pathlib, signal, sys, time
root=pathlib.Path(os.environ['TEST_ROOT'])
name=pathlib.Path(sys.argv[0]).name
args=sys.argv[1:]
if name=='ffmpeg':
    if 'pulse' in args:
        signal.signal(signal.SIGINT, lambda *_: sys.exit(0))
        rates=[int(args[i+1]) for i,a in enumerate(args) if a=='-ar']
        extra=[(os.fdopen(int(a[5:]),'wb'),rates[1]//rates[0]) for a in args if a.startswith('pipe:')]
        while True:
            chunk=b'\0\1'*1600
            sys.stdout.buffer.write(chunk); sys.stdout.buffer.flush()
            for out,ratio in extra: out.write(chunk.replace(b'\1',b'\2')*ratio); out.flush()
            time.sleep(.03)
    else:
        with (root/'ffmpeg-args').open('a') as f: f.write(json.dumps(args)+'\n')
        if (root/'fail-wrap').exists(): sys.exit(1)
        pathlib.Path(args[-1]).write_bytes(pathlib.Path(args[args.index('-i')+1]).read_bytes())
elif name=='curl':
    if '-o' in args: sys.exit(0)
    with (root/'curl-args').open('a') as f: f.write(json.dumps(args)+'\n')
    (root/'inference-started').touch()
    while (root/'wait-inference').exists(): time.sleep(.01)
    if (root/'fail-http').exists(): sys.exit(22)
    if (root/'bad-json').exists(): print('{"error":"broken"}'); sys.exit(0)
    text='' if (root/'silence').exists() else 'hello world'
    print(json.dumps({'text':text,'segments':[{'avg_logprob':-.1}]}))
elif name=='xdotool':
    if args[0]=='getactivewindow': print('123')
    elif args[0]=='windowactivate' and (root/'fail-focus').exists(): sys.exit(1)
    else:
        with (root/'keys').open('a') as f: f.write(json.dumps(args)+'\n')
        if args[0]=='type' and (root/'fail-type').exists(): sys.exit(1)
'''


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='talkey-test-')
        self.root = Path(self.tmp.name)
        self.state = self.root/'state'
        self.commands = self.root/'bin'; self.commands.mkdir()
        for name in ['ffmpeg','curl','xdotool','notify-send']:
            p=self.commands/name; p.write_text(FAKE); p.chmod(0o755)
        self.env = dict(os.environ, TEST_ROOT=str(self.root),
                        PATH=f'{self.commands}:{os.environ["PATH"]}',
                        TALKEY_STATE_DIR=str(self.state), TALKEY_CONFIG=str(self.root/'config'),
                        TALKEY_ARCHIVE_DIR=str(self.root/'archive'), TALKEY_ARCHIVE_TITLE='0',
                        TALKEY_STREAM='0', TALKEY_SOUNDS='0', TALKEY_HOLD_MS='30',
                        TALKEY_DOUBLE_TAP_MS='0', XDG_STATE_HOME=str(self.root/'persistent'),
                        TALKEY_VIDEO_SYSFS=str(self.root/'video4linux'))
        self.workers=[]

    def tearDown(self):
        for process in self.workers:
            if process.poll() is None: process.kill()
            process.communicate(timeout=5)
        if self.state.exists():
            for p in self.state.glob('takes/*/*.pid'):
                try: os.kill(int(p.read_text()), 15)
                except (ValueError, ProcessLookupError): pass
        self.tmp.cleanup()

    def run_key(self,*args,check=True):
        result=subprocess.run([str(ROOT/'bin/talkey'),*args],env=self.env,
                              capture_output=True,text=True,timeout=10)
        if check: self.assertEqual(result.returncode,0,result.stderr)
        return result

    def start(self):
        self.run_key('dictate','start')
        take=(self.state/'dictate.current').resolve()
        self.wait_for(lambda: (take/'dictate.raw').stat().st_size>0)
        return take

    def wait_for(self,predicate):
        deadline=time.monotonic()+5
        while not predicate():
            if time.monotonic()>deadline: self.fail('condition timed out')
            time.sleep(.01)

    def keys(self):
        p=self.root/'keys'
        return [json.loads(s) for s in p.read_text().splitlines()] if p.exists() else []

    def test_success_and_submit_once(self):
        self.env['TALKEY_SUBMIT']='1'
        take=self.start()
        self.run_key('dictate','stop')
        self.assertEqual(sum(k[-1]=='Return' for k in self.keys()),1)
        self.assertEqual(sum(k[0]=='type' for k in self.keys()),1)
        self.assertEqual((take/'final.txt').read_text().strip(),'hello world')
        self.assertEqual(len(list((self.root/'archive').glob('*.wav'))),1)
        self.assertEqual(len(list((self.root/'archive').glob('*.txt'))),1)
        self.assertFalse((take/'dictate.pending').exists())

    def test_context_is_dropped_only_without_a_prompt(self):
        def fields():
            args=json.loads((self.root/'curl-args').read_text().splitlines()[-1])
            return [args[i+1] for i,arg in enumerate(args) if arg=='-F']
        vocabulary=self.root/'vocabulary.txt'; vocabulary.write_text('SonarQube\n')
        self.env['TALKEY_PROMPT_FILE']=str(vocabulary)
        self.start(); self.run_key('dictate','stop')
        self.assertIn('prompt=SonarQube',fields())
        self.assertNotIn('max_context=0',fields())
        self.env['TALKEY_VOCAB_MAX_S']='0'
        self.start(); self.run_key('dictate','stop')
        self.assertIn('max_context=0',fields())
        self.assertFalse(any(field.startswith('prompt=') for field in fields()))

    def test_long_text_types_within_cap(self):
        def delay():
            typed=[k for k in self.keys() if k[0]=='type'][-1]
            return typed[typed.index('--delay')+1]
        self.start(); self.run_key('dictate','stop')
        self.assertEqual(delay(),'8')
        self.env['TALKEY_TYPE_MAX_MS']='44'   # 'hello world' is 11 characters
        self.start(); self.run_key('dictate','stop')
        self.assertEqual(delay(),'4')

    def test_failure_never_submits(self):
        for failure in ['fail-wrap','fail-http','bad-json','silence','fail-focus','fail-type']:
            with self.subTest(failure=failure):
                (self.root/failure).touch()
                self.start(); self.run_key('submit')
                self.run_key('dictate','stop',check=False)
                self.assertFalse(any(k[-1]=='Return' for k in self.keys()))
                (self.root/failure).unlink()

    def test_cancel_is_recoverable(self):
        self.start(); self.run_key('submit'); self.run_key('submit')
        self.run_key('dictate','stop')
        self.assertFalse(self.keys())
        self.assertEqual(len(list((self.root/'archive/cancelled').glob('*.wav'))),1)

    def test_disabled_archive_does_not_keep_cancelled(self):
        self.env['TALKEY_ARCHIVE']='0'
        take=self.start(); self.run_key('submit'); self.run_key('submit')
        self.run_key('dictate','stop')
        self.assertFalse((self.root/'archive').exists())
        self.assertFalse((take/'dictate.raw').exists())

    def test_failed_archive_retains_audio(self):
        take=self.start(); (self.root/'fail-wrap').touch()
        self.run_key('dictate','stop',check=False)
        self.assertGreater((take/'dictate.raw').stat().st_size,0)

    def test_new_take_survives_old_finish(self):
        old=self.start(); self.run_key('submit')
        (self.root/'wait-inference').touch()
        p=subprocess.Popen([str(ROOT/'bin/talkey'),'dictate','stop'],env=self.env,
                           stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        self.workers.append(p)
        self.wait_for(lambda:(self.root/'inference-started').exists())
        new=self.start(); self.run_key('submit')
        (self.root/'wait-inference').unlink()
        _,error=p.communicate(timeout=10)
        self.assertEqual(p.returncode,0,error)
        self.assertNotEqual(old,new)
        self.assertTrue((new/'dictate.presstime').exists())
        self.assertTrue((new/'submit.requested').exists())
        self.assertFalse(self.keys())
        self.assertEqual((old/'final.txt').read_text().strip(),'hello world')
        self.run_key('dictate','stop')
        self.assertEqual(sum(k[-1]=='Return' for k in self.keys()),1)

    def test_language_survives_release_from_another_binding(self):
        self.env['TALKEY_LANG2']='nl'
        self.run_key('dictate-alt','start')
        take=(self.state/'dictate.current').resolve()
        self.assertEqual((take/'language').read_text().strip(),'nl')
        self.run_key('dictate','stop')

    def test_repeated_stop_is_harmless(self):
        self.start(); self.run_key('dictate','stop'); self.run_key('dictate','stop')
        self.assertEqual(sum(k[0]=='type' for k in self.keys()),1)

    def test_submit_cycle_can_disable_default_send(self):
        self.env['TALKEY_SUBMIT']='1'
        self.start(); self.run_key('submit'); self.run_key('submit')
        self.run_key('dictate','stop')
        self.assertFalse(any(k[-1]=='Return' for k in self.keys()))
        self.assertTrue(any(k[0]=='type' for k in self.keys()))

    def test_empty_capture_never_submits(self):
        take=self.start(); self.run_key('submit')
        os.kill(int((take/'dictate.pid').read_text()),15)
        time.sleep(.05)
        (take/'dictate.raw').write_bytes(b'')
        self.run_key('dictate','stop')
        self.assertFalse(self.keys())

    def test_stopped_recorder_keeps_captured_audio(self):
        take=self.start()
        os.kill(int((take/'dictate.pid').read_text()),15)
        time.sleep(.05)
        self.run_key('dictate','stop')
        self.assertTrue(any(k[0]=='type' for k in self.keys()))

    def test_restart_keeps_take_and_press_timestamp(self):
        self.env['TALKEY_DOUBLE_TAP_MS']='2000'
        old=self.start(); timestamp=(old/'dictate.presstime').read_text()
        self.run_key('memo','press'); self.run_key('memo','release')
        self.run_key('memo','press')
        new=(self.state/'dictate.current').resolve()
        self.assertNotEqual(old,new)
        self.assertEqual((new/'dictate.presstime').read_text(),timestamp)
        self.assertEqual(len(list((self.root/'archive/cancelled').glob('*.wav'))),1)
        self.run_key('dictate','stop')

    def test_cancel_during_inference(self):
        self.start(); (self.root/'wait-inference').touch()
        p=subprocess.Popen([str(ROOT/'bin/talkey'),'dictate','stop'],env=self.env,
                           stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        self.workers.append(p)
        self.wait_for(lambda:(self.root/'inference-started').exists())
        self.run_key('submit'); self.run_key('submit')
        (self.root/'wait-inference').unlink()
        _,error=p.communicate(timeout=10)
        self.assertEqual(p.returncode,0,error)
        self.assertFalse(self.keys())
        self.assertEqual(len(list((self.root/'archive/cancelled').glob('*.wav'))),1)

    def test_legacy_recording_can_finish(self):
        take=self.start()
        # Move the active capture into the pre-refactor root layout.
        for link in list(self.state.iterdir()):
            if link.is_symlink(): link.unlink()
        for name in ['dictate.raw','dictate.pid','dictate.presstime']:
            (take/name).rename(self.state/name)
        self.run_key('dictate','stop')
        self.assertTrue(any(k[0]=='type' for k in self.keys()))
        self.assertFalse((self.state/'dictate.pid').exists())


if __name__=='__main__': unittest.main()
