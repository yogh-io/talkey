"""Auto-mode boundaries and lifecycle with fake audio, VAD, inference and keys."""
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest

import test_runtime as runtime

ROOT, FAKE = runtime.ROOT, runtime.FAKE

loader = importlib.machinery.SourceFileLoader('auto_worker', str(ROOT/'bin/talkey-auto-worker'))
spec = importlib.util.spec_from_loader(loader.name, loader)
auto = importlib.util.module_from_spec(spec)
loader.exec_module(auto)


class EndpointTests(unittest.TestCase):
    def test_silence_and_clicks_do_not_start_speech(self):
        detector = auto.Endpoint()
        for value in [False] * 100 + [True, False] * 50:
            self.assertIsNone(detector.update(value))

    def test_pause_resumes_and_mute_ends_once(self):
        detector = auto.Endpoint(start_ms=64, silence_ms=160)
        self.assertIsNone(detector.update(True))
        self.assertEqual(detector.update(True), 'start')
        for value in [False] * 4 + [True] + [False] * 4:
            self.assertIsNone(detector.update(value))
        self.assertEqual(detector.update(False), 'end')
        for _ in range(20):
            self.assertIsNone(detector.update(False))
        self.assertIsNone(detector.update(True))
        self.assertEqual(detector.update(True), 'start')

    def test_continuous_speech_is_capped(self):
        detector = auto.Endpoint(start_ms=32, max_ms=96)
        self.assertEqual([detector.update(True) for _ in range(3)], ['start', None, 'end'])


class AutoRuntimeTests(unittest.TestCase):
    run_key = runtime.RuntimeTests.run_key
    wait_for = runtime.RuntimeTests.wait_for
    keys = runtime.RuntimeTests.keys

    def setUp(self):
        runtime.RuntimeTests.setUp(self)
        self.env.update(TALKEY_VAD_START_MS='64', TALKEY_VAD_SILENCE_MS='160',
                        TALKEY_SMART_TURN='0', TALKEY_AUTO_PYTHON='python3',
                        XDG_STATE_HOME=str(self.root/'persistent'),
                        TALKEY_DOUBLE_TAP_MS='0', TALKEY_HOLD_MS='2000')
        self.mute = self.root/'mute'
        self.mute.touch()
        recorder = FAKE.replace("b'\\0\\1'*1600", "(b'\\0\\0' if (root/'mute').exists() else b'\\0\\1')*512")
        recorder = recorder.replace('while True:\n',
                                    "(root/'capture.pid').write_text(str(os.getpid()))\n        while True:\n            if (root/'stall').exists(): time.sleep(.03); continue\n")
        (self.commands/'ffmpeg').write_text(recorder)
        # Replace only the detector at the process boundary. Production capture,
        # buffering, segmentation and all controller code still run unchanged.
        wrapper = self.commands/'python3'
        wrapper.write_text(f'''#!{sys.executable}
import os, pathlib, runpy, sys, time
if sys.argv[1].endswith('/talkey-auto-worker'):
    if os.path.exists(os.path.join(os.environ['TEST_ROOT'], 'fail-vad')): sys.exit(1)
    ns=runpy.run_path(sys.argv[1])
    class FakeVad:
        def speech(self, frame): return any(frame)
        def close(self): pass
    class FakeTurn:
        def probability(self, pcm):
            root=pathlib.Path(os.environ['TEST_ROOT'])
            (root/'turn-started').touch()
            probability=.1 if (root/'turn-incomplete').exists() else .99
            while (root/'turn-wait').exists(): time.sleep(.01)
            if (root/'turn-fail').exists(): raise RuntimeError('test turn inference failed')
            return probability
    ns['main'].__globals__['Silero']=FakeVad
    ns['main'].__globals__['load_turn']=lambda: FakeTurn() if os.environ.get('TALKEY_SMART_TURN')=='1' else None
    sys.argv=sys.argv[1:]
    sys.exit(ns['main']())
os.execv({sys.executable!r}, [{sys.executable!r}]+sys.argv[1:])
''')
        wrapper.chmod(0o755)

    def tearDown(self):
        if (self.state/'auto.current').exists():
            self.run_key('auto', 'stop', check=False)
        runtime.RuntimeTests.tearDown(self)

    def status(self):
        return json.loads(self.run_key('auto', 'status').stdout)

    def listen(self):
        self.run_key('auto', 'start')
        self.wait_for(lambda: self.status()['state'] == 'listening')

    def speak(self):
        self.mute.unlink(missing_ok=True)
        self.wait_for(lambda: self.status()['state'] == 'speaking')
        take = (self.state/'dictate.current').resolve()
        self.wait_for(lambda: (take/'dictate.raw').stat().st_size >= 1024)
        return take

    def end_phrase(self):
        self.mute.touch()
        self.wait_for(lambda: self.status()['state'] == 'listening')

    def smart_listen(self, minimum='320', maximum='2000'):
        self.env.update(TALKEY_SMART_TURN='1', TALKEY_TURN_MIN_SILENCE_MS=minimum,
                        TALKEY_TURN_MAX_SILENCE_MS=maximum)
        self.listen()

    def test_incomplete_thinking_pause_keeps_the_same_phrase(self):
        (self.root/'turn-incomplete').touch()
        self.run_key('auto', 'enter-on')
        self.smart_listen()
        take = self.speak()
        self.mute.touch()
        self.wait_for(lambda: self.status()['state'] == 'paused')
        time.sleep(.4)
        self.assertFalse(self.keys())
        self.assertFalse((take/'speech.ended').exists())
        self.assertEqual(self.speak(), take)
        (self.root/'turn-incomplete').unlink()
        self.end_phrase()
        self.assertEqual(sum(k[0] == 'type' for k in self.keys()), 1)
        self.assertEqual(sum(k[-1] == 'Return' for k in self.keys()), 1)
        self.assertEqual(json.loads((take/'turn.json').read_text())['reason'], 'complete')

    def test_complete_turn_still_waits_for_minimum_silence(self):
        self.smart_listen(minimum='1000')
        self.speak()
        self.mute.touch()
        self.wait_for(lambda: (self.root/'turn-started').exists())
        time.sleep(.2)
        self.assertFalse(self.keys())
        self.wait_for(lambda: self.status()['state'] == 'listening')
        self.assertEqual(sum(k[0] == 'type' for k in self.keys()), 1)

    def test_dictate_key_finishes_paused_phrase_and_keeps_auto_enabled(self):
        (self.root/'turn-incomplete').touch()
        self.run_key('auto', 'enter-on')
        self.smart_listen(maximum='20000')
        take = self.speak()
        self.mute.touch()
        self.wait_for(lambda: self.status()['state'] == 'paused')
        self.run_key('dictate', 'press')
        self.run_key('dictate', 'release')
        self.wait_for(lambda: self.status()['state'] == 'listening')
        self.assertEqual(json.loads((take/'turn.json').read_text())['reason'], 'manual')
        self.assertTrue(self.status()['enabled'])
        self.assertEqual(sum(k[0] == 'type' for k in self.keys()), 1)
        self.assertEqual(sum(k[-1] == 'Return' for k in self.keys()), 1)

    def test_dictate_key_during_final_inference_does_not_start_or_cancel(self):
        self.listen()
        take = self.speak()
        (self.root/'wait-inference').touch()
        self.mute.touch()
        self.wait_for(lambda: (self.root/'inference-started').exists())
        self.run_key('dictate', 'press')
        self.run_key('dictate', 'release')
        self.assertEqual(take, (self.state/'dictate.current').resolve())
        self.assertTrue(self.status()['enabled'])
        (self.root/'wait-inference').unlink()
        self.wait_for(lambda: self.status()['state'] == 'listening')
        self.assertEqual(sum(k[0] == 'type' for k in self.keys()), 1)

    def test_speech_resuming_during_turn_inference_invalidates_completion(self):
        (self.root/'turn-wait').touch()
        self.smart_listen()
        take = self.speak()
        self.mute.touch()
        self.wait_for(lambda: (self.root/'turn-started').exists())
        self.assertEqual(self.speak(), take)
        (self.root/'turn-wait').unlink()
        time.sleep(.2)
        self.assertFalse(self.keys())
        self.assertEqual(self.status()['state'], 'speaking')
        self.end_phrase()
        self.assertEqual(sum(k[0] == 'type' for k in self.keys()), 1)

    def test_incomplete_turn_finishes_after_maximum_silence(self):
        (self.root/'turn-incomplete').touch()
        self.smart_listen(maximum='640')
        take = self.speak()
        self.end_phrase()
        self.assertEqual(json.loads((take/'turn.json').read_text())['reason'], 'silence_timeout')
        self.assertEqual(sum(k[0] == 'type' for k in self.keys()), 1)

    def test_turn_model_failure_never_submits(self):
        (self.root/'turn-fail').touch()
        self.run_key('auto', 'enter-on')
        self.smart_listen()
        take = self.speak()
        self.mute.touch()
        self.wait_for(lambda: not self.status()['enabled'])
        self.assertEqual(self.status()['state'], 'error')
        self.assertFalse(self.keys())
        self.assertGreater((take/'dictate.raw').stat().st_size, 0)

    def test_stopping_during_turn_inference_cancels_without_sending(self):
        (self.root/'turn-wait').touch()
        self.run_key('auto', 'enter-on')
        self.smart_listen()
        self.speak()
        self.mute.touch()
        self.wait_for(lambda: (self.root/'turn-started').exists())
        self.run_key('auto', 'stop')
        (self.root/'turn-wait').unlink()
        self.assertFalse(self.keys())
        self.assertEqual(self.status()['state'], 'off')

    def test_microphone_failure_does_not_bypass_turn_detection(self):
        self.run_key('auto', 'enter-on')
        self.smart_listen()
        take = self.speak()
        os.kill(int((self.root/'capture.pid').read_text()), 15)
        self.wait_for(lambda: not self.status()['enabled'])
        self.assertEqual(self.status()['state'], 'error')
        self.assertFalse(self.keys())
        self.assertGreater((take/'dictate.raw').stat().st_size, 0)

    def test_background_idle_then_two_phrases_and_one_shot_send(self):
        self.listen()
        self.assertFalse((self.state/'dictate.current').exists())
        self.assertFalse(self.keys())
        self.run_key('submit')  # arm while idle; must not press Enter yet
        self.assertEqual(self.status()['submit'], 'send')
        self.assertFalse(self.keys())
        first = self.speak()
        self.run_key('dictate', 'release')  # a release never stops auto mode
        self.end_phrase()
        self.assertTrue((first/'speech.ended').exists())
        self.assertTrue(self.status()['speech_ended'])
        self.assertEqual(sum(k[-1] == 'Return' for k in self.keys()), 1)
        second = self.speak()
        self.assertNotEqual(first, second)
        self.assertFalse(self.status()['speech_ended'])
        self.end_phrase()
        self.assertEqual(sum(k[0] == 'type' for k in self.keys()), 2)
        self.assertEqual(sum(k[-1] == 'Return' for k in self.keys()), 1)

    def test_double_tap_enables_single_tap_disables(self):
        self.env['TALKEY_DOUBLE_TAP_MS'] = '2000'
        self.run_key('dictate', 'press')
        self.assertFalse((self.state/'dictate.current').exists())
        self.run_key('dictate', 'release')
        self.run_key('dictate', 'press')
        self.run_key('dictate', 'release')
        self.wait_for(lambda: self.status()['state'] == 'listening')
        self.assertTrue(self.status()['enabled'])
        self.assertFalse(self.keys())
        self.assertFalse((self.state/'dictate.current').exists())
        self.run_key('dictate', 'press')
        self.run_key('dictate', 'release')
        self.wait_for(lambda: self.status()['state'] == 'off')
        self.assertFalse(self.status()['enabled'])

    def test_stop_cancels_speech_without_output(self):
        self.listen()
        take = self.speak()
        self.run_key('submit')
        self.run_key('auto', 'stop')
        self.assertFalse(self.keys())
        self.assertFalse((take/'dictate.pid').exists())
        self.assertEqual(len(list((self.root/'archive/cancelled').glob('*.wav'))), 1)

    def test_default_send_can_be_disabled_while_idle(self):
        self.env['TALKEY_SUBMIT'] = '1'
        self.listen()
        self.run_key('submit')
        self.run_key('submit')
        self.assertEqual(self.status()['submit'], 'type')
        self.speak()
        self.end_phrase()
        self.assertTrue(any(k[0] == 'type' for k in self.keys()))
        self.assertFalse(any(k[-1] == 'Return' for k in self.keys()))
        self.assertEqual(self.status()['submit'], 'send')

    def test_enter_preference_can_be_set_while_off_and_survives_restart(self):
        self.run_key('auto', 'enter-on')
        self.assertTrue(self.status()['enter_enabled'])
        self.assertFalse(self.status()['enabled'])
        self.assertFalse(self.keys())
        self.listen()
        for _ in range(2):
            self.speak()
            self.end_phrase()
        self.assertEqual(sum(k[-1] == 'Return' for k in self.keys()), 2)
        self.run_key('auto', 'stop')
        self.listen()
        self.assertTrue(self.status()['enter_enabled'])
        self.run_key('auto', 'enter-toggle')
        self.assertFalse(self.status()['enter_enabled'])
        self.speak()
        self.end_phrase()
        self.assertEqual(sum(k[-1] == 'Return' for k in self.keys()), 2)

    def test_enter_toggle_during_speech_applies_to_current_and_next_phrase(self):
        self.listen()
        self.speak()
        self.run_key('auto', 'enter-toggle')
        self.assertEqual(self.status()['submit'], 'send')
        self.end_phrase()
        self.assertEqual(sum(k[-1] == 'Return' for k in self.keys()), 1)
        self.assertEqual(self.status()['submit'], 'send')

    def test_enter_can_be_disabled_during_inference(self):
        self.run_key('auto', 'enter-on')
        self.listen()
        self.speak()
        (self.root/'wait-inference').touch()
        self.mute.touch()
        self.wait_for(lambda: (self.root/'inference-started').exists())
        self.run_key('auto', 'enter-off')
        (self.root/'wait-inference').unlink()
        self.wait_for(lambda: self.status()['state'] == 'listening')
        self.assertTrue(any(k[0] == 'type' for k in self.keys()))
        self.assertFalse(any(k[-1] == 'Return' for k in self.keys()))

    def test_enter_preference_does_not_override_cancellation(self):
        self.listen()
        self.speak()
        self.run_key('submit')
        self.run_key('submit')
        self.run_key('auto', 'enter-on')
        self.assertEqual(self.status()['submit'], 'cancel')
        self.end_phrase()
        self.assertFalse(self.keys())

    def test_auto_enter_preference_does_not_change_manual_dictation(self):
        self.run_key('auto', 'enter-on')
        self.run_key('dictate', 'start')
        self.run_key('dictate', 'stop')
        self.assertTrue(any(k[0] == 'type' for k in self.keys()))
        self.assertFalse(any(k[-1] == 'Return' for k in self.keys()))

    def test_enter_preference_survives_a_fresh_runtime_directory(self):
        self.run_key('auto', 'enter-on')
        self.env['TALKEY_STATE_DIR'] = str(self.root/'fresh-runtime')
        self.assertTrue(self.status()['enter_enabled'])
        self.run_key('auto', 'enter-off')
        self.assertFalse(self.status()['enter_enabled'])

    def test_hardware_mute_that_stalls_samples_ends_phrase(self):
        self.listen()
        take = self.speak()
        (self.root/'stall').touch()
        self.wait_for(lambda: self.status()['state'] == 'listening')
        self.assertTrue((take/'speech.ended').exists())
        self.assertEqual(sum(k[0] == 'type' for k in self.keys()), 1)
        (self.root/'stall').unlink()
        self.wait_for(lambda: self.status()['state'] == 'speaking')
        self.end_phrase()
        self.assertEqual(sum(k[0] == 'type' for k in self.keys()), 2)

    def test_preroll_preserves_audio_before_detected_onset(self):
        self.listen()
        time.sleep(.2)
        take = self.speak()
        pcm = (take/'dictate.raw').read_bytes()
        self.assertEqual(pcm[:1024], bytes(1024))
        self.assertIn(b'\0\1' * 512, pcm)

    def test_triangle_during_speech_and_cancellation(self):
        self.listen()
        self.speak()
        self.run_key('submit')
        self.run_key('submit')
        self.end_phrase()
        self.assertFalse(self.keys())
        self.assertEqual(len(list((self.root/'archive/cancelled').glob('*.wav'))), 1)

    def test_stop_during_inference_never_types(self):
        self.listen()
        self.speak()
        (self.root/'wait-inference').touch()
        self.mute.touch()
        self.wait_for(lambda: (self.root/'inference-started').exists())
        self.run_key('submit')
        self.run_key('auto', 'stop')
        (self.root/'wait-inference').unlink()
        self.wait_for(lambda: not (self.state/'dictate.pending').exists())
        self.assertFalse(self.keys())
        self.assertEqual(self.status()['state'], 'off')

    def test_speech_captured_while_inference_is_busy_is_delivered_in_order(self):
        self.listen()
        first = self.speak()
        (self.root/'wait-inference').touch()
        self.mute.touch()
        self.wait_for(lambda: (self.root/'inference-started').exists())
        self.mute.unlink()
        time.sleep(.25)
        self.mute.touch()
        time.sleep(.2)
        (self.root/'wait-inference').unlink()
        self.wait_for(lambda: sum(k[0] == 'type' for k in self.keys()) == 2)
        self.assertNotEqual(first, (self.state/'dictate.current').resolve())

    def test_old_worker_callback_cannot_affect_new_run(self):
        self.listen()
        old = (self.state/'auto.current').resolve()
        self.run_key('auto', 'stop')
        self.listen()
        result = subprocess.run([str(ROOT/'bin/talkey'), '_auto', 'begin'],
                                env=dict(self.env, TALKEY_AUTO_RUN=str(old)),
                                capture_output=True, text=True, timeout=5)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.state/'dictate.current').exists())

    def test_detector_failure_keeps_manual_recording(self):
        self.run_key('dictate', 'start')
        take = (self.state/'dictate.current').resolve()
        (self.root/'fail-vad').touch()
        result = self.run_key('auto', 'start', check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((take/'dictate.pid').exists())
        self.assertEqual(self.status()['state'], 'off')
        self.run_key('dictate', 'stop')

    def enable_sounds(self):
        player = self.commands/'player'
        player.write_text('#!/bin/sh\nprintf "%s\\n" "$1" >> "$TEST_ROOT/sounds"\n')
        player.chmod(0o755)
        self.env.update(TALKEY_SOUNDS='1', TALKEY_PLAYER=str(player))

    def test_double_tap_only_toggles_auto_with_distinct_tones(self):
        self.env['TALKEY_DOUBLE_TAP_MS'] = '2000'
        self.enable_sounds()
        for expected in ('listening', 'off'):
            self.run_key('dictate', 'press')
            self.run_key('dictate', 'release')
            self.run_key('dictate', 'press')
            self.run_key('dictate', 'release')
            self.wait_for(lambda: self.status()['state'] == expected)
            self.assertFalse((self.state/'dictate.current').exists())
        self.wait_for(lambda: (self.root/'sounds').exists() and len((self.root/'sounds').read_text().splitlines()) == 2)
        self.assertEqual([Path(p).stem for p in (self.root/'sounds').read_text().splitlines()], ['auto-on', 'auto-off'])
        time.sleep(2.1)  # Neither cancelled single-tap timer may start a take later.
        self.assertFalse((self.state/'dictate.current').exists())

    def test_idle_timeout_stops_with_off_tone_and_does_not_interrupt_speech(self):
        self.enable_sounds()
        self.env['TALKEY_AUTO_IDLE_S'] = '1'
        self.listen()
        self.speak()
        time.sleep(1.1)
        self.assertTrue(self.status()['enabled'])
        self.end_phrase()
        self.wait_for(lambda: not self.status()['enabled'])
        self.assertEqual(self.status()['state'], 'off')
        self.wait_for(lambda: 'auto-off.wav' in (self.root/'sounds').read_text())
        self.assertEqual(sum(k[0] == 'type' for k in self.keys()), 1)

    def test_double_tap_during_speech_does_not_finish_or_send_first(self):
        self.env['TALKEY_DOUBLE_TAP_MS'] = '2000'
        self.enable_sounds()
        self.listen()
        take = self.speak()
        self.run_key('dictate', 'press')
        self.run_key('dictate', 'release')
        self.assertFalse((take/'finish.requested').exists())
        self.run_key('dictate', 'press')
        self.run_key('dictate', 'release')
        self.assertFalse(self.status()['enabled'])
        self.assertFalse(self.keys())
        self.wait_for(lambda: 'auto-off.wav' in (self.root/'sounds').read_text())
        self.assertEqual([Path(p).stem for p in (self.root/'sounds').read_text().splitlines()], ['auto-on', 'auto-off'])

    def test_idle_timeout_can_be_disabled(self):
        self.env['TALKEY_AUTO_IDLE_S'] = '0'
        self.listen()
        time.sleep(.4)
        self.assertTrue(self.status()['enabled'])

    def test_idle_timeout_drains_speech_captured_during_slow_finalization(self):
        self.env['TALKEY_AUTO_IDLE_S'] = '1'
        self.listen()
        self.speak()
        (self.root/'wait-inference').touch()
        self.mute.touch()
        self.wait_for(lambda: (self.root/'inference-started').exists())
        self.mute.unlink()
        time.sleep(.25)
        self.mute.touch()
        time.sleep(1.1)
        (self.root/'wait-inference').unlink()
        self.wait_for(lambda: sum(k[0] == 'type' for k in self.keys()) == 2)

    def test_single_tap_waits_then_starts_manual_recording(self):
        self.env['TALKEY_DOUBLE_TAP_MS'] = '300'
        self.run_key('dictate', 'press')
        self.run_key('dictate', 'release')
        self.assertFalse((self.state/'dictate.current').exists())
        self.wait_for(lambda: (self.state/'dictate.pid').exists())
        self.assertFalse(self.status()['enabled'])
        self.run_key('dictate', 'stop')

    def test_transcription_failure_stops_auto_and_never_sends(self):
        self.listen()
        self.speak()
        self.run_key('submit')
        (self.root/'fail-http').touch()
        self.mute.touch()
        self.wait_for(lambda: not self.status()['enabled'])
        self.assertEqual(self.status()['state'], 'error')
        self.assertFalse(self.keys())

    def test_manual_hold_and_toggle_still_finish(self):
        self.env['TALKEY_DOUBLE_TAP_MS'] = '0'
        self.run_key('dictate', 'press')
        self.run_key('dictate', 'release')
        self.assertTrue((self.state/'dictate.pid').exists())
        self.run_key('dictate', 'press')
        self.assertEqual(sum(k[0] == 'type' for k in self.keys()), 1)
        self.env['TALKEY_HOLD_MS'] = '0'
        self.run_key('dictate', 'press')
        self.run_key('dictate', 'release')
        self.assertEqual(sum(k[0] == 'type' for k in self.keys()), 2)


if __name__ == '__main__':
    unittest.main()
