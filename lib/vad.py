"""Silero VAD through whisper.cpp and audio-time phrase endpoints."""
from array import array
from collections import deque
import ctypes as C
import ctypes.util
import math
import os
from pathlib import Path
import sys

FRAME_SAMPLES = 512
FRAME_MS = 32
FRAME_BYTES = FRAME_SAMPLES * 2


def setting(name, default, minimum, maximum):
    value = float(os.environ.get('TALKEY_' + name, default))
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(f'TALKEY_{name} must be between {minimum} and {maximum}')
    return value


class VadParams(C.Structure):
    _fields_ = [('n_threads', C.c_int), ('use_gpu', C.c_bool), ('gpu_device', C.c_int)]


class Silero:
    """Use whisper.cpp's C API; older releases use a rolling context window."""
    def __init__(self):
        home = Path.home()
        opt = Path(os.environ.get('WHISPER_OPT', home / '.local/opt/whisper.cpp'))
        override = os.environ.get('TALKEY_WHISPER_LIB')
        local = opt / 'build/bin/libwhisper.so'
        library = override or (str(local) if local.exists() else C.util.find_library('whisper'))
        if not library:
            raise ValueError('libwhisper not found; set TALKEY_WHISPER_LIB to libwhisper.so')
        model = os.environ.get('TALKEY_VAD_MODEL')
        if not model:
            for directory in (Path(os.environ.get('WHISPER_MODEL_DIR', home / '.local/share/whisper')),
                              opt / 'models', Path('/usr/share/whisper.cpp-model-silero-v5.1.2')):
                candidate = directory / 'ggml-silero-v5.1.2.bin'
                if candidate.is_file():
                    model = str(candidate)
                    break
        if not model or not Path(model).is_file():
            raise ValueError('Silero model missing; run talkey-setup vad for download instructions')
        self.lib = C.CDLL(library)
        lib = self.lib
        # VAD otherwise logs several INFO lines for every 32 ms audio frame.
        self.log_callback = C.CFUNCTYPE(None, C.c_int, C.c_char_p, C.c_void_p)(
            lambda level, message, _: print(message.decode(errors='replace'), end='', file=sys.stderr)
            if level >= 3 else None)
        lib.whisper_log_set.argtypes = [type(self.log_callback), C.c_void_p]
        lib.whisper_log_set(self.log_callback, None)
        try:
            lib.whisper_vad_default_context_params.restype = VadParams
            lib.whisper_vad_init_from_file_with_params.argtypes = [C.c_char_p, VadParams]
            lib.whisper_vad_init_from_file_with_params.restype = C.c_void_p
            lib.whisper_vad_n_probs.argtypes = [C.c_void_p]
            lib.whisper_vad_n_probs.restype = C.c_int
            lib.whisper_vad_probs.argtypes = [C.c_void_p]
            lib.whisper_vad_probs.restype = C.POINTER(C.c_float)
            lib.whisper_vad_free.argtypes = [C.c_void_p]
            lib.whisper_vad_free.restype = None
            self.streaming = hasattr(lib, 'whisper_vad_detect_speech_no_reset')
            self.detect = (lib.whisper_vad_detect_speech_no_reset if self.streaming
                           else lib.whisper_vad_detect_speech)
            self.detect.argtypes = [C.c_void_p, C.POINTER(C.c_float), C.c_int]
            self.detect.restype = C.c_bool
        except AttributeError as error:
            raise ValueError('whisper.cpp library lacks VAD support; upgrade whisper.cpp') from error
        params = lib.whisper_vad_default_context_params()
        params.n_threads, params.use_gpu = 1, False
        self.context = lib.whisper_vad_init_from_file_with_params(os.fsencode(model), params)
        if not self.context:
            raise ValueError(f'could not load VAD model: {model}')
        if self.streaming:
            lib.whisper_vad_reset_state.argtypes = [C.c_void_p]
            lib.whisper_vad_reset_state.restype = None
            lib.whisper_vad_reset_state(self.context)
        self.history = deque(maxlen=32)
        self.threshold = setting('VAD_THRESHOLD', '0.5', 0.01, 0.99)

    def speech(self, frame):
        samples = array('h', frame)
        if sys.byteorder != 'little':
            samples.byteswap()
        self.history.append(samples)
        values = samples if self.streaming else [v for chunk in self.history for v in chunk]
        audio = (C.c_float * len(values))(*(v / 32768.0 for v in values))
        if not self.detect(self.context, audio, len(values)):
            raise RuntimeError('VAD inference failed')
        count = self.lib.whisper_vad_n_probs(self.context)
        if count < 1:
            raise RuntimeError('VAD returned no probabilities')
        return self.lib.whisper_vad_probs(self.context)[count - 1] >= self.threshold

    def close(self):
        self.lib.whisper_vad_free(self.context)


class Endpoint:
    """Debounce speech onset and tolerate pauses; times use audio, not inference."""
    def __init__(self, start_ms=160, silence_ms=1200, max_ms=900000, wait_ms=None):
        self.start_frames = math.ceil(start_ms / FRAME_MS)
        self.silence_frames = math.ceil(silence_ms / FRAME_MS)
        self.max_frames = math.ceil(max_ms / FRAME_MS)
        self.wait_frames = math.ceil(wait_ms / FRAME_MS) if wait_ms is not None else self.silence_frames
        self.reset()

    def reset(self):
        self.active = False
        self.voiced = self.quiet = self.length = 0

    def update(self, speech):
        if not self.active:
            self.voiced = self.voiced + 1 if speech else 0
            if self.voiced >= self.start_frames:
                self.active = True
                self.length = self.voiced
                return 'start'
        else:
            self.length += 1
            self.quiet = 0 if speech else self.quiet + 1
            if self.quiet >= self.wait_frames or self.length >= self.max_frames:
                self.reset()
                return 'end'
            if self.quiet == self.silence_frames:
                return 'pause'
        return None
