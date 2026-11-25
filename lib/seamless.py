"""The SeamlessStreaming translate engine: whole words from its tokens, and where its files are.

Standard library only: the worker and the server, each in its own Python, import it.
"""
import os
from pathlib import Path
import subprocess
import sys

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
