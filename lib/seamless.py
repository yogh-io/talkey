"""The SeamlessStreaming translate engine: the worker's side of its server, whole words from the
model's tokens, and where its files are.

Standard library only: the worker and the server, each in its own Python, import it.
"""
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import time

# talkey's source languages, as Seamless names them.
CODES = {'nl': 'nld', 'de': 'deu', 'fr': 'fra', 'es': 'spa', 'it': 'ita', 'pt': 'por'}
WEIGHTS = ('seamless_streaming_unity.pt', 'seamless_streaming_monotonic_decoder.pt',
           'spm_char_lang38_tc.model', 'tokenizer.model')
# The audio the server gathers before it steps the model again.
STEP_MS = 320
SETUP = 'run: talkey-setup seamless'


class Words:
    """Whole words from subword tokens. A word is complete when the next token starts a new word,
    when the model's own next token is the end of the text, or when the phrase ends."""
    MARK = '▁'

    def __init__(self):
        self.open = ''

    def feed(self, tokens, end=False):
        done = []
        for token in tokens:
            if token.startswith(self.MARK) and self.open:
                done.append(self.open)
                self.open = ''
            self.open += token.lstrip(self.MARK)
        if end and self.open:
            done.append(self.open)
            self.open = ''
        return done


def home():
    """Holds venv/, the Seamless Python, and models/, its weights."""
    data = Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share'))
    return Path(os.environ.get('TALKEY_SEAMLESS_HOME') or data / 'talkey/seamless')


def server():
    """The server sits beside the worker, as talkey's programs find each other."""
    return Path(sys.argv[0]).resolve().with_name('talkey-seamless-server')


def check():
    """The settings and files the engine needs, without loading any of it."""
    if os.environ.get('TALKEY_SEAMLESS_PAUSE_WHISPER', '0') not in ('0', '1'):
        raise ValueError('TALKEY_SEAMLESS_PAUSE_WHISPER must be 0 or 1')
    language = os.environ.get('TALKEY_TRANSLATE_FROM', 'nl')
    if language not in CODES:
        raise ValueError('the Seamless engine needs TALKEY_TRANSLATE_FROM set to one of: ' + ', '.join(CODES))
    python = home() / 'venv/bin/python'
    if not python.is_file():
        raise ValueError(f'no Seamless environment at {home()}; {SETUP}')
    missing = [name for name in WEIGHTS if not (home() / 'models' / name).is_file()]
    if missing:
        raise ValueError(f'Seamless weights missing in {home() / "models"}: {", ".join(missing)}; {SETUP}')
    result = subprocess.run([str(python), str(server()), '--check'], capture_output=True, text=True)
    if result.returncode:
        reason = (result.stderr.strip().splitlines() or [f'{python} failed'])[-1]
        raise ValueError(f'{reason}; {SETUP}')


class SeamlessEngine:
    """SeamlessStreaming hears the phrase while it is spoken and writes its English as it goes, in a
    server process of its own. Each whole English word goes to the voice as soon as the voice's
    timing lets it; the words of the speaker's own language are the subtitles."""

    def __init__(self, out):
        self.out, self.process, self.closing = out, None, False
        # Per phrase number: the phrase, the frames sent, the English not yet spoken, whether the
        # model's next token ended the text, the source words, whether any English went out, and
        # when the latest step came.
        self.phrases = {}
        out.cycle.append(STEP_MS / 1000)

    @classmethod
    def check(cls):
        check()

    def start(self):
        self.process = subprocess.Popen([str(home() / 'venv/bin/python'), str(server())], stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, text=True, encoding='utf-8')
        line = self.process.stdout.readline()
        event = json.loads(line) if line else {'event': 'error', 'message': 'the Seamless engine stopped while loading'}
        if event['event'] != 'ready':
            raise RuntimeError(event['message'])
        print(f"talkey translate: Seamless loaded in {event['load_s']} s, holds {event['gpu_mb']} MiB of GPU "
              f"memory, and takes {event['step_s']} s for the longest phrase", file=sys.stderr)
        self.out.guard(self.listen)

    def begin(self, phrase):
        self.phrases[phrase.number] = {'phrase': phrase, 'sent': 0, 'pending': [], 'end': False, 'source': [],
                                       'queued': False, 'stepped': None}
        self.send(op='start', n=phrase.number)
        self.hear(phrase)

    def hear(self, phrase):
        state = self.phrases[phrase.number]
        pcm = b''.join(phrase.frames[state['sent']:])
        state['sent'] = len(phrase.frames)
        if pcm:
            self.send(op='audio', n=phrase.number, pcm=base64.b64encode(pcm).decode())

    def end(self, phrase):
        self.hear(phrase)
        self.send(op='end', n=phrase.number)

    def close(self):
        self.closing = True
        if self.process:
            try:
                self.process.stdin.close()
            except OSError:
                pass
            # Its GPU memory is free once it has gone.
            self.process.terminate()
            try:
                self.process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()

    def send(self, **message):
        try:
            self.process.stdin.write(json.dumps(message) + '\n')
            self.process.stdin.flush()
        except (OSError, ValueError) as error:
            raise RuntimeError('the Seamless engine stopped') from error

    def listen(self):
        for line in self.process.stdout:
            self.event(json.loads(line))
        if not self.closing:
            raise RuntimeError('the Seamless engine stopped')

    def event(self, event):
        if event['event'] == 'error':
            raise RuntimeError(event['message'])
        state = self.phrases[event['n']]
        phrase, words = state['phrase'], event['words']
        if event['event'] == 'step':
            now = time.monotonic()
            if state['stepped'] is not None:
                self.out.cycle.append(now - state['stepped'])
            state['stepped'] = now
            state['pending'] += words
            state['end'] = event['end']
            # eot: the model's next token is the end of the text.
            self.out.trace(phrase, 'step', heard_to=phrase.audio_from + event['heard_s'], compute=event['compute_s'],
                           text=' '.join(words), eot=event['end'])
            if state['pending']:
                self.offer(state)
        elif event['event'] == 'done':
            state['pending'] += words
            self.out.trace(phrase, 'closed')
            self.out.trace(phrase, 'final', text=' '.join(words), compute=event['compute_s'])
            if state['pending']:
                self.out.queue(phrase, ' '.join(state['pending']), last=True)
                state['queued'] = True
            (self.out.finish if state['queued'] else self.out.drop)(phrase)
        elif event['event'] == 'source':
            state['source'] += words
            self.out.trace(phrase, 'source', text=' '.join(words))
            self.out.heard(phrase, ' '.join(state['source']), live=not event['final'])
            if event['final']:
                del self.phrases[event['n']]

    def offer(self, state):
        """The English not yet spoken goes to the voice, or waits while the voice says it should."""
        phrase, text = state['phrase'], ' '.join(state['pending'])
        wait, voice_left, cover, refill = self.out.gate(phrase, text)
        self.out.trace(phrase, 'agree', result=wait or 'spoken', piece=text, voice_left=round(voice_left, 3),
                       eot=state['end'])
        if wait is None:
            state['pending'], state['queued'] = [], True
            self.out.queue(phrase, text, ended=state['end'], cover=round(cover, 3), refill=round(refill, 3))
