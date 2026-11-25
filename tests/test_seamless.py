"""The Seamless engine without the model: word completion, the server's loop, the worker's side
and a whole translate session, with fakes."""
import base64
from collections import deque
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch

import test_translate

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'lib'))
import seamless  # noqa: E402

# Loading it here, where torch and fairseq2 are missing, shows the server imports them only to load the model.
loader = importlib.machinery.SourceFileLoader('seamless_server', str(ROOT/'bin/talkey-seamless-server'))
spec = importlib.util.spec_from_loader(loader.name, loader)
server = importlib.util.module_from_spec(spec)
loader.exec_module(server)

FRAME = bytes(1024)


class WordsTests(unittest.TestCase):
    def test_tokens_split_over_steps_make_one_word(self):
        words = seamless.Words()
        self.assertEqual(words.feed(['▁Good', '▁mor']), ['Good'])
        self.assertEqual(words.feed(['ning', '▁all']), ['morning'])
        self.assertEqual(words.feed([], end=True), ['all'])

    def test_a_token_without_the_word_mark_joins_the_open_word(self):
        words = seamless.Words()
        self.assertEqual(words.feed(['▁We', "'", 're', '▁at', '▁gate', '▁4', '2', '.', '▁Then']),
                         ["We're", 'at', 'gate', '42.'])
        self.assertEqual(words.open, 'Then')

    def test_the_open_word_waits_until_the_model_or_the_phrase_ends_it(self):
        words = seamless.Words()
        self.assertEqual(words.feed(['▁It', '▁rains']), ['It'])
        self.assertEqual(words.feed(['.'], end=True), ['rains.'])
        self.assertEqual(words.feed(['▁Bye'], end=False), [])
        self.assertEqual(words.feed([], end=True), ['Bye'])
        self.assertEqual(words.feed([], end=True), [])

    def test_a_lone_word_mark_makes_no_empty_word(self):
        words = seamless.Words()
        self.assertEqual(words.feed(['▁', '▁', '▁yes', '▁']), ['yes'])
        self.assertEqual(words.feed(['▁no'], end=True), ['no'])


class FakeModel:
    def __init__(self):
        self.english_calls, self.begun = [], 0
        self.running, self.stepping = threading.Event(), threading.Event()
        self.running.set()
        self.fail = None

    def ready(self):
        return {'load_s': 1.0, 'gpu_mb': 100, 'step_s': .1}

    def begin(self):
        self.begun += 1

    def english(self, pcm, finished):
        self.stepping.set()
        self.running.wait()
        if self.fail:
            raise self.fail
        self.english_calls.append((len(pcm) // len(FRAME), finished))
        return [f'word{len(self.english_calls)}'], False

    def source(self, finished):
        return [] if finished else ['woord']


class ServeTests(unittest.TestCase):
    def setUp(self):
        self.lines, self.events, self.result = queue.Queue(), [], []

    def serve(self, model):
        load = model if callable(model) else lambda: model
        thread = threading.Thread(target=lambda: self.result.append(
            server.serve(load, iter(self.lines.get, None), self.events.append)), daemon=True)
        thread.start()
        self.addCleanup(self.lines.put, None)

    def send(self, op, n, frames=0):
        if op == 'audio':
            for _ in range(frames):
                self.lines.put(json.dumps({'op': 'audio', 'n': n, 'pcm': base64.b64encode(FRAME).decode()}))
        else:
            self.lines.put(json.dumps({'op': op, 'n': n}))

    def wait_for(self, predicate):
        deadline = time.monotonic() + 3
        while not predicate():
            self.assertLess(time.monotonic(), deadline, 'condition timed out')
            time.sleep(.01)

    def named(self, *names):
        return [event for event in self.events if event['event'] in names]

    def test_a_step_waits_for_a_third_of_a_second_of_audio(self):
        model = FakeModel()
        self.serve(model)
        self.send('start', 1)
        self.send('audio', 1, 9)
        time.sleep(.1)
        self.assertEqual(self.named('step'), [])
        self.send('audio', 1, 1)
        self.wait_for(lambda: self.named('step'))
        self.assertEqual(model.english_calls, [(10, False)])
        self.assertEqual(self.events[0], {'event': 'ready', 'load_s': 1.0, 'gpu_mb': 100, 'step_s': .1})
        step = self.named('step')[0]
        self.assertEqual((step['n'], step['heard_s'], step['words'], step['end']), (1, .32, ['word1'], False))

    def test_audio_that_waited_for_a_slow_step_goes_in_one_step(self):
        model = FakeModel()
        model.running.clear()
        self.serve(model)
        self.send('start', 1)
        self.send('audio', 1, 10)
        self.wait_for(model.stepping.is_set)
        self.send('audio', 1, 25)
        self.wait_for(self.lines.empty)
        time.sleep(.05)
        model.running.set()
        self.wait_for(lambda: len(self.named('step')) == 2)
        self.assertEqual(model.english_calls, [(10, False), (25, False)])
        self.assertEqual(self.named('step')[-1]['heard_s'], 1.12)

    def test_english_comes_before_the_source_words_and_phrases_stay_in_order(self):
        model = FakeModel()
        self.serve(model)
        for n in (1, 2):
            self.send('start', n)
            self.send('audio', n, 10)
            self.wait_for(lambda: len(self.named('step')) == n)
            self.send('audio', n, 3)
            self.send('end', n)
            self.wait_for(lambda: len(self.named('done')) == n)
        self.lines.put(None)
        self.wait_for(lambda: self.result)
        self.assertEqual(self.result, [0])
        self.assertEqual(model.begun, 2)
        self.assertEqual(model.english_calls, [(10, False), (3, True)] * 2)
        steps = [(event['event'], event['n'], event.get('final')) for event in self.events[1:]]
        self.assertEqual(steps, [('step', 1, None), ('source', 1, False), ('done', 1, None), ('source', 1, True),
                                 ('step', 2, None), ('source', 2, False), ('done', 2, None), ('source', 2, True)])
        done = self.named('done')[0]
        self.assertEqual((done['heard_s'], done['words']), (.416, ['word2']))
        self.assertEqual(self.named('source')[1]['words'], [])

    def test_a_failing_step_is_one_error_and_exit_status_1(self):
        model = FakeModel()
        model.fail = RuntimeError('the model broke\nmore detail')
        self.serve(model)
        self.send('start', 1)
        self.send('audio', 1, 10)
        self.wait_for(lambda: self.result)
        self.assertEqual(self.result, [1])
        self.assertEqual(self.events[1:], [{'event': 'error', 'message': 'RuntimeError: the model broke'}])

    def test_a_model_that_cannot_load_is_one_error(self):
        def load():
            raise ValueError('no CUDA device')
        self.serve(load)
        self.wait_for(lambda: self.result)
        self.assertEqual((self.result, self.events), ([1], [{'event': 'error', 'message': 'ValueError: no CUDA device'}]))

    def test_the_server_exits_once_its_input_ends_even_while_it_loads(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            # Stand-ins for the Seamless stack: loading takes a minute.
            (tmp/'stack').mkdir()
            (tmp/'stack/numpy.py').write_text('')
            (tmp/'stack/torch.py').write_text('import time\ntime.sleep(60)\n')
            env = dict(os.environ, PYTHONPATH=str(tmp/'stack'), TALKEY_TRANSLATE_RUN=str(tmp),
                       TALKEY_SEAMLESS_HOME=str(tmp/'home'))
            process = subprocess.Popen([sys.executable, str(ROOT/'bin/talkey-seamless-server')], env=env,
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE)
            self.addCleanup(process.stdout.close)
            self.addCleanup(process.wait)
            self.addCleanup(process.kill)
            self.wait_for(lambda: (tmp/'seamless/cards/talkey.yaml').exists())
            process.stdin.close()
            self.assertEqual(process.wait(timeout=5), 0)
            self.assertEqual(process.stdout.read(), b'')

    def test_the_cards_point_every_model_file_at_the_local_copy(self):
        card = server.card(Path('/data/seamless/models'))
        uris = sorted(line.split(': ', 1)[1] for line in card.splitlines() if 'file://' in line)
        self.assertEqual(uris, sorted(f'"file:///data/seamless/models/{name}"' for name in seamless.WEIGHTS))
        self.assertIn('tokenizer: "file:///data/seamless/models/tokenizer.model"', card)
        self.assertEqual([line for line in card.splitlines() if line.startswith('name:')],
                         ['name: seamless_streaming_unity@user', 'name: seamless_streaming_monotonic_decoder@user'])


class FakeOut:
    """Only what an engine may use of the Interpreter: anything else is an AttributeError."""
    __slots__ = ('cycle', 'calls', 'waits', 'events', 'errors')

    def __init__(self, waits=()):
        self.cycle, self.calls, self.waits, self.events, self.errors = deque(maxlen=20), [], list(waits), [], []

    def trace(self, phrase, event, **fields):
        self.events.append((event, fields))

    def guard(self, work):
        def run():
            try:
                work()
            except Exception as error:
                self.errors.append(error)
        threading.Thread(target=run, daemon=True).start()

    def heard(self, phrase, text, live=True):
        self.calls.append(('heard', text, live))

    def gate(self, phrase, piece):
        self.calls.append(('gate', piece))
        return (self.waits.pop(0) if self.waits else None), 0.0, 1.0, 1.0

    def queue(self, phrase, text, joined=False, ended=False, last=False, **trace):
        self.calls.append(('queue', text, last, ended))

    def finish(self, phrase):
        self.calls.append(('finish',))

    def drop(self, phrase):
        self.calls.append(('drop',))

    def ends(self):
        return [call for call in self.calls if call[0] in ('finish', 'drop')]


class EngineTests(unittest.TestCase):
    def engine(self, out):
        engine = seamless.SeamlessEngine(out)
        engine.process = types.SimpleNamespace(stdin=io.StringIO())
        phrase = types.SimpleNamespace(number=1, frames=[b'a' * 1024, b'b' * 1024], audio_from=0.0)
        engine.begin(phrase)
        return engine, phrase

    def step(self, engine, *words, end=False):
        engine.event({'event': 'step', 'n': 1, 'heard_s': .32, 'compute_s': .05, 'words': list(words), 'end': end})

    def test_the_phrase_goes_to_the_server_as_it_is_heard(self):
        engine, phrase = self.engine(FakeOut())
        phrase.frames.append(b'c' * 1024)
        engine.hear(phrase)
        engine.hear(phrase)
        phrase.frames.append(b'd' * 1024)
        engine.end(phrase)
        sent = [json.loads(line) for line in engine.process.stdin.getvalue().splitlines()]
        self.assertEqual([(m['op'], base64.b64decode(m.get('pcm', ''))) for m in sent],
                         [('start', b''), ('audio', b'a' * 1024 + b'b' * 1024), ('audio', b'c' * 1024),
                          ('audio', b'd' * 1024), ('end', b'')])
        self.assertEqual({m['n'] for m in sent}, {1})

    def test_whole_words_go_to_the_voice_through_the_shared_gate(self):
        out = FakeOut()
        engine, _ = self.engine(out)
        self.step(engine)
        self.step(engine, 'Good', 'morning')
        self.step(engine, 'all.', end=True)
        self.assertEqual(out.calls, [('gate', 'Good morning'), ('queue', 'Good morning', False, False),
                                     ('gate', 'all.'), ('queue', 'all.', False, True)])
        self.assertEqual(len(out.cycle), 3)
        agreed = [fields for event, fields in out.events if event == 'agree']
        self.assertEqual([(a['result'], a['piece'], a['eot']) for a in agreed],
                         [('spoken', 'Good morning', False), ('spoken', 'all.', True)])

    def test_a_held_piece_grows_and_is_offered_again_on_every_step(self):
        out = FakeOut(waits=['would-run-dry', 'voice-busy', 'voice-busy'])
        engine, _ = self.engine(out)
        self.step(engine, 'We')
        self.step(engine)
        self.step(engine, 'start', 'now')
        self.step(engine)
        self.assertEqual(out.calls, [('gate', 'We'), ('gate', 'We'), ('gate', 'We start now'), ('gate', 'We start now'),
                                     ('queue', 'We start now', False, False)])
        results = [fields['result'] for event, fields in out.events if event == 'agree']
        self.assertEqual(results, ['would-run-dry', 'voice-busy', 'voice-busy', 'spoken'])

    def test_the_rest_goes_out_as_the_last_piece_when_the_phrase_ends(self):
        out = FakeOut(waits=['voice-busy'])
        engine, _ = self.engine(out)
        self.step(engine, 'See', 'you')
        engine.event({'event': 'done', 'n': 1, 'heard_s': .5, 'compute_s': .1, 'words': ['soon.'], 'end': False})
        engine.event({'event': 'source', 'n': 1, 'words': ['Tot', 'ziens.'], 'final': True})
        self.assertEqual(out.calls, [('gate', 'See you'), ('queue', 'See you soon.', True, False), ('finish',),
                                     ('heard', 'Tot ziens.', False)])
        self.assertEqual(engine.phrases, {})
        self.assertEqual([event for event, _ in out.events], ['step', 'agree', 'closed', 'final', 'source'])

    def test_a_phrase_without_english_leaves_the_subtitles(self):
        out = FakeOut()
        engine, _ = self.engine(out)
        self.step(engine)
        engine.event({'event': 'source', 'n': 1, 'words': ['eh'], 'final': False})
        engine.event({'event': 'done', 'n': 1, 'heard_s': .5, 'compute_s': .1, 'words': [], 'end': False})
        engine.event({'event': 'source', 'n': 1, 'words': [], 'final': True})
        self.assertEqual(out.calls, [('heard', 'eh', True), ('drop',), ('heard', 'eh', False)])

    def test_an_error_from_the_server_stops_the_session(self):
        engine, _ = self.engine(FakeOut())
        with self.assertRaisesRegex(RuntimeError, 'not enough GPU memory'):
            engine.event({'event': 'error', 'message': 'not enough GPU memory for Seamless'})


class ContractTests(unittest.TestCase):
    """Each engine works with only the contract's members of the Interpreter and ends every phrase once."""

    def test_the_whisper_engine_keeps_to_the_contract(self):
        worker = test_translate.worker

        class Translate:
            def __call__(self, text, spoken='', context=''):
                return 'EN ' + text, False

            def ends(self, text):
                return 0.0

            def context(self):
                return ''

            def remember(self, text, english):
                pass

        def words(text):
            return text, [(word, 1.0) for word in text.split()], 0.0

        out = FakeOut()
        with patch.object(test_translate.engine, 'preview', lambda *args: words('een twee')), \
                patch.object(test_translate.engine, 'transcribe_words', lambda *args: words('een twee drie.')):
            engine = test_translate.engine.WhisperEngine(out, Translate())
            engine.start()
            phrase = worker.Phrase(time.monotonic(), 1)
            phrase.frames = [bytes(1024)] * 3
            engine.begin(phrase)
            for _ in range(2):
                phrase.frames += [bytes(1024)] * engine.preview_frames
                engine.hear(phrase)
                engine.previewing.result()
            phrase.audio = b''.join(phrase.frames)
            engine.end(phrase)
            deadline = time.monotonic() + 3
            while not out.ends():
                self.assertLess(time.monotonic(), deadline)
                time.sleep(.01)
            time.sleep(.1)
            engine.close()
        self.assertEqual(out.errors, [])
        self.assertEqual(out.ends(), [('finish',)])
        self.assertIn(('heard', 'een twee drie.', False), out.calls)
        self.assertEqual([call for call in out.calls if call[0] == 'queue'][-1], ('queue', 'EN een twee drie.', True, False))

    def test_the_seamless_engine_keeps_to_the_contract(self):
        out = FakeOut()
        engine = seamless.SeamlessEngine(out)
        engine.process = types.SimpleNamespace(stdin=io.StringIO())
        for n in (1, 2):
            phrase = types.SimpleNamespace(number=n, frames=[bytes(1024)], audio_from=0.0)
            engine.begin(phrase)
            engine.end(phrase)
        for line in ['{"event": "step", "n": 1, "heard_s": 0.32, "compute_s": 0.1, "words": ["Hi"], "end": false}',
                     '{"event": "done", "n": 1, "heard_s": 0.4, "compute_s": 0.1, "words": [], "end": false}',
                     '{"event": "source", "n": 1, "words": ["Hoi"], "final": true}',
                     '{"event": "done", "n": 2, "heard_s": 0.4, "compute_s": 0.1, "words": [], "end": false}',
                     '{"event": "source", "n": 2, "words": [], "final": true}']:
            engine.event(json.loads(line))
        self.assertEqual(out.ends(), [('finish',), ('drop',)])
        self.assertEqual(out.errors, [])


FAKE_SERVER = '''if len(sys.argv) > 1 and sys.argv[1].endswith('/talkey-seamless-server'):
    import base64, json
    root = pathlib.Path(os.environ['TEST_ROOT'])
    if sys.argv[2:] == ['--check']:
        sys.exit(1 if (root/'seamless-broken').exists() else 0)
    def emit(**event):
        print(json.dumps(event), flush=True)
    if (root/'seamless-fail-start').exists():
        emit(event='error', message='not enough GPU memory for Seamless: 100 of 8000 MiB free')
        sys.exit(1)
    if (root/'seamless-slow-load').exists():
        # Like the real load, it reads nothing until it is ready.
        import time
        (root/'seamless-loading').write_text(str(os.getpid()))
        time.sleep(10)
    emit(event='ready', load_s=.1, gpu_mb=100, step_s=.1)
    def lines(name):
        path = root/name
        return path.read_text().splitlines() if path.exists() else []
    for line in sys.stdin:
        message = json.loads(line)
        if message['op'] == 'start':
            n, heard, pending = message['n'], 0, 0
            english = (lines('seamless-english')[n - 1:n] or [''])[0].split()
            own = (lines('seamless-source')[n - 1:n] or [''])[0].split()
        elif message['op'] == 'audio':
            pending += len(base64.b64decode(message['pcm']))
            if pending >= 10240:
                if (root/'seamless-die').exists():
                    sys.exit(1)
                heard, pending = heard + pending, 0
                emit(event='step', n=n, heard_s=heard / 32000, compute_s=.01, words=english[:1], end=False)
                english = english[1:]
                if own:
                    emit(event='source', n=n, words=own[:1], final=False)
                    own = own[1:]
        elif message['op'] == 'end':
            emit(event='done', n=n, heard_s=(heard + pending) / 32000, compute_s=.01, words=english, end=False)
            emit(event='source', n=n, words=own, final=True)
    sys.exit(0)
'''


class SeamlessRuntimeTests(unittest.TestCase):
    """A whole translate session with the Seamless engine, its server replaced by a scripted fake."""
    harness = test_translate.TranslateRuntimeTests
    run_key, wait_for, status, listen, spoken = (harness.run_key, harness.wait_for, harness.status, harness.listen,
                                                 harness.spoken)
    subtitles, notified, record_notifications = harness.subtitles, harness.notified, harness.record_notifications

    def setUp(self):
        self.harness.setUp(self)
        self.home = self.root/'seamless'
        (self.home/'venv/bin').mkdir(parents=True)
        (self.home/'models').mkdir()
        for name in seamless.WEIGHTS:
            (self.home/'models'/name).touch()
        wrapper = self.commands/'python3'
        wrapper.write_text(wrapper.read_text().replace('os.execv(', FAKE_SERVER + 'os.execv(', 1))
        (self.home/'venv/bin/python').symlink_to(wrapper)
        self.env.update(TALKEY_TRANSLATE_ENGINE='seamless', TALKEY_SEAMLESS_HOME=str(self.home),
                        TALKEY_TRANSLATE_TRACE='1')

    def tearDown(self):
        self.harness.tearDown(self)

    def speak(self, english, source):
        (self.root/'seamless-english').write_text(english + '\n')
        (self.root/'seamless-source').write_text(source + '\n')
        self.mute.unlink()
        self.wait_for(lambda: self.status()['state'] == 'hearing')

    def events(self):
        trace = self.state/'translate.current/trace.jsonl'
        return [json.loads(line) for line in trace.read_text().splitlines()] if trace.exists() else []

    def test_english_streams_into_the_microphone_and_the_window(self):
        self.listen()
        self.assertEqual(self.status()['engine'], 'seamless')
        self.speak('Good morning all of you.', 'Goedemorgen allemaal samen.')
        # The first words go out while the phrase is still heard.
        self.wait_for(lambda: self.spoken().startswith(b'Good'))
        self.assertEqual(self.status()['state'], 'hearing')
        self.mute.touch()
        self.wait_for(lambda: self.subtitles() and self.subtitles()[-1]['done'])
        self.wait_for(lambda: self.spoken() == b''.join(piece['text'].encode() for piece in self.subtitles()[-1]['pieces']))
        line = self.subtitles()[-1]
        self.assertEqual((line['english'], line['dutch'], line['live']),
                         ('Good morning all of you.', 'Goedemorgen allemaal samen.', False))
        self.assertGreater(len(line['pieces']), 1)
        self.wait_for(lambda: '"event": "spoken"' in json.dumps(self.events()))
        names = [event['event'] for event in self.events()]
        for name in ('speech_start', 'step', 'source', 'agree', 'queue', 'play', 'speech_end', 'closed', 'final'):
            self.assertIn(name, names)
        for name in ('preview', 'draft', 'final_asr'):
            self.assertNotIn(name, names)
        # The audio the model had heard, timed like the rest of the trace from the first word.
        step = next(event for event in self.events() if event['event'] == 'step')
        self.assertGreater(step['heard_to'], 0)
        self.assertIs(step['eot'], False)

    def test_a_server_that_dies_stops_the_session_without_another_engine(self):
        self.listen()
        self.speak('one two three four five six seven eight', 'een twee drie vier vijf zes zeven acht')
        self.wait_for(lambda: self.spoken())
        (self.root/'seamless-die').touch()
        self.wait_for(lambda: self.status()['state'] == 'error')
        self.assertFalse(self.status()['enabled'])
        spoken = self.spoken()
        self.mute.touch()
        time.sleep(.5)
        self.assertEqual(self.spoken(), spoken)
        self.assertNotIn(b'eight', spoken)
        # Whisper and the translator were never asked.
        self.assertFalse((self.root/'prompts').exists())
        self.assertFalse({'preview', 'draft', 'final_asr'} & {event['event'] for event in self.events()})

    def test_an_error_at_start_is_the_reason_in_the_notification(self):
        self.record_notifications()
        (self.root/'seamless-fail-start').touch()
        self.run_key('translate', 'start')
        self.wait_for(lambda: self.status()['state'] == 'error')
        self.assertEqual(self.status()['engine'], 'seamless')
        self.wait_for(lambda: 'translate stopped: not enough GPU memory for Seamless: 100 of 8000 MiB free'
                      in self.notified())

    def fake_systemctl(self, active=True):
        fake = self.commands/'systemctl'
        fake.write_text(f'''#!{sys.executable}
import json, os, pathlib, sys
root = pathlib.Path(os.environ['TEST_ROOT'])
args = sys.argv[1:]
with (root/'systemctl').open('a') as f: f.write(json.dumps(args) + '\\n')
unit = root/'whisper-active'
if args[1] == 'is-active': sys.exit(0 if unit.exists() else 3)
if args[1] == 'stop': unit.unlink(missing_ok=True)
if args[1] == 'start': unit.touch()
loading = root/'seamless-loading'
if args[1] == 'start' and loading.exists():
    try:
        state = pathlib.Path(f'/proc/{{loading.read_text()}}/stat').read_text().rsplit(') ', 1)[1].split()[0]
    except FileNotFoundError:
        state = 'gone'
    (root/'server-when-whisper-started').write_text('gone' if state in ('gone', 'Z') else 'running')
''')
        fake.chmod(0o755)
        if active:
            (self.root/'whisper-active').touch()
        # The speech server answers only while its unit runs.
        curl = self.commands/'curl'
        curl.write_text(curl.read_text().replace("if '-o' in args: sys.exit(0)",
                                                 "if '-o' in args: sys.exit(0 if (root/'whisper-active').exists() else 7)"))

    def systemctl(self):
        path = self.root/'systemctl'
        calls = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
        self.assertTrue(all(call[0] == '--user' and call[-1] == 'talkey-whisper.service' for call in calls))
        return [call[1] for call in calls]

    def unloads(self):
        """The Ollama models talkey asked to unload, with the endpoint it asked."""
        path = self.root/'curl-args'
        calls = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
        return [(call[call.index('-d') - 1], json.loads(call[call.index('-d') + 1])) for call in calls
                if '-d' in call and 'keep_alive' in json.loads(call[call.index('-d') + 1])]

    def test_whisper_pauses_only_when_asked_and_comes_back_when_translate_stops(self):
        self.fake_systemctl()
        self.listen()
        self.run_key('translate', 'stop')
        self.assertEqual(self.systemctl(), [])
        self.assertEqual(self.unloads(), [])
        self.env['TALKEY_SEAMLESS_PAUSE_WHISPER'] = '1'
        self.record_notifications()
        self.listen()
        self.assertEqual(self.systemctl(), ['is-active', 'stop'])
        self.assertEqual(self.unloads(), [
            ('http://localhost:11434/api/generate', {'model': 'qwen2.5:3b', 'keep_alive': 0}),
            ('http://127.0.0.1:11434/api/chat', {'model': 'translategemma:4b', 'messages': [], 'keep_alive': 0})])
        self.assertTrue((self.state/'whisper.paused').exists())
        self.assertIn('translate starting - dictation is paused until you stop translating', self.notified())
        self.run_key('dictate', 'start', check=False)
        self.assertEqual(self.notified()[-1], 'dictation is paused while Seamless translates')
        self.run_key('translate', 'stop')
        self.assertEqual(self.systemctl(), ['is-active', 'stop', 'start'])
        self.assertFalse((self.state/'whisper.paused').exists())
        self.assertEqual(self.notified()[-1], 'translate off')

    def test_whisper_is_not_paused_under_a_dictation(self):
        self.fake_systemctl()
        self.env['TALKEY_SEAMLESS_PAUSE_WHISPER'] = '1'
        self.record_notifications()
        self.run_key('dictate', 'start')
        self.assertNotEqual(self.run_key('translate', 'start', check=False).returncode, 0)
        self.assertEqual(self.notified()[-1], 'translate: dictation is still running - try again when it is done')
        self.assertEqual(self.systemctl(), [])
        self.assertFalse(self.status()['enabled'])
        self.run_key('dictate', 'stop')
        self.listen()
        self.assertEqual(self.systemctl(), ['is-active', 'stop'])

    def test_whisper_comes_back_when_seamless_fails(self):
        self.fake_systemctl()
        self.env['TALKEY_SEAMLESS_PAUSE_WHISPER'] = '1'
        (self.root/'seamless-fail-start').touch()
        self.run_key('translate', 'start')
        self.wait_for(lambda: self.status()['state'] == 'error')
        self.wait_for(lambda: not (self.state/'whisper.paused').exists())
        self.assertEqual(self.systemctl(), ['is-active', 'stop', 'start'])
        self.assertTrue((self.root/'whisper-active').exists())

    def test_a_stop_while_seamless_loads_ends_it_before_whisper_starts(self):
        self.fake_systemctl()
        self.env['TALKEY_SEAMLESS_PAUSE_WHISPER'] = '1'
        (self.root/'seamless-slow-load').touch()
        self.run_key('translate', 'start')
        self.wait_for(lambda: (self.root/'seamless-loading').exists())
        self.run_key('translate', 'stop')
        self.assertEqual(self.systemctl(), ['is-active', 'stop', 'start'])
        self.assertEqual((self.root/'server-when-whisper-started').read_text(), 'gone')

    def test_the_next_dictation_starts_whisper_that_a_killed_session_left_paused(self):
        self.fake_systemctl(active=False)
        self.status()
        (self.state/'whisper.paused').touch()
        self.run_key('dictate', 'start')
        self.assertEqual(self.systemctl(), ['start'])
        self.assertFalse((self.state/'whisper.paused').exists())
        self.run_key('dictate', 'stop')

    def test_the_check_names_what_is_missing(self):
        self.record_notifications()

        def refused(reason):
            self.assertNotEqual(self.run_key('translate', 'start', check=False).returncode, 0)
            self.assertFalse(self.status()['enabled'])
            self.assertIn(reason, self.notified()[-1])
            # A setting names itself; anything missing names the step that installs it.
            self.assertTrue(self.notified()[-1].endswith('run: talkey-setup seamless') or 'TALKEY_' in reason)

        self.env['TALKEY_SEAMLESS_PAUSE_WHISPER'] = 'yes'
        refused('TALKEY_SEAMLESS_PAUSE_WHISPER must be 0 or 1')
        del self.env['TALKEY_SEAMLESS_PAUSE_WHISPER']
        self.env['TALKEY_TRANSLATE_FROM'] = 'auto'
        refused('needs TALKEY_TRANSLATE_FROM set to one of: nl, de, fr, es, it, pt')
        del self.env['TALKEY_TRANSLATE_FROM']
        (self.home/'models/tokenizer.model').unlink()
        refused('Seamless weights missing in')
        (self.home/'models/tokenizer.model').touch()
        (self.root/'seamless-broken').touch()
        refused('; run: talkey-setup seamless')
        self.env['TALKEY_SEAMLESS_HOME'] = str(self.root/'nowhere')
        refused(f'no Seamless environment at {self.root}/nowhere')
        self.assertFalse((self.root/'devices').exists())


if __name__ == '__main__':
    unittest.main()
