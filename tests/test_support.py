import io
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import signal
import sys
import time
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import wave

ROOT=Path(__file__).resolve().parents[1]
loader=importlib.machinery.SourceFileLoader('live_pump',str(ROOT/'bin/talkey-live-pump'))
spec=importlib.util.spec_from_loader(loader.name,loader)
pump=importlib.util.module_from_spec(spec); loader.exec_module(pump)


class PumpTests(unittest.TestCase):
    def test_agreement_requires_two_passes(self):
        agreement=pump.Agreement()
        self.assertIsNone(agreement.update('Hello world'))
        self.assertEqual(agreement.update('hello world again'),'hello world')
        self.assertIsNone(agreement.update('hello there'))
        self.assertIsNone(agreement.update('hello there again'))
        self.assertEqual(agreement.update('hello there again today'),'hello there again')

    def test_wav_uses_configured_format(self):
        data=pump.wav_bytes(b'\x00'*88201,44100,2)
        with wave.open(io.BytesIO(data),'rb') as wav:
            self.assertEqual(wav.getframerate(),44100)
            self.assertEqual(wav.getnchannels(),2)
            self.assertEqual(wav.getnframes(),22050)

    def test_stop_during_inference_cannot_overwrite_final(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); raw=root/'raw'; raw.write_bytes(b'\0'*64000)
            transcript=root/'transcript'; transcript.write_text('FINAL')
            pidfile=root/'pid'; pidfile.write_text(str(os.getpid()))
            worker=pump.Pump(raw,transcript,pidfile)
            worker.agreement.update('stale preview')
            def infer(_):
                pidfile.unlink()
                return 'stale preview'
            with patch.object(worker,'infer',side_effect=infer): worker.run()
            self.assertEqual(transcript.read_text(),'FINAL')

    def test_signal_interrupts_blocking_request(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); (root/'raw').write_bytes(b'\0'*64000)
            (root/'pid').write_text(str(os.getpid()))
            (root/'transcript').write_text('FINAL')
            script="""import runpy, sys, time, pathlib
ns=runpy.run_path(sys.argv[1])
marker=pathlib.Path(sys.argv[2])
def blocked(*args,**kwargs):
    marker.touch()
    time.sleep(20)
ns['urllib'].request.urlopen=blocked
sys.argv=[sys.argv[1],sys.argv[3],sys.argv[4],sys.argv[5]]
ns['main']()
"""
            process=subprocess.Popen([sys.executable,'-c',script,str(ROOT/'bin/talkey-live-pump'),
                                      str(root/'started'),str(root/'raw'),str(root/'transcript'),str(root/'pid')],
                                     stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            try:
                deadline=time.monotonic()+5
                while not (root/'started').exists():
                    self.assertIsNone(process.poll())
                    self.assertLess(time.monotonic(),deadline)
                    time.sleep(.01)
                process.send_signal(signal.SIGTERM)
                _,error=process.communicate(timeout=2)
                self.assertEqual(process.returncode,0,error)
                self.assertEqual((root/'transcript').read_text(),'FINAL')
            finally:
                if process.poll() is None: process.kill()
                process.communicate()

    def test_http_failure_is_not_a_transcript(self):
        from urllib.error import HTTPError
        with patch.object(pump.urllib.request,'urlopen',side_effect=HTTPError('url',500,'broken',{},None)):
            with self.assertRaises(HTTPError):
                pump.transcribe(b'\0'*32000,'http://localhost','en','',16000,1)

    def test_pid_reuse_invalidates_worker(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); pid=root/'pid'; pid.write_text(str(os.getpid()))
            worker=pump.Pump(root/'raw',root/'transcript',pid)
            with patch.object(pump,'recorder_identity',return_value=(str(os.getpid()),'different')):
                self.assertFalse(worker.alive())


class ShellSupportTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='talkey-support-')
        self.root=Path(self.tmp.name)
        self.env=dict(os.environ,TALKEY_CONFIG=str(self.root/'config'),
                      TALKEY_STATE_DIR=str(self.root/'state'),TALKEY_DATA_DIR=str(ROOT))
    def tearDown(self): self.tmp.cleanup()
    def shell(self,script,*args):
        return subprocess.run(['bash','-c',script,'test',str(ROOT),*map(str,args)],env=self.env,
                              capture_output=True,text=True,timeout=10)

    def test_config_roundtrip_and_no_evaluation(self):
        value='A "quoted" # label \\ $HOME $(touch never) | &'
        script='''set -euo pipefail
. "$1/lib/config.sh"
. "$1/lib/setup/config.sh"
CONFIG_DIR="$(dirname "$TALKEY_CONFIG")"; DATA_DIR="$1"
ok() { :; }
config_set TALKEY_TEST "$2"
config_load "$TALKEY_CONFIG"
printf '%s' "$TALKEY_TEST"
'''
        result=self.shell(script,value)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stdout,value)

    def test_config_last_assignment_and_environment_priority(self):
        (self.root/'config').write_text('TALKEY_TEST=first\nTALKEY_TEST=last')
        script='. "$1/lib/config.sh"; config_load "$TALKEY_CONFIG"; printf "%s" "$TALKEY_TEST"'
        self.assertEqual(self.shell(script).stdout,'last')
        self.env['TALKEY_TEST']='environment'
        self.assertEqual(self.shell(script).stdout,'environment')

    def test_bad_quotes_are_rejected(self):
        (self.root/'config').write_text('TALKEY_TEST="unfinished')
        result=self.shell('. "$1/lib/config.sh"; config_load "$TALKEY_CONFIG"')
        self.assertNotEqual(result.returncode,0)
        self.assertIn(':1',result.stderr)

    def test_state_directory_permissions(self):
        (self.root/'state').mkdir(mode=0o755)
        result=self.shell('SCRIPT_DIR="$1/bin"; . "$1/lib/common.sh"')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual((self.root/'state').stat().st_mode&0o777,0o700)

    def test_state_symlink_rejected(self):
        target=self.root/'target'; target.mkdir()
        (self.root/'state').symlink_to(target)
        self.assertNotEqual(self.shell('SCRIPT_DIR="$1/bin"; . "$1/lib/common.sh"').returncode,0)

    def test_process_identity_mismatch_is_not_signaled(self):
        sleeper=subprocess.Popen(['sleep','30'])
        try:
            pidfile=self.root/'pid'; pidfile.write_text(str(sleeper.pid))
            (self.root/'pid.start').write_text('wrong')
            result=self.shell('. "$1/lib/session.sh"; process_stop "$2"',pidfile)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIsNone(sleeper.poll())
        finally: sleeper.terminate(); sleeper.wait()

    def test_service_uses_shared_url(self):
        self.env['TALKEY_WHISPER_PORT']='9876'
        result=self.shell('. "$1/lib/whisper.sh"; talkey_whisper_url')
        self.assertEqual(result.stdout,'http://127.0.0.1:9876/inference')

    def test_checkout_hud_launcher(self):
        fake=self.root/'eww'
        fake.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$@" >> "$TEST_ARGS"\n')
        fake.chmod(0o755)
        self.env.update(TALKEY_EWW=str(fake),TEST_ARGS=str(self.root/'args'))
        # The checkout launcher resolves the adapter without starting a daemon.
        result=subprocess.run([str(ROOT/'bin/talkey-hud'),'stop'],env=self.env,capture_output=True,text=True,timeout=5)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('kill',(self.root/'args').read_text())

    def test_package_contains_runnable_modules_and_launchers(self):
        stage=self.root/'stage'; source=self.root/'source'; source.mkdir()
        (source/'talkey-git').symlink_to(ROOT,target_is_directory=True)
        script='''set -e
. "$1/packaging/aur/PKGBUILD"
pkgdir="$2"; srcdir="$3"; cd "$srcdir"; package
'''
        result=self.shell(script,stage,source)
        self.assertEqual(result.returncode,0,result.stderr)
        for name in ['controller.sh','session.sh','auto.sh','setup/keys.sh','config.sh','whisper.sh','hud.sh',
                     'smart_turn.py','speech.py','commands.py','commands.sh','gestures.sh',
                     'vendor/smart_turn_features.py','vendor/LICENSE.pipecat',
                     'vendor/LICENSE.transformers']:
            self.assertTrue((stage/'usr/share/talkey/lib'/name).is_file(),name)
        env=dict(self.env,TALKEY_DATA_DIR=str(stage/'usr/share/talkey'))
        result=subprocess.run([str(stage/'usr/bin/talkey-setup'),'--help'],env=env,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('talkey-setup',result.stdout)
        self.assertTrue(os.access(stage/'usr/share/talkey/integrations/eww/talkey-hud',os.X_OK))
        self.assertTrue(os.access(stage/'usr/bin/talkey-auto-worker',os.X_OK))
        self.assertTrue((stage/'usr/share/talkey/config/commands.d/enter-on.json').is_file())
        for tone in ('auto-on', 'auto-off'):
            self.assertTrue((stage/f'usr/share/talkey/sounds/{tone}.wav').is_file())

    def test_recursive_i3_includes_handle_cycles_and_globs(self):
        main=self.root/'i3'; child=self.root/'child.conf'; grand=self.root/'grand'
        main.write_text('include *.conf\n')
        child.write_text('include grand\n')
        grand.write_text('include i3\nbindcode 66 exec example\n')
        result=self.shell('. "$1/lib/setup/bindings.sh"; i3_config_files "$2"',main)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(set(result.stdout.splitlines()),{str(main),str(child),str(grand)})

    def test_unit_paths_escape_spaces_and_specifiers(self):
        result=self.shell('. "$1/lib/install.sh"; render_unit "$1/systemd/talkey-hud.service.in" "$2"',
                          '/tmp/a b%&$"/bin')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('ExecStart="/tmp/a b%%&$$\\"/bin/talkey-hud"',result.stdout)

    def test_hud_preserves_duration_and_emits_valid_json(self):
        take=self.root/'take'; take.mkdir()
        (take/'bytes').write_text('64000')
        state=self.root/'state'; state.mkdir()
        (state/'dictate.current').symlink_to(take,target_is_directory=True)
        (state/'dictate.header').write_text('done')
        (state/'dictate.transcript').write_text('hello "world"')
        script="""set -u
SCRIPT_DIR="$1/bin"; . "$1/lib/common.sh"
RAW="$STATE_DIR/dictate.raw"; TR="$STATE_DIR/dictate.transcript"
HD="$STATE_DIR/dictate.header"; FT="$STATE_DIR/dictate.footer"; MET="$STATE_DIR/dictate.metrics"
L_SUBMIT=submit; L_MEMO=memo; LOUD=-10; LOW=-20; FLOOR=-45; WARN_S=25
. "$1/lib/hud.sh"
emit
"""
        result=self.shell(script)
        self.assertEqual(result.returncode,0,result.stderr)
        data=json.loads(result.stdout)
        self.assertEqual(data['timetext'],'2s')
        self.assertIn('hello "world"',data['transcript'])

    def test_real_wav_archive_is_private_and_complete(self):
        import shutil
        if not shutil.which('ffmpeg'): self.skipTest('ffmpeg unavailable')
        raw=self.root/'audio.raw'; raw.write_bytes(b'\0\1'*16000)
        script="""set -euo pipefail
SCRIPT_DIR="$1/bin"; . "$1/lib/common.sh"
. "$1/lib/recording.sh"; . "$1/lib/archive.sh"
FFMPEG=ffmpeg; SAMPLE_RATE=16000; CHANNELS=1; ARCHIVE=1
log() { :; }
save_audio "$2" "$3" dictate 'hello'
printf '%s' "$ARCHIVED_FILE"
"""
        result=self.shell(script,raw,self.root/'archive')
        self.assertEqual(result.returncode,0,result.stderr)
        out=Path(result.stdout)
        self.assertEqual(out.stat().st_mode&0o777,0o600)
        with wave.open(str(out),'rb') as wav:
            self.assertEqual(wav.getnframes(),16000)
        self.assertEqual(out.with_suffix('.txt').read_text().strip(),'hello')

    def test_generated_bindings_pass_i3_parser(self):
        import shutil
        if not shutil.which('i3'): self.skipTest('i3 unavailable')
        dest=self.root/'bindings'
        script="""set -euo pipefail
DATA_DIR="$1"; . "$1/lib/setup/bindings.sh"
ok() { :; }; warn() { :; }
write_i3_bindings "$2" 66 '' '' ''
i3 -C -c "$2"
"""
        result=self.shell(script,dest)
        self.assertEqual(result.returncode,0,result.stderr)


if __name__=='__main__': unittest.main()
