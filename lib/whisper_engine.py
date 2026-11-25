"""The default translate engine. Whisper hears the phrase while it is spoken, TranslateGemma drafts
its English, and the English that later drafts keep goes to the voice before the phrase ends.
"""
from collections import deque
from concurrent.futures import ThreadPoolExecutor
import difflib
import json
import math
import os
from pathlib import Path
import queue
import re
import sys
import time
import urllib.parse
import urllib.request

from speech import transcribe_words
from vad import FRAME_MS

LANGUAGES = {'nl': 'Dutch', 'de': 'German', 'fr': 'French', 'es': 'Spanish', 'it': 'Italian', 'pt': 'Portuguese'}
# Whisper's stock captions for near-silent Dutch and English clips.
HALLUCINATION = re.compile(r'amara\.org|ondertitel|bedankt voor het kijken|thanks for watching', re.I)


def bare(word):
    return re.sub(r'\W', '', word.lower())


def resume(said, words):
    """Index in words just past the Dutch already translated; whisper may word it slightly differently."""
    if not said:
        return 0
    matcher = difflib.SequenceMatcher(None, [bare(w) for w in said], [bare(w) for w in words], autojunk=False)
    blocks = [block for block in matcher.get_matching_blocks() if block.size]
    if not blocks:
        return min(len(said), len(words))
    a, b, size = blocks[-1]
    return min(len(words), b + size + len(said) - (a + size))


def words_of(tokens):
    return [b for b in (bare(t) for t in tokens) if b]


def unfinished(english):
    """English that is still going on: a sentence end there makes the model stop continuing."""
    return re.sub(r'[\s.!?…]+$', '', english).strip()


def closes(token):
    """A next token that ends the sentence: end of text, a new line, or a closing mark."""
    if not token or '\n' in token or token.startswith('<'):
        return True
    return token.strip()[:1] in ('.', '?', '!')


class Agreement:
    """Which English of a phrase is settled enough to speak before the phrase ends.

    Each draft continues the English already spoken, for all the trusted Dutch so far. English is
    settled in two ways. Its first words are the same as in the latest draft made from less of the
    same Dutch: hearing more did not change them. Or the model says a sentence may end after this
    Dutch: then the whole draft stands, while whisper keeps hearing that Dutch. A guessed verb
    changes when the real one is heard, so it is not settled.
    """
    def __init__(self, end_p=1.0):
        self.end_p = end_p
        self.said = []
        self.spoken = ''
        # Earlier drafts as (Dutch words, English words), and the latest whole draft.
        self.drafts = []
        self.whole = None
        # What offer found settled, until it is spoken or the next offer replaces it.
        self.settled = None
        # What the last offer decided and the piece it was about, for the trace.
        self.decision = None

    def hold(self, reason, piece=None):
        self.decision = {'result': reason, 'piece': piece}

    def offer(self, words, english, ends=0.0, joined=False):
        """Record a draft for the trusted Dutch words; return the English settled so far, or None.

        ends: the model's chance that a sentence ends after these words. joined: the draft carries
        on inside the last spoken word, as "'ll" after "I".
        """
        start = resume(self.said, words)
        dutch, written = words[start:], english.split()
        heard, drafted = words_of(dutch), words_of(written)
        if ends >= self.end_p and drafted:
            self.whole = (dutch, written, joined)
        elif self.whole and heard[:len(words_of(self.whole[0]))] != words_of(self.whole[0]):
            self.whole = None
        found, reason = [], 'dutch-changed' if self.drafts else 'first-draft'
        for before, kept in reversed(self.drafts):
            if len(words_of(before)) < len(heard) and heard[:len(words_of(before))] == words_of(before):
                count = 0
                while count < min(len(kept), len(drafted)) and kept[count] == drafted[count]:
                    count += 1
                if count:
                    found.append((count, len(before), self.take(written, count), joined, 'agreed'))
                else:
                    reason = 'no-agreement'
                break
        if self.drafts and words_of(self.drafts[-1][0]) == heard:
            self.drafts.pop()
        self.drafts.append((dutch, drafted))
        if self.whole:
            before, piece, attached = self.whole
            found.append((len(words_of(piece)), len(before), piece, attached, 'end'))
        if not found:
            self.settled = None
            return self.hold(reason)
        count, said, piece, attached, via = max(found, key=lambda option: (option[0], option[1]))
        text = re.sub(r'^[\s.,;:!?…]+', '', ' '.join(piece))
        self.settled = (text, attached, words[:start + said], ' '.join(words[start:start + said]), via)
        self.hold('settled', text)
        return text

    @staticmethod
    def take(tokens, count):
        """The first count words, as the draft writes them, so its punctuation stays."""
        piece, seen = [], 0
        for token in tokens:
            if seen == count:
                break
            piece.append(token)
            seen += 1 if bare(token) else 0
        return piece

    def commit(self):
        """The settled English is being spoken; return it, whether it joins the last word, and
        whether the model said its sentence may end there."""
        text, joined, self.said, dutch, via = self.settled
        self.spoken = self.spoken + text if joined else f'{self.spoken} {text}'.strip()
        self.drafts, self.whole, self.settled = [], None, None
        self.decision = {'result': 'spoken', 'piece': text, 'dutch': dutch, 'via': via}
        return text, joined, via == 'end'


def preview(pcm, url, language, prompt):
    """Transcript of the phrase so far, its words with whisper's probabilities, and its chance of no speech."""
    return transcribe_words(pcm, url, language, prompt, 16000, 1)


def settled(words, earlier, least):
    """The words, from the start, that the latest transcripts agree on.

    Whisper splits its probability between spellings of the same word, such as "Er" and "er" or
    "ja" and "ja,", so a low one does not make a word wrong; it only asks for more agreement. A word
    it is sure of is trusted when the previous transcript had it in the same place, another one
    when the two before did too.
    """
    heard, trusted = [bare(word) for word, _ in words], []
    for i, (word, probability) in enumerate(words):
        agreeing = 0
        for before in reversed(earlier):
            if before[:i + 1] != heard[:i + 1]:
                break
            agreeing += 1
        if agreeing < (1 if probability >= least else 2):
            break
        trusted.append((word, probability))
    return trusted


class Translator:
    """Ollama chat in TranslateGemma's own prompt format; recent phrases and the conversation context
    the user or another program wrote are context."""
    def __init__(self):
        self.url = os.environ.get('TALKEY_TRANSLATE_URL', 'http://127.0.0.1:11434/api/chat')
        self.generate_url = urllib.parse.urljoin(self.url, '/api/generate')
        self.model = os.environ.get('TALKEY_TRANSLATE_LLM', 'translategemma:4b')
        self.options = {'temperature': 0, 'num_ctx': 2048}
        layers = os.environ.get('TALKEY_TRANSLATE_GPU_LAYERS')
        if layers:
            self.options['num_gpu'] = int(layers)
        code = os.environ.get('TALKEY_TRANSLATE_FROM', 'nl')
        name = LANGUAGES.get(code, 'source')
        # A free-form interpreter prompt read idioms like "dat valt wel mee" the wrong way round.
        self.prompt = (f'You are a professional {name} ({code}) to English (en) translator. Your goal is to '
                       f'accurately convey the meaning and nuances of the original {name} text while adhering '
                       'to English grammar, vocabulary, and cultural sensitivities. Produce only the English '
                       'translation, without any additional explanations or commentary. Please translate the '
                       f'following {name} text into English:\n\n\n')
        self.history = deque(maxlen=3)
        config = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config'))
        self.files = [Path(os.environ.get('TALKEY_TRANSLATE_NOTES', config / 'talkey/translate-notes.txt'))]
        if os.environ.get('TALKEY_STATE_DIR'):
            self.files.append(Path(os.environ['TALKEY_STATE_DIR']) / 'translate.context')
        self.read = {}

    def context(self):
        """The notes the user wrote and the live context any program may write, read again only when
        changed. The newest part is kept, up to about a quarter of the model's context."""
        parts = []
        for path in self.files:
            try:
                stamp = path.stat().st_mtime_ns
            except OSError:
                continue
            if self.read.get(path, (None,))[0] != stamp:
                self.read[path] = (stamp, path.read_text(errors='replace').strip())
            parts.append(self.read[path][1])
        return '\n'.join(part for part in parts if part)[-self.options['num_ctx']:]

    def __call__(self, text, spoken='', context=''):
        """English for text; with spoken, only what follows the English already spoken.

        Also says whether that carries on inside the last spoken word, as "'ll" after "I".
        """
        messages = []
        for source, english in self.history:
            messages += [{'role': 'user', 'content': self.prompt + source}, {'role': 'assistant', 'content': english}]
        prompt = self.prompt
        if context:
            prompt = prompt.replace('Please translate', 'Context of the conversation, for reference only, do not '
                                    f'translate it:\n{context}\n\nPlease translate')
        messages.append({'role': 'user', 'content': prompt + text})
        spoken = unfinished(spoken)
        if spoken:
            messages.append({'role': 'assistant', 'content': spoken})
        reply = self.post(self.url, {'model': self.model, 'messages': messages, 'stream': False, 'think': False,
                                     'keep_alive': '30m', 'options': self.options})['message']['content']
        reply = re.sub(r'\s+', ' ', reply)
        english = re.sub(r'^[\s.,;:!?…]+', '', reply.strip().strip('"“”'))
        return english, bool(spoken and english) and reply[:1] == english[:1]

    def ends(self, text):
        """The model's chance that a sentence ends right after text, read from its next token."""
        context = ' '.join(source for source, _ in self.history)
        prompt = (context + ' ' + re.sub(r'[\s.,;:!?…]+$', '', text)).strip()
        reply = self.post(self.generate_url, {
            'model': self.model, 'prompt': prompt, 'raw': True, 'stream': False, 'keep_alive': '30m',
            'logprobs': True, 'top_logprobs': 20, 'options': {**self.options, 'num_predict': 1}})
        if not reply.get('logprobs'):
            raise RuntimeError('Ollama returned no token probabilities; update Ollama')
        return sum(math.exp(top['logprob']) for top in reply['logprobs'][0]['top_logprobs'] if closes(top['token']))

    @staticmethod
    def post(url, body):
        request = urllib.request.Request(url, data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)

    def remember(self, text, english):
        """A finished phrase becomes context for the next ones."""
        if english:
            self.history.append((text, english))


class WhisperEngine:
    """Whisper hears the phrase while it is spoken, TranslateGemma drafts its English, and the English
    later drafts keep goes to the voice before the phrase ends."""
    # How often the phrase so far is transcribed and translated.
    PREVIEW_S = 0.25
    # How sure the model must be that a sentence can end after the Dutch so far, to speak its whole draft.
    END_P = 0.2
    # Below this probability, a word must be the same in three transcripts, not two, to be translated.
    WORD_P = 0.5

    def __init__(self, out, translator=None):
        self.out, self.translate, self.trace = out, translator or Translator(), out.trace
        self.url = os.environ.get('TALKEY_WHISPER_URL', 'http://127.0.0.1:8910/inference')
        self.language = os.environ.get('TALKEY_TRANSLATE_FROM', 'nl')
        self.phrases = queue.Queue()
        self.preview_frames = math.ceil(self.PREVIEW_S * 1000 / FRAME_MS)
        self.previews = ThreadPoolExecutor(max_workers=1)
        self.previewing = None
        self.recent = deque(maxlen=2)
        out.cycle.append(self.PREVIEW_S)

    @classmethod
    def check(cls):
        """Nothing to check before it loads: whisper and Ollama are asked when it starts."""

    def start(self):
        # Also checks that Ollama gives the token probabilities the sentence-end chance needs.
        self.translate('Hallo.')
        self.translate.ends('Hallo.')
        self.out.guard(self.recognize)

    def begin(self, phrase):
        phrase.context = self.translate.context()
        phrase.previewed = len(phrase.frames)

    def hear(self, phrase):
        if (len(phrase.frames) - phrase.previewed >= self.preview_frames
                and (self.previewing is None or self.previewing.done())):
            phrase.previewed = len(phrase.frames)
            self.previewing = self.previews.submit(self.preview_phrase, phrase, b''.join(phrase.frames),
                                                   self.prompt(phrase))

    def end(self, phrase):
        self.phrases.put((phrase.audio, phrase))

    def close(self):
        self.previews.shutdown(wait=False, cancel_futures=True)

    def prompt(self, phrase):
        """Whisper's prompt: the conversation context, then the last sentences, which whisper keeps if it must cut."""
        return ' '.join(filter(None, [phrase.context.replace('\n', ' '), *self.recent])) or None

    def preview_phrase(self, phrase, pcm, prompt):
        try:
            with phrase.lock:
                if phrase.closed:
                    return
                sent = time.monotonic()
                if phrase.previewed_at is not None:
                    self.out.cycle.append(sent - phrase.previewed_at)
                phrase.previewed_at = sent
                text, words, _ = preview(pcm, self.url, self.language, prompt)
                # Only what whisper stands by is translated; the subtitles show all of it.
                trusted = settled(words, phrase.heard, self.WORD_P)
                phrase.heard.append([bare(word) for word, _ in words])
                self.trace(phrase, 'preview', audio_s=round(len(pcm) / 32000, 3), text=text,
                           heard_to=phrase.audio_from + len(pcm) / 32000, latency=round(time.monotonic() - sent, 3),
                           trusted=len(trusted), low=round(min((p for _, p in trusted), default=1), 3))
                self.out.heard(phrase, text)
                if re.search(r'\w', text):
                    self.advance(phrase, ' '.join(word for word, _ in trusted))
        except Exception as error:
            print(f'talkey translate: preview failed: {error}', file=sys.stderr)

    def advance(self, phrase, text):
        """Draft the English for the phrase so far and speak what the drafts agree on."""
        if phrase.agreement is None:
            phrase.agreement = Agreement(self.END_P)
        words, spoken = text.split(), phrase.agreement.spoken
        if not words_of(words[resume(phrase.agreement.said, words):]):
            self.trace(phrase, 'agree', result='dutch-unsettled', piece=None)
            return
        # The same Dutch after the same English gives the same draft.
        if phrase.drafted and phrase.drafted[0] == (text, spoken):
            draft, joined, ends = phrase.drafted[1]
        else:
            sent = time.monotonic()
            draft, joined = self.translate(text, spoken, phrase.context)
            ends = self.translate.ends(text)
            phrase.drafted = ((text, spoken), (draft, joined, ends))
            self.trace(phrase, 'draft', source=text, spoken=spoken, english=draft, ends=round(ends, 3),
                       latency=round(time.monotonic() - sent, 3))
        agreement = phrase.agreement
        piece = agreement.offer(words, draft, ends, joined)
        wait, voice_left, cover, refill = self.out.gate(phrase, piece)
        if piece and wait:
            agreement.hold(wait, piece)
            piece = None
        elif piece:
            piece = agreement.commit()
        self.trace(phrase, 'agree', voice_left=round(voice_left, 3), ends=round(ends, 3), **agreement.decision)
        if piece:
            self.out.queue(phrase, *piece, last=False, cover=round(cover, 3), refill=round(refill, 3))

    def recognize(self):
        while True:
            pcm, phrase = self.phrases.get()
            sent = time.monotonic()
            # The last sentences steer whisper toward words that fit what was being said.
            text, _, no_speech = transcribe_words(pcm, self.url, self.language, self.prompt(phrase), 16000, 1)
            self.trace(phrase, 'final_asr', audio_s=round(len(pcm) / 32000, 3), text=text,
                       latency=round(time.monotonic() - sent, 3), no_speech=round(no_speech, 4))
            with phrase.lock:
                phrase.closed = True
                self.trace(phrase, 'closed')
                if not re.search(r'\w', text) or HALLUCINATION.search(text):
                    self.out.drop(phrase)
                    continue
                self.recent.append(text)
                self.out.heard(phrase, text, live=False)
                words, agreement = text.split(), phrase.agreement or Agreement(self.END_P)
                if re.search(r'\w', ' '.join(words[resume(agreement.said, words):])):
                    sent = time.monotonic()
                    english, joined = self.translate(text, agreement.spoken, phrase.context)
                    self.trace(phrase, 'final_draft', source=text, spoken=agreement.spoken, english=english,
                               latency=round(time.monotonic() - sent, 3))
                    if english:
                        self.out.queue(phrase, english, joined, last=True)
                self.out.finish(phrase)
                self.translate.remember(text, phrase.english)
