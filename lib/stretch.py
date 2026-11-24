"""Change the pace of speech without changing its pitch, as it streams (WSOLA)."""
import numpy as np


class Stretch:
    """Streams 16-bit mono PCM through at another speed; flush() ends an utterance.

    Each output window is the input window near its nominal place that best continues the
    previous one, overlap-added. Below 1 speech gets slower: windows overlap more in the input.
    The speed may change between pushes; the nominal place moves on by the speed of each window.
    """
    WINDOW_S = 0.03

    def __init__(self, speed, rate):
        self.speed = speed
        self.size = round(rate * self.WINDOW_S) // 2 * 2
        self.hop = self.size // 2
        self.slack = self.hop // 2
        self.window = np.hanning(self.size + 1)[:-1]
        self.reset()

    def reset(self):
        self.input = np.zeros(0)
        self.base = 0
        self.position = 0.0
        self.last = None
        self.tail = None

    def idle(self):
        return self.last is None and not len(self.input)

    def push(self, pcm):
        if self.speed == 1 and self.idle():
            return pcm
        self.input = np.concatenate([self.input, np.frombuffer(pcm, np.int16)])
        out = []
        while (x := self.place()) is not None:
            frame = self.input[x - self.base:x - self.base + self.size]
            if self.tail is None:
                out.append(frame[:self.hop])
            else:
                out.append(self.tail + frame[:self.hop] * self.window[:self.hop])
            self.tail = frame[self.hop:] * self.window[self.hop:]
            self.last, self.position = x, self.position + self.hop * self.speed
            keep = min(round(self.position) - self.slack, x + self.hop)
            self.input, self.base = self.input[keep - self.base:], keep
        return self.pcm(out)

    def flush(self):
        """The rest of the utterance, unstretched from where the last window left off."""
        if self.idle():
            return b''
        start = 0 if self.last is None else self.last + self.hop - self.base
        rest = self.input[start:]
        self.reset()
        return self.pcm([rest])

    def place(self):
        """Input position of the next window, or None until enough input has arrived."""
        end = self.base + len(self.input)
        if self.last is None:
            return 0 if end >= self.size else None
        low = max(round(self.position) - self.slack, self.base)
        high = round(self.position) + self.slack
        ref = self.last + self.hop
        if max(high, ref) + self.size > end:
            return None
        follow = self.input[ref - self.base:ref - self.base + self.size]
        span = self.input[low - self.base:high - self.base + self.size]
        match = np.correlate(span, follow, 'valid')
        energy = np.convolve(span * span, np.ones(self.size), 'valid')
        return low + int(np.argmax(match / np.sqrt(energy + 1e-9)))

    @staticmethod
    def pcm(parts):
        if not parts:
            return b''
        return np.clip(np.rint(np.concatenate(parts)), -32768, 32767).astype(np.int16).tobytes()
