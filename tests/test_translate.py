"""Translate mode with fake audio devices, VAD, recognition, translation and voice."""
from http.server import BaseHTTPRequestHandler, HTTPServer
import importlib.machinery
import importlib.util
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
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
        agreement = worker.Agreement()
        self.assertIsNone(agreement.offer('We moeten volgende week de release'.split(),
                                          'We need to release it next week'))
        self.assertEqual(agreement.decision['result'], 'first-draft')
        # "uitstellen" (postpone) changed the earlier draft after "We need to", so "release" was a guess.
        self.assertEqual(agreement.offer('We moeten volgende week de release nog even uitstellen'.split(),
                                         'We need to postpone the release for another week'), 'We need to')
        self.assertEqual(agreement.commit(), ('We need to', False, False))
        self.assertEqual(agreement.spoken, 'We need to')

    def test_a_stable_head_is_settled_although_the_tail_changed(self):
        agreement = worker.Agreement()
        agreement.offer('Morgen ga ik'.split(), 'Tomorrow I will')
        self.assertEqual(agreement.offer('Morgen ga ik even'.split(), "Tomorrow I'll just"), 'Tomorrow')
        self.assertEqual(agreement.decision, {'result': 'settled', 'piece': 'Tomorrow'})

    def test_settled_english_is_spoken_as_the_later_draft_writes_it(self):
        agreement = worker.Agreement()
        self.assertIsNone(agreement.offer(['Hallo', 'allemaal'], 'Hello everyone'))
        self.assertEqual(agreement.offer('Hallo allemaal ik deel'.split(), 'Hello everyone, I am sharing'),
                         'Hello everyone,')
        agreement.commit()
        self.assertEqual(agreement.said, ['Hallo', 'allemaal'])
        self.assertEqual(agreement.decision,
                         {'result': 'spoken', 'piece': 'Hello everyone,', 'dutch': 'Hallo allemaal', 'via': 'agreed'})

    def test_nothing_is_settled_when_more_dutch_changes_the_first_word(self):
        agreement = worker.Agreement()
        agreement.offer(['Hallo', 'allemaal'], 'Hello everyone')
        self.assertIsNone(agreement.offer('Hallo allemaal ik deel'.split(), 'Hi all, I am sharing'))
        self.assertEqual(agreement.decision['result'], 'no-agreement')
        self.assertIsNone(agreement.offer('Dag allemaal ik deel'.split(), 'Bye all, I am sharing'))
        self.assertEqual(agreement.decision['result'], 'dutch-changed')

    def test_a_later_piece_continues_the_spoken_english(self):
        agreement = worker.Agreement()
        agreement.offer(['Hallo', 'allemaal'], 'Hello everyone')
        agreement.offer('Hallo allemaal ik deel'.split(), 'Hello everyone, I am sharing')
        agreement.commit()
        heard = 'Hallo allemaal ik deel even mijn scherm'.split()
        self.assertIsNone(agreement.offer(heard, 'I am sharing my screen'))
        self.assertEqual(agreement.offer(heard + ['en', 'dan'], 'I am sharing my screen, and then'),
                         'I am sharing my screen,')

    def test_a_draft_that_carries_on_inside_the_last_word_joins_it(self):
        agreement = worker.Agreement()
        agreement.offer('Morgen ga ik'.split(), 'Tomorrow I')
        agreement.offer('Morgen ga ik even'.split(), 'Tomorrow I will')
        agreement.commit()
        self.assertEqual(agreement.spoken, 'Tomorrow I')
        agreement.offer('Morgen ga ik even bellen'.split(), "'ll just", joined=True)
        self.assertEqual(agreement.offer('Morgen ga ik even bellen met'.split(), "'ll just call", joined=True), "'ll just")
        self.assertEqual(agreement.commit(), ("'ll just", True, False))
        self.assertEqual(agreement.spoken, "Tomorrow I'll just")

    def test_a_sentence_the_model_says_may_end_is_spoken_without_more_dutch(self):
        agreement = worker.Agreement(.2)
        self.assertEqual(agreement.offer('Ik deel even mijn scherm.'.split(), 'I am sharing my screen.', .6),
                         'I am sharing my screen.')
        agreement.commit()
        self.assertEqual(agreement.decision['via'], 'end')
        self.assertEqual(agreement.said, 'Ik deel even mijn scherm.'.split())

    def test_an_open_clause_waits_for_more_dutch(self):
        agreement = worker.Agreement(.2)
        self.assertIsNone(agreement.offer('Ik heb gisteren met de klant'.split(), 'Yesterday I spoke with the client', .01))
        self.assertEqual(agreement.decision['result'], 'first-draft')

    def test_a_whole_sentence_stays_settled_while_its_dutch_stays(self):
        agreement = worker.Agreement(.2)
        heard = 'Ik deel even mijn scherm.'.split()
        self.assertEqual(agreement.offer(heard, 'I am sharing my screen.', .6), 'I am sharing my screen.')
        # The next sentence makes the model reword the first one; the whole draft still stands.
        self.assertEqual(agreement.offer(heard + ['Dan', 'zie'], 'I am showing my screen. Then', .01),
                         'I am sharing my screen.')
        self.assertEqual(agreement.settled[-1], 'end')

    def test_a_whole_sentence_is_dropped_when_its_dutch_changes(self):
        agreement = worker.Agreement(.2)
        agreement.offer('Ik deel even mijn scherm.'.split(), 'I am sharing my screen.', .6)
        self.assertIsNone(agreement.offer('Ik deel even mijn schermen'.split(), 'I am sharing my screens', .01))
        self.assertIsNone(agreement.whole)


class SettledTests(unittest.TestCase):
    def test_words_two_transcripts_agree_on_are_trusted_up_to_a_change(self):
        words = [('Nee,', .9), ('dan', .9), ('bel', .9), ('je', .9), ('morgen', .9)]
        self.assertEqual(worker.settled(words, [['nee', 'dan', 'bel', 'ik']], .5), words[:3])
        self.assertEqual(worker.settled(words, [], .5), [])

    def test_a_word_whisper_is_unsure_of_needs_a_third_transcript(self):
        words = [('Nee,', .9), ('dan', .3), ('bel', .9), ('ik', .9)]
        self.assertEqual(worker.settled(words, [['nee', 'dan', 'bel', 'ik']], .5), words[:1])
        self.assertEqual(worker.settled(words, [['nee', 'dan', 'bel'], ['nee', 'dan', 'bel', 'ik']], .5), words)
        # Three transcripts must agree on everything up to that word.
        self.assertEqual(worker.settled(words, [['nee', 'of'], ['nee', 'dan', 'bel', 'ik']], .5), words[:1])


class WordsTests(unittest.TestCase):
    def test_tokens_join_into_words_with_their_least_sure_probability(self):
        import speech
        reply = {'text': ' Nee, dan morgenochtend [BLANK_AUDIO]', 'segments': [{'no_speech_prob': .01, 'words': [
            {'word': ' Nee', 'probability': .9}, {'word': ',', 'probability': .6}, {'word': ' dan', 'probability': .8},
            {'word': ' morgen', 'probability': .95}, {'word': 'ochtend', 'probability': .7},
            {'word': ' [BLANK_AUDIO]', 'probability': .5}]}]}
        with patch.object(speech, 'inference', return_value=json.dumps(reply)):
            self.assertEqual(speech.transcribe_words(b'', 'url', 'nl', None, 16000, 1),
                             ('Nee, dan morgenochtend', [('Nee,', .6), ('dan', .8), ('morgenochtend', .7)], .01))


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

                def stream(self, text, last=True, joined=False):
                    yield bytes(2 * 300)

                def seconds(self, text):
                    return .3

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
            interpreter.queue(phrase, 'Hello there,')
            interpreter.queue(phrase, 'how are you?', last=True)
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


class QueueTests(unittest.TestCase):
    def test_a_piece_that_carries_on_inside_a_word_is_joined_to_it(self):
        with tempfile.TemporaryDirectory(prefix='talkey-queue-') as tmp:
            state, run = Path(tmp)/'state', Path(tmp)/'run'
            state.mkdir(); run.mkdir()
            env = {'TALKEY_TRANSLATE_RUN': str(run), 'TALKEY_STATE_DIR': str(state)}
            os.environ.update(env)
            self.addCleanup(lambda: [os.environ.pop(name) for name in env])
            interpreter = worker.Interpreter(None, None, None)
            phrase = worker.Phrase(time.monotonic(), 1)
            interpreter.subtitles.append(phrase.shown)
            interpreter.queue(phrase, 'Tomorrow I')
            interpreter.queue(phrase, "'ll call you.", True, last=True)
            self.assertEqual(phrase.shown['english'], "Tomorrow I'll call you.")
            self.assertEqual([phrase.segments.get()[1:], phrase.segments.get()[1:]],
                             [(False, False, False), (True, True, True)])


class TimingTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='talkey-timing-')
        self.addCleanup(tmp.cleanup)
        state, run = Path(tmp.name)/'state', Path(tmp.name)/'run'
        state.mkdir(); run.mkdir()
        env = {'TALKEY_TRANSLATE_RUN': str(run), 'TALKEY_STATE_DIR': str(state)}
        os.environ.update(env)
        self.addCleanup(lambda: [os.environ.pop(name) for name in env])

        class Translate:
            drafts = {'Hallo allemaal': 'Hello everyone, welcome to the meeting',
                      'Hallo allemaal ik deel': 'Hello everyone, welcome to the meeting, I am sharing'}

            def __call__(self, text, spoken='', context=''):
                return self.drafts[text], False

            def ends(self, text):
                return 0.0

        class Voice:
            sample_rate = 1000

            def seconds(self, text):
                return len(text) * .06

        self.interpreter = worker.Interpreter(None, None, Translate())
        self.interpreter.voice = Voice()
        self.interpreter.first_audio.append(.25)
        self.phrase = worker.Phrase(time.monotonic(), 1)
        self.interpreter.subtitles.append(self.phrase.shown)

    def say(self, *texts):
        for text in texts:
            self.interpreter.advance(self.phrase, text)

    def test_a_piece_that_lasts_until_more_is_usually_settled_goes_out(self):
        self.say('Hallo allemaal', 'Hallo allemaal ik deel')
        self.assertEqual(self.phrase.segments.get()[0]['text'], 'Hello everyone, welcome to the meeting,')

    def test_a_piece_is_held_while_the_voice_would_run_dry_before_more_is_settled(self):
        self.interpreter.refill.extend([5.0] * 20)
        self.say('Hallo allemaal', 'Hallo allemaal ik deel')
        self.assertEqual(self.phrase.agreement.decision['result'], 'would-run-dry')
        self.assertTrue(self.phrase.segments.empty())

    def test_a_piece_waits_while_the_voice_has_enough_to_say(self):
        self.interpreter.voice_end = time.monotonic() + 3
        self.say('Hallo allemaal', 'Hallo allemaal ik deel')
        self.assertEqual(self.phrase.agreement.decision['result'], 'voice-busy')

    def test_the_pace_follows_what_is_waiting_and_what_is_expected(self):
        interpreter = self.interpreter
        interpreter.refill.extend([2.0] * 20)
        interpreter.speaking = (self.phrase, False, 1.0)
        self.assertEqual(interpreter.pace(), interpreter.speed_min)
        interpreter.speaking = (self.phrase, False, 1.9)
        self.assertAlmostEqual(interpreter.pace(), .95 * 1.9 / 2)
        interpreter.speaking = (self.phrase, False, 4.0)
        self.assertEqual(interpreter.pace(), interpreter.speed)
        self.phrase.waiting = 1
        self.assertEqual(interpreter.pace(), interpreter.speed_max)
        self.phrase.waiting = 0
        interpreter.speaking = (self.phrase, True, 1.0)
        self.assertEqual(interpreter.pace(), interpreter.speed)
        interpreter.lines.put(worker.Phrase(time.monotonic(), 2))
        self.assertEqual(interpreter.pace(), interpreter.speed_max)

    def test_a_tick_fills_a_wait_inside_a_sentence_only(self):
        interpreter = self.interpreter
        interpreter.tick_sound = b'TICK'
        interpreter.ended = True
        self.assertIsNone(interpreter.tick(None))
        interpreter.ended = False
        self.assertIsNotNone(interpreter.tick(None))


class TranslatorTests(unittest.TestCase):
    def setUp(self):
        self.requests, self.generated, self.reply = [], [], None
        self.logprobs = [{'token': '.', 'logprob': -1, 'top_logprobs': [
            {'token': '.', 'logprob': math.log(.4)}, {'token': ' van', 'logprob': math.log(.3)},
            {'token': '?', 'logprob': math.log(.1)}, {'token': '', 'logprob': math.log(.05)},
            {'token': ',', 'logprob': math.log(.05)}, {'token': ' ', 'logprob': math.log(.05)}]}]
        test = self

        class Ollama(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                if self.path == '/api/generate':
                    test.generated.append(body)
                    reply = json.dumps({'response': '.', 'logprobs': test.logprobs}).encode()
                else:
                    test.requests.append(body)
                    content = test.reply if test.reply is not None else f' "EN {len(test.requests)}" \n'
                    reply = json.dumps({'message': {'content': content}}).encode()
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
        self.assertEqual(translate('warm'), ('EN 1', False))
        for i in range(2, 6):
            self.assertEqual(translate(f'nl {i}'), (f'EN {i}', False))
            translate.remember(f'nl {i}', f'EN {i}')
        messages = self.requests[-1]['messages']
        prompt = translate.prompt
        self.assertTrue(prompt.startswith('You are a professional Dutch (nl) to English (en) translator.'))
        self.assertEqual([m['content'] for m in messages],
                         [prompt + 'nl 2', 'EN 2', prompt + 'nl 3', 'EN 3', prompt + 'nl 4', 'EN 4', prompt + 'nl 5'])
        self.assertFalse(self.requests[-1]['stream'])

    def test_the_sentence_end_chance_is_the_model_s_closing_tokens(self):
        translate = worker.Translator()
        translate.remember('Ik ben er.', 'I am here.')
        self.assertAlmostEqual(translate.ends('Ik bel je morgen even op.'), .55)
        body = self.generated[-1]
        # The same Ollama, asked as a plain language model for one token, without the sentence end.
        self.assertEqual(body['prompt'], 'Ik ben er. Ik bel je morgen even op')
        self.assertTrue(body['raw'])
        self.assertTrue(body['logprobs'])
        self.assertEqual(body['options']['num_predict'], 1)

    def test_no_token_probabilities_is_an_error(self):
        self.logprobs = None
        with self.assertRaisesRegex(RuntimeError, 'token probabilities'):
            worker.Translator().ends('Hallo.')

    def test_a_reply_that_carries_on_inside_the_last_word_says_so(self):
        translate = worker.Translator()
        self.reply = "'ll show it."
        self.assertEqual(translate('Ik zal het laten zien.', spoken='I'), ("'ll show it.", True))
        self.reply = ' will show it.'
        self.assertEqual(translate('Ik zal het laten zien.', spoken='I'), ('will show it.', False))
        self.reply = '. Then it.'
        self.assertEqual(translate('Ik zal het laten zien.', spoken='I will.'), ('Then it.', False))

    def test_notes_and_live_context_go_before_the_text_and_are_read_when_changed(self):
        with tempfile.TemporaryDirectory(prefix='talkey-context-') as tmp:
            notes, state = Path(tmp)/'notes.txt', Path(tmp)/'state'
            state.mkdir()
            env = {'TALKEY_TRANSLATE_NOTES': str(notes), 'TALKEY_STATE_DIR': str(state)}
            os.environ.update(env)
            self.addCleanup(lambda: [os.environ.pop(name) for name in env])
            translate = worker.Translator()
            self.assertEqual(translate.context(), '')
            notes.write_text('Weekly planning with the design team.\n')
            (state/'translate.context').write_text('They asked about the release date.\n')
            context = translate.context()
            self.assertEqual(context, 'Weekly planning with the design team.\nThey asked about the release date.')
            translate('Dan schuift hij een week op.', context=context)
            prompt = self.requests[-1]['messages'][-1]['content']
            self.assertIn('Context of the conversation, for reference only, do not translate it:\n' + context
                          + '\n\nPlease translate the following Dutch text into English:', prompt)
            self.assertTrue(prompt.endswith('Dan schuift hij een week op.'))
            os.utime(notes, ns=(1, 1))
            notes.write_text('Another meeting.')
            self.assertTrue(translate.context().startswith('Another meeting.'))

    def test_spoken_english_is_continued_without_its_sentence_end(self):
        translate = worker.Translator()
        self.assertEqual(translate('heel de zin', spoken="Okay, I'm getting."), ('EN 1', False))
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
        def stream(self, text, last=True, joined=False): yield text.encode()
        def seconds(self, text): return len(text) * .1
        def end(self): pass
    class FakeTranslator:
        def context(self):
            notes = root/'notes'
            return notes.read_text().strip() if notes.exists() else ''
        def __call__(self, text, spoken='', context=''):
            if context:
                with (root/'contexts').open('a') as f: f.write(json.dumps(context)+'\\n')
            if (root/'fail-translator').exists(): raise RuntimeError('test translator down')
            english = 'EN:' + text
            return (english[len(spoken):].strip() if spoken and english.startswith(spoken) else english), False
        def remember(self, text, english): pass
        def ends(self, text):
            return float((root/'ends').read_text()) if (root/'ends').exists() else 0.0
    def words(text):
        return text, [(word, .1 if word.startswith('~') else 1.0) for word in text.split()], 0.0
    def heard(*args):
        with (root/'prompts').open('a') as f: f.write(json.dumps(args[3])+'\\n')
        lines=(root/'heard').read_text().splitlines()
        (root/'heard').write_text(''.join(line + '\\n' for line in lines[1:]))
        return words(lines[0])
    def previewed(*args):
        path = root/'preview-lines'
        if not path.exists(): return words('voorlopig')
        lines = path.read_text().splitlines()
        if len(lines) > 1: path.write_text(''.join(line + '\\n' for line in lines[1:]))
        return words(lines[0])
    ns['main'].__globals__.update(Silero=FakeVad, Voice=FakeVoice, Translator=FakeTranslator,
                                  transcribe_words=heard, preview=previewed)
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

    def test_only_words_whisper_stands_by_are_translated(self):
        self.env.update(TALKEY_TRANSLATE_PREVIEW_MS='100', TALKEY_TRANSLATE_TRACE='1')
        self.listen()
        # "twee" changes in the next preview, and whisper is unsure of "vier" until a third preview agrees.
        (self.root/'preview-lines').write_text('een twee\neen drie ~vier\neen drie ~vier vijf\n')
        (self.root/'heard').write_text('een drie vier vijf.\n')
        self.mute.unlink()
        self.wait_for(lambda: (self.root/'preview-lines').read_text().startswith('een drie ~vier vijf'))
        time.sleep(.3)
        self.mute.touch()
        trace = self.state/'translate.current/trace.jsonl'
        self.wait_for(lambda: trace.exists() and '"event": "spoken"' in trace.read_text())
        events = [json.loads(line) for line in trace.read_text().splitlines()]
        sources = [e['source'] for e in events if e['event'] == 'draft']
        self.assertEqual(sorted(set(sources), key=sources.index), ['een', 'een drie', 'een drie ~vier vijf'])
        self.assertEqual([e['trusted'] for e in events if e['event'] == 'preview'][:4], [0, 1, 2, 4])
        # The final transcript after the pause is trusted whole.
        self.assertEqual(next(e['source'] for e in events if e['event'] == 'final_draft'), 'een drie vier vijf.')

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

    def test_conversation_context_reaches_the_translator_and_whisper_per_phrase(self):
        self.listen()
        (self.root/'notes').write_text('Planning van het team.')
        self.say('Een.', 'Twee.')
        self.wait_for(lambda: self.spoken().endswith(b'EN:Twee.'))
        prompts = [json.loads(line) for line in (self.root/'prompts').read_text().splitlines()]
        self.assertEqual(prompts, ['Planning van het team.', 'Planning van het team. Een.'])
        contexts = {json.loads(line) for line in (self.root/'contexts').read_text().splitlines()}
        self.assertEqual(contexts, {'Planning van het team.'})

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
