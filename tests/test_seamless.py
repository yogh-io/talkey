"""The Seamless engine without the model: word completion and the server's loop, with fakes."""
import base64
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import queue
import sys
import threading
import time
import unittest

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

    def test_the_cards_point_every_model_file_at_the_local_copy(self):
        card = server.card(Path('/data/seamless/models'))
        uris = sorted(line.split(': ', 1)[1] for line in card.splitlines() if 'file://' in line)
        self.assertEqual(uris, sorted(f'"file:///data/seamless/models/{name}"' for name in seamless.WEIGHTS))
        self.assertIn('tokenizer: "file:///data/seamless/models/tokenizer.model"', card)
        self.assertEqual([line for line in card.splitlines() if line.startswith('name:')],
                         ['name: seamless_streaming_unity@user', 'name: seamless_streaming_monotonic_decoder@user'])


if __name__ == '__main__':
    unittest.main()
