"""Translate mode with fake audio devices, VAD, recognition, translation and voice."""
from http.server import BaseHTTPRequestHandler, HTTPServer
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
import wave

import test_auto
import test_runtime as runtime

ROOT = runtime.ROOT

loader = importlib.machinery.SourceFileLoader('translate_worker', str(ROOT/'bin/talkey-translate-worker'))
spec = importlib.util.spec_from_loader(loader.name, loader)
worker = importlib.util.module_from_spec(spec)
loader.exec_module(worker)

AUDIO = r'''#!/usr/bin/env python3
import json, os, pathlib, sys
root=pathlib.Path(os.environ['TEST_ROOT'])
name=pathlib.Path(sys.argv[0]).name
args=sys.argv[1:]
if name=='pacat':
    with (root/'pacat-args').open('a') as f: f.write(json.dumps(args)+'\n')
    out=root/('monitor' if not any(a.startswith('--device=') for a in args) else 'spoken')
    while chunk:=sys.stdin.buffer.read1(65536):
        with out.open('ab') as f: f.write(chunk)
else:
    devices=root/'devices'
    known=devices.read_text().split() if devices.exists() else []
    if args[:2]==['list','short']:
        for line in known:
            kind,device=line.split(':')
            if kind==args[2]: print(f'1\t{device}\tmodule\ts16le 1ch 48000Hz\tIDLE')
    elif args[0]=='load-module':
        option='sink_name=' if args[1]=='module-null-sink' else 'source_name='
        kind='sinks' if args[1]=='module-null-sink' else 'sources'
        device=next(a for a in args if a.startswith(option))[len(option):]
        with devices.open('a') as f: f.write(f'{kind}:{device}\n')
        print(42)
    elif args[0]=='get-default-source':
        default=root/'default-source'
        print(default.read_text().strip() if default.exists() else 'real_mic')
'''


class PhrasesTests(unittest.TestCase):
    def test_long_phrase_ends_on_a_short_pause(self):
        phrases = worker.Phrases(32, 320, 60000, 64, 160)
        self.assertEqual(phrases.update(True), 'start')
        self.assertEqual([phrases.update(True) for _ in range(5)], [None] * 5)
        self.assertEqual([phrases.update(False) for _ in range(2)], [None, 'end'])

    def test_short_phrase_waits_for_the_full_pause(self):
        phrases = worker.Phrases(32, 320, 60000, 64, 160)
        self.assertEqual(phrases.update(True), 'start')
        self.assertEqual([phrases.update(False) for _ in range(9)], [None] * 9)
        self.assertEqual(phrases.update(False), 'end')


class AgreementTests(unittest.TestCase):
    def test_a_guessed_verb_is_never_spoken(self):
        agreement = worker.Agreement(.25)
        self.assertIsNone(agreement.offer('We moeten volgende week de release'.split(),
                                          'We need to release it next week', -9))
        # "uitstellen" (postpone) changed the earlier draft, so it was a guess.
        self.assertIsNone(agreement.offer('We moeten volgende week de release nog even uitstellen'.split(),
                                          'We need to postpone the release for another week', -9))
        self.assertEqual(agreement.spoken, '')
        self.assertEqual(agreement.decision['result'], 'english-changed')
        self.assertIsNone(agreement.offer('Wij moeten volgende week de release nog even uitstellen ja'.split(),
                                          'We need to postpone the release for another week, yes', -9))
        self.assertEqual(agreement.decision['result'], 'dutch-changed')

    def test_a_draft_a_later_one_keeps_is_spoken_as_the_later_one_writes_it(self):
        agreement = worker.Agreement(.25)
        self.assertIsNone(agreement.offer(['Hallo', 'allemaal'], 'Hello everyone', -9))
        self.assertEqual(agreement.offer('Hallo allemaal ik deel'.split(), 'Hello everyone, I am sharing', -9),
                         'Hello everyone,')
        self.assertEqual(agreement.said, ['Hallo', 'allemaal'])

    def test_later_pieces_end_at_a_clause_once_the_voice_needs_more(self):
        agreement = worker.Agreement(.25)
        agreement.offer(['Hallo', 'allemaal'], 'Hello everyone', -9)
        agreement.offer('Hallo allemaal ik deel'.split(), 'Hello everyone, I am sharing', -9)
        heard = 'Hallo allemaal ik deel even mijn scherm'.split()
        self.assertIsNone(agreement.offer(heard, 'I am sharing my screen', 2))
        # Agreed and at a comma, but two seconds of English are still playing.
        self.assertIsNone(agreement.offer(heard + ['en', 'dan'], 'I am sharing my screen, and then', 2))
        # "and then" has no clause end; the voice is about to run out, so the clause goes.
        self.assertEqual(agreement.offer(heard + ['en', 'dan', 'kijken'], 'I am sharing my screen, and then look', .5),
                         'I am sharing my screen,')

    def test_after_a_long_silence_a_piece_need_not_end_at_a_clause(self):
        agreement = worker.Agreement(.25)
        agreement.offer(['Hallo', 'allemaal'], 'Hello everyone', -9)
        agreement.offer('Hallo allemaal ik deel'.split(), 'Hello everyone, I am sharing', -9)
        heard = 'Hallo allemaal ik deel even mijn scherm'.split()
        agreement.offer(heard, 'I am sharing my screen and', -9)
        self.assertEqual(agreement.offer(heard + ['en'], 'I am sharing my screen and then', -1.5),
                         'I am sharing my screen and')


    def test_each_offer_says_why_it_spoke_or_held(self):
        agreement = worker.Agreement(.25)
        agreement.offer(['Hallo', 'allemaal'], 'Hello everyone', -9)
        self.assertEqual(agreement.decision, {'result': 'first-draft', 'piece': None})
        agreement.offer(['Hallo', 'allemaal'], 'Hello all', -9)
        self.assertEqual(agreement.decision['result'], 'no-new-dutch')
        agreement.offer('Hallo allemaal ik deel'.split(), 'Hello everyone, I am sharing', -9)
        self.assertEqual(agreement.decision, {'result': 'spoken', 'piece': 'Hello everyone,', 'dutch': 'Hallo allemaal'})
        heard = 'Hallo allemaal ik deel even mijn scherm'.split()
        agreement.offer(heard, 'I am sharing my screen', 2)
        agreement.offer(heard + ['en'], 'I am sharing my screen and', 2)
        self.assertEqual(agreement.decision, {'result': 'no-clause-end', 'piece': 'I am sharing my screen'})
        agreement.offer(heard + ['en', 'dan'], 'I am sharing my screen, and then', 2)
        self.assertEqual(agreement.decision, {'result': 'voice-busy', 'piece': 'I am sharing my screen,'})


class ResumeTests(unittest.TestCase):
    def test_finds_the_end_of_what_was_said_despite_rewording(self):
        said = 'een twee drie vier,'.split()
        self.assertEqual(worker.resume(said, 'Een twee drie vier, vijf zes.'.split()), 4)
        self.assertEqual(worker.resume(said, 'een twee drieë vier, vijf'.split()), 4)
        self.assertEqual(worker.resume(said, 'een twee drie'.split()), 3)
        self.assertEqual(worker.resume([], 'een twee'.split()), 0)


class OnAirTests(unittest.TestCase):
    def test_pieces_carry_when_they_play_and_the_hud_stays_until_they_have(self):
        with tempfile.TemporaryDirectory(prefix='talkey-onair-') as tmp:
            state, run = Path(tmp)/'state', Path(tmp)/'run'
            state.mkdir(); run.mkdir()
            env = {'TALKEY_TRANSLATE_RUN': str(run), 'TALKEY_STATE_DIR': str(state)}
            os.environ.update(env)
            self.addCleanup(lambda: [os.environ.pop(name) for name in env])
            interpreter = worker.Interpreter(None, None, None)

            class Voice:
                sample_rate = 1000

                def stream(self, text, last=True):
                    yield bytes(2 * 300)

                def end(self):
                    pass

            interpreter.voice = Voice()
            phrase = worker.Phrase(time.monotonic(), 1)
            phrase.ended = time.monotonic()
            interpreter.subtitles.append(phrase.shown)
            interpreter.lines.put(phrase)
            interpreter.mark(1)
            threading.Thread(target=interpreter.speak, daemon=True).start()
            before = time.time()
            interpreter.queue(phrase, 'Hello there,', False)
            interpreter.queue(phrase, 'how are you?', True)
            phrase.segments.put(None)

            def pieces():
                return json.loads((state/'translate.subtitles').read_text())['lines'][0]['pieces']
            deadline = time.monotonic() + 3
            while not all(piece['end'] for piece in pieces()):
                self.assertLess(time.monotonic(), deadline, 'the pieces never finished')
                time.sleep(.01)
            first, second = pieces()
            self.assertEqual([first['text'], second['text']], ['Hello there,', 'how are you?'])
            self.assertGreaterEqual(first['start'], round(before, 3))
            self.assertAlmostEqual(first['end'] - first['start'], .3, delta=.002)
            # The second piece waits for the first to finish playing.
            self.assertAlmostEqual(second['start'], first['end'], delta=.002)
            self.assertAlmostEqual(second['end'] - second['start'], .3, delta=.002)
            # The voice is done long before the listeners have heard it all.
            while interpreter.inflight:
                time.sleep(.01)
            interpreter.mark()
            self.assertGreater(second['end'], time.time())
            self.assertTrue((state/'translate.showing').exists())
            time.sleep(max(0, second['end'] - time.time()) + .02)
            interpreter.mark()
            self.assertFalse((state/'translate.showing').exists())


class TranslatorTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        test = self

        class Ollama(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                test.requests.append(body)
                reply = json.dumps({'message': {'content': f' "EN {len(test.requests)}" \n'}}).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(reply)))
                self.end_headers()
                self.wfile.write(reply)

            def log_message(self, *_):
                pass

        self.server = HTTPServer(('127.0.0.1', 0), Ollama)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        os.environ['TALKEY_TRANSLATE_URL'] = f'http://127.0.0.1:{self.server.server_port}/api/chat'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        del os.environ['TALKEY_TRANSLATE_URL']

    def test_recent_phrases_are_context_and_quotes_are_dropped(self):
        translate = worker.Translator()
        self.assertEqual(translate('warm'), 'EN 1')
        for i in range(2, 6):
            self.assertEqual(translate(f'nl {i}'), f'EN {i}')
            translate.remember(f'nl {i}', f'EN {i}')
        messages = self.requests[-1]['messages']
        prompt = translate.prompt
        self.assertTrue(prompt.startswith('You are a professional Dutch (nl) to English (en) translator.'))
        self.assertEqual([m['content'] for m in messages],
                         [prompt + 'nl 2', 'EN 2', prompt + 'nl 3', 'EN 3', prompt + 'nl 4', 'EN 4', prompt + 'nl 5'])
        self.assertFalse(self.requests[-1]['stream'])

    def test_spoken_english_is_continued_without_its_sentence_end(self):
        translate = worker.Translator()
        self.assertEqual(translate('heel de zin', spoken="Okay, I'm getting."), 'EN 1')
        # A prefill that ends a sentence makes the model stop, though the Dutch goes on.
        self.assertEqual(self.requests[-1]['messages'][-1], {'role': 'assistant', 'content': "Okay, I'm getting"})


class TranslateRuntimeTests(unittest.TestCase):
    run_key = runtime.RuntimeTests.run_key
    wait_for = runtime.RuntimeTests.wait_for

    def setUp(self):
        test_auto.AutoRuntimeTests.setUp(self)
        for name in ['pacat', 'pactl']:
            fake = self.commands/name
            fake.write_text(AUDIO)
            fake.chmod(0o755)
        voice = self.root/'voice.safetensors'
        voice.touch()
        self.env.update(TALKEY_TRANSLATE_PYTHON='python3', TALKEY_TRANSLATE_VOICE=str(voice),
                        TALKEY_TRANSLATE_SILENCE_MS='160', TALKEY_TRANSLATE_QUICK_AFTER_S='600',
                        TALKEY_TRANSLATE_TICK='0')
        wrapper = self.commands/'python3'
        script = wrapper.read_text()
        fakes = f'''if sys.argv[1].endswith('/talkey-translate-worker'):
    import types
    import importlib.machinery
    sys.modules['pocket_tts'] = types.ModuleType('pocket_tts')
    sys.modules['pocket_tts'].__spec__ = importlib.machinery.ModuleSpec('pocket_tts', None)
    ns=runpy.run_path(sys.argv[1])
    import json
    root=pathlib.Path(os.environ['TEST_ROOT'])
    class FakeVad:
        def speech(self, frame): return any(frame)
        def close(self): pass
    class FakeVoice:
        sample_rate = 24000
        def __init__(self, path): pass
        def stream(self, text, last=True): yield text.encode()
        def end(self): pass
    class FakeTranslator:
        def __call__(self, text, spoken=''):
            if (root/'fail-translator').exists(): raise RuntimeError('test translator down')
            english = 'EN:' + text
            return english[len(spoken):].strip() if spoken and english.startswith(spoken) else english
        def remember(self, text, english): pass
    def heard(*args):
        with (root/'prompts').open('a') as f: f.write(json.dumps(args[3])+'\\n')
        lines=(root/'heard').read_text().splitlines()
        (root/'heard').write_text(''.join(line + '\\n' for line in lines[1:]))
        return lines[0]
    def previewed(*args):
        path = root/'preview-lines'
        if not path.exists(): return 'voorlopig'
        lines = path.read_text().splitlines()
        if len(lines) > 1: path.write_text(''.join(line + '\\n' for line in lines[1:]))
        return lines[0]
    ns['main'].__globals__.update(Silero=FakeVad, Voice=FakeVoice, Translator=FakeTranslator, transcribe=heard,
                                  preview=previewed)
    sys.argv=sys.argv[1:]
    sys.exit(ns['main']())
'''
        wrapper.write_text(script.replace('os.execv(', fakes + 'os.execv(', 1))

    def tearDown(self):
        if (self.state/'translate.current').exists():
            self.run_key('translate', 'stop', check=False)
        test_auto.AutoRuntimeTests.tearDown(self)

    def status(self, mode='translate'):
        return json.loads(self.run_key(mode, 'status').stdout)

    def listen(self, mode='translate'):
        self.run_key(mode, 'start')
        self.wait_for(lambda: self.status(mode)['state'] == 'listening')

    def say(self, *texts):
        (self.root/'heard').write_text(''.join(text + '\n' for text in texts))
        for _ in texts:
            self.mute.unlink()
            self.wait_for(lambda: self.status()['state'] == 'hearing')
            self.mute.touch()
            self.wait_for(lambda: self.status()['state'] == 'listening')

    def spoken(self):
        path = self.root/'spoken'
        return path.read_bytes() if path.exists() else b''

    def test_phrases_are_spoken_in_order_into_the_virtual_microphone(self):
        self.listen()
        self.say('Ik bel je morgen op.', 'Tot dan.')
        self.wait_for(lambda: self.spoken() == b'EN:Ik bel je morgen op.EN:Tot dan.')
        args = json.loads((self.root/'pacat-args').read_text().splitlines()[0])
        self.assertIn('--device=talkey_translate', args)
        self.assertIn('--rate=24000', args)
        self.assertFalse((self.root/'monitor').exists())
        log = (self.state/'translate.current/lines.log').read_text()
        self.assertIn('-> EN:Tot dan.', log)

    def subtitles(self):
        path = self.state/'translate.subtitles'
        return json.loads(path.read_text())['lines'] if path.exists() else []

    def test_subtitles_show_what_was_heard_and_said(self):
        self.listen()
        before = time.time()
        self.say('Ik bel je morgen op.', 'Tot dan.', 'Dag.')
        self.wait_for(lambda: self.spoken().endswith(b'EN:Dag.'))
        self.wait_for(lambda: not (self.state/'translate.showing').exists())
        lines = self.subtitles()
        times = [(piece.pop('start'), piece.pop('end')) for line in lines for piece in line['pieces']]
        self.assertEqual(lines, [
            {'n': 1, 'dutch': 'Ik bel je morgen op.', 'english': 'EN:Ik bel je morgen op.', 'live': False,
             'done': True, 'pieces': [{'text': 'EN:Ik bel je morgen op.'}]},
            {'n': 2, 'dutch': 'Tot dan.', 'english': 'EN:Tot dan.', 'live': False,
             'done': True, 'pieces': [{'text': 'EN:Tot dan.'}]},
            {'n': 3, 'dutch': 'Dag.', 'english': 'EN:Dag.', 'live': False,
             'done': True, 'pieces': [{'text': 'EN:Dag.'}]}])
        # Unix times, so the HUD can hold them against its own clock.
        for start, end in times:
            self.assertLessEqual(round(before, 3), start)
            self.assertLessEqual(start, end)
            self.assertLessEqual(end, time.time())

    def test_the_hud_meters_the_microphone_only_while_a_phrase_shows(self):
        self.listen()
        self.assertFalse((self.state/'translate.raw').exists())
        (self.root/'heard').write_text('Hallo.\n')
        self.mute.unlink()
        self.wait_for(lambda: (self.state/'translate.raw').exists())
        self.assertEqual(len((self.state/'translate.raw').read_bytes()), 4 * 1024)
        self.mute.touch()
        self.run_key('translate', 'stop')
        self.assertFalse((self.state/'translate.raw').exists())

    def test_dutch_preview_shows_while_speaking(self):
        self.env['TALKEY_TRANSLATE_PREVIEW_MS'] = '100'
        self.listen()
        (self.root/'heard').write_text('Definitief.\n')
        self.mute.unlink()
        self.wait_for(lambda: self.subtitles() and self.subtitles()[-1]['dutch'] == 'voorlopig')
        self.assertTrue(self.subtitles()[-1]['live'])
        self.assertTrue((self.state/'translate.showing').exists())
        self.mute.touch()
        self.wait_for(lambda: self.subtitles()[-1]['dutch'] == 'Definitief.')

    def test_agreed_english_is_spoken_before_the_pause(self):
        self.env['TALKEY_TRANSLATE_PREVIEW_MS'] = '100'
        self.listen()
        (self.root/'preview-lines').write_text('een twee\neen twee drie\n')
        (self.root/'heard').write_text('een twee drie vier.\n')
        self.mute.unlink()
        # The draft for "een twee" was kept when "drie" was heard, so it is spoken during the phrase.
        self.wait_for(lambda: self.spoken() == b'EN:een twee')
        self.assertEqual(self.status()['state'], 'hearing')
        self.assertEqual(self.subtitles()[-1]['english'], 'EN:een twee')
        self.assertFalse(self.subtitles()[-1]['done'])
        self.mute.touch()
        self.wait_for(lambda: self.spoken() == b'EN:een tweedrie vier.')
        self.wait_for(lambda: self.subtitles()[-1]['done'])
        self.assertEqual(self.subtitles()[-1]['english'], 'EN:een twee drie vier.')
        self.assertEqual([piece['text'] for piece in self.subtitles()[-1]['pieces']], ['EN:een twee', 'drie vier.'])
        log = (self.state/'translate.current/lines.log').read_text()
        self.assertIn('spoken as: EN:een twee | drie vier.', log)
        self.assertRegex(log, r'voice began [0-9.]+s after you did, -[0-9.]+s from your pause')

    def test_a_tick_fills_the_wait_between_pieces_only(self):
        sounds = self.root/'sounds'; sounds.mkdir()
        with wave.open(str(sounds/'translate-tick.wav'), 'wb') as sound:
            sound.setnchannels(1); sound.setsampwidth(2); sound.setframerate(24000)
            sound.writeframes(b'TICK')
        self.env.update(TALKEY_TRANSLATE_PREVIEW_MS='100', TALKEY_TRANSLATE_TICK='1', TALKEY_SOUND_DIR=str(sounds))
        self.listen()
        (self.root/'preview-lines').write_text('een twee\neen twee drie\n')
        (self.root/'heard').write_text('een twee drie vier.\n')
        self.mute.unlink()
        self.wait_for(lambda: self.spoken().startswith(b'EN:een twee'))
        # The rest of the phrase is not agreed yet; the listener hears ticks, not silence.
        self.wait_for(lambda: self.spoken().count(b'TICK') >= 2)
        self.mute.touch()
        self.wait_for(lambda: self.spoken().endswith(b'drie vier.'))
        spoken = self.spoken()
        self.assertTrue(spoken.startswith(b'EN:een tweeTICK'))
        time.sleep(1)
        self.assertEqual(self.spoken(), spoken, 'ticked after the phrase was spoken')

    def test_recording_keeps_each_phrase_as_heard_and_as_said(self):
        sounds = self.root/'sounds'; sounds.mkdir()
        with wave.open(str(sounds/'translate-tick.wav'), 'wb') as sound:
            sound.setnchannels(1); sound.setsampwidth(2); sound.setframerate(24000)
            sound.writeframes(b'TICK')
        self.env.update(TALKEY_TRANSLATE_PREVIEW_MS='100', TALKEY_TRANSLATE_TICK='1', TALKEY_SOUND_DIR=str(sounds),
                        TALKEY_TRANSLATE_RECORD='1')
        self.listen()
        (self.root/'preview-lines').write_text('een twee\neen twee drie\n')
        (self.root/'heard').write_text('een twee drie vier.\nTot zo.\n')
        self.mute.unlink()
        self.wait_for(lambda: self.spoken().count(b'TICK') >= 2)
        self.mute.touch()
        self.wait_for(lambda: self.spoken().endswith(b'drie vier.'))
        self.say('Tot zo.')
        self.wait_for(lambda: self.spoken().endswith(b'EN:Tot zo.'))
        session = next((self.root/'archive'/'translate').iterdir())
        self.wait_for(lambda: (session/'002-en.wav').exists())
        with wave.open(str(session/'001-en.wav')) as said:
            self.assertEqual(said.getframerate(), 24000)
            english = said.readframes(said.getnframes())
        # The mic carried the first piece, ticks in a real pause, then the rest.
        self.assertTrue(english.startswith(b'EN:een twee'))
        self.assertIn(b'\0' * 960 + b'TICK', english)
        # The fake voice's text is an odd number of bytes; a 16-bit file drops the last one.
        self.assertTrue(english.rstrip(b'\0').endswith(b'drie vier'))
        with wave.open(str(session/'001-nl.wav')) as heard:
            self.assertEqual(heard.getframerate(), 16000)
            self.assertGreater(heard.getnframes(), 0)
        log = (session/'lines.log').read_text()
        self.assertIn('audio: 001-nl.wav, 001-en.wav', log)
        self.assertIn('audio: 002-nl.wav, 002-en.wav', log)

    def test_nothing_is_recorded_unless_asked(self):
        self.listen()
        self.say('Hallo.')
        self.wait_for(lambda: self.spoken() == b'EN:Hallo.')
        self.assertFalse((self.root/'archive'/'translate').exists())
        self.assertFalse((self.state/'translate.current/trace.jsonl').exists())

    def test_the_trace_times_each_step_of_a_phrase_from_its_first_word(self):
        self.env.update(TALKEY_TRANSLATE_PREVIEW_MS='100', TALKEY_TRANSLATE_TRACE='1')
        self.listen()
        (self.root/'preview-lines').write_text('een twee\neen twee drie\n')
        (self.root/'heard').write_text('een twee drie vier.\n')
        self.mute.unlink()
        self.wait_for(lambda: self.spoken() == b'EN:een twee')
        self.mute.touch()
        trace = self.state/'translate.current/trace.jsonl'
        self.wait_for(lambda: trace.exists() and '"event": "spoken"' in trace.read_text())
        events = [json.loads(line) for line in trace.read_text().splitlines()]
        self.assertEqual({event['n'] for event in events}, {1})
        names = [event['event'] for event in events]
        self.assertEqual(names[0], 'speech_start')
        self.assertEqual(names[-1], 'spoken')
        for name in ('preview', 'draft', 'agree', 'speech_end', 'final_asr', 'closed', 'final_draft'):
            self.assertIn(name, names)
        start = events[0]
        # The VAD needs 64 ms of speech before it starts a phrase; the phrase audio begins before that.
        self.assertGreaterEqual(start['detected'], .06)
        self.assertLess(start['audio_from'], 0)
        agreed = [event for event in events if event['event'] == 'agree' and event['result'] == 'spoken']
        self.assertEqual([event['piece'] for event in agreed], ['EN:een twee'])
        queued = [(event['text'], event['last']) for event in events if event['event'] == 'queue']
        self.assertEqual(queued, [('EN:een twee', False), ('drie vier.', True)])
        played = [event for event in events if event['event'] == 'play']
        self.assertEqual([event['text'] for event in played], ['EN:een twee', 'drie vier.'])
        self.assertLess(names.index('tts_first'), names.index('play'))
        for event in played:
            self.assertLessEqual(0, event['start'])
            self.assertLessEqual(event['start'], event['end'])
        self.assertLess(0, next(event['t'] for event in events if event['event'] == 'speech_end'))
        self.assertTrue(all(event['t'] >= -1 for event in events))

    def test_other_sink_names_keep_a_second_instance_private(self):
        self.env.update(TALKEY_TRANSLATE_SINK='test_out', TALKEY_TRANSLATE_MIC='test_out_mic')
        self.listen()
        self.say('Hallo.')
        self.wait_for(lambda: self.spoken() == b'EN:Hallo.')
        self.assertIn('--device=test_out', json.loads((self.root/'pacat-args').read_text().splitlines()[0]))
        self.assertEqual((self.root/'devices').read_text().split(), ['sinks:test_out', 'sources:test_out_mic'])
        self.assertEqual(self.status()['microphone'], 'test_out_mic')

    def test_streaming_off_waits_for_the_pause(self):
        self.env.update(TALKEY_TRANSLATE_PREVIEW_MS='100', TALKEY_TRANSLATE_STREAM='0')
        self.listen()
        (self.root/'preview-lines').write_text('een twee\neen twee drie\n')
        (self.root/'heard').write_text('een twee drie vier.\n')
        self.mute.unlink()
        self.wait_for(lambda: (self.root/'preview-lines').read_text().startswith('een twee drie'))
        time.sleep(.5)
        self.assertEqual(self.spoken(), b'')
        self.mute.touch()
        self.wait_for(lambda: self.spoken() == b'EN:een twee drie vier.')

    def test_recent_sentences_are_whisper_context(self):
        self.listen()
        self.say('Een.', 'Twee.', '...', 'Drie.', 'Vier.')
        self.wait_for(lambda: self.spoken().endswith(b'EN:Vier.'))
        prompts = [json.loads(line) for line in (self.root/'prompts').read_text().splitlines()]
        self.assertEqual(prompts, [None, 'Een.', 'Een. Twee.', 'Een. Twee.', 'Twee. Drie.'])

    def test_empty_and_caption_hallucinations_are_not_spoken(self):
        self.listen()
        self.say('...', 'Ondertiteling door de Amara.org gemeenschap', 'Hallo.')
        self.wait_for(lambda: self.spoken() == b'EN:Hallo.')

    def test_monitor_also_plays_on_the_default_output(self):
        self.env['TALKEY_TRANSLATE_MONITOR'] = '1'
        self.listen()
        self.say('Hallo.')
        self.wait_for(lambda: (self.root/'monitor').exists() and (self.root/'monitor').read_bytes() == b'EN:Hallo.')

    def test_devices_are_created_once_and_kept_after_stop(self):
        self.listen()
        self.run_key('translate', 'stop')
        self.assertEqual(self.status(), {'enabled': False, 'state': 'off', 'microphone': 'talkey_translate_mic'})
        self.listen()
        self.assertEqual((self.root/'devices').read_text().split(),
                         ['sinks:talkey_translate', 'sources:talkey_translate_mic'])

    def test_refuses_to_listen_to_its_own_output(self):
        (self.root/'default-source').write_text('talkey_translate_mic')
        self.assertNotEqual(self.run_key('translate', 'start', check=False).returncode, 0)
        self.assertFalse(self.status()['enabled'])

    def test_missing_voice_fails_before_creating_devices(self):
        self.env['TALKEY_TRANSLATE_VOICE'] = str(self.root/'missing.safetensors')
        self.assertNotEqual(self.run_key('translate', 'start', check=False).returncode, 0)
        self.assertFalse((self.root/'devices').exists())

    def test_auto_and_translate_exclude_each_other(self):
        self.listen('auto')
        self.listen()
        self.assertFalse(self.status('auto')['enabled'])
        self.listen('auto')
        self.assertFalse(self.status()['enabled'])
        self.assertEqual(self.status()['state'], 'off')

    def square(self, *actions):
        for action in actions:
            self.run_key('memo', action)

    def fake_player(self):
        player = self.commands/'player'
        player.write_text(f'#!/bin/sh\necho "$@" >> {self.root}/played\n')
        player.chmod(0o755)
        self.env['TALKEY_PLAYER'] = str(player)

    def test_square_double_tap_toggles_translate_without_a_memo(self):
        self.fake_player()
        self.env.update(TALKEY_DOUBLE_TAP_MS='3000', TALKEY_HOLD_MS='3000')
        self.square('press', 'release', 'press', 'release')
        self.wait_for(lambda: self.status()['state'] == 'listening')
        self.square('press', 'release', 'press', 'release')
        self.assertFalse(self.status()['enabled'])
        self.assertFalse((self.root/'played').exists())
        self.assertEqual(len(list((self.root/'archive/cancelled').glob('memo-*.wav'))), 2)
        self.assertFalse(list((self.root/'archive').glob('memo-*.wav')))

    def test_held_square_still_records_and_plays_a_memo(self):
        self.fake_player()
        self.env.update(TALKEY_DOUBLE_TAP_MS='3000', TALKEY_HOLD_MS='30')
        self.square('press')
        time.sleep(.1)
        self.square('release', 'press')
        self.assertFalse(self.status()['enabled'])
        time.sleep(.1)
        self.square('release')
        self.assertEqual(len((self.root/'played').read_text().splitlines()), 2)
        self.assertEqual(len(list((self.root/'archive').glob('memo-*.wav'))), 2)

    def test_translator_failing_at_startup_is_reported(self):
        (self.root/'fail-translator').touch()
        self.run_key('translate', 'start')
        self.wait_for(lambda: self.status()['state'] == 'error')
        self.assertFalse(self.status()['enabled'])

    def test_worker_error_is_reported(self):
        self.listen()
        (self.root/'heard').write_text('')
        self.mute.unlink()
        self.wait_for(lambda: self.status()['state'] == 'hearing')
        self.mute.touch()
        self.wait_for(lambda: self.status()['state'] == 'error')
        self.assertFalse(self.status()['enabled'])


if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).parent))
    unittest.main()
