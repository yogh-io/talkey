from pathlib import Path
import sys
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'lib'))

from stretch import Stretch

RATE = 24000


def voice(seconds, pitch=120.0):
    t = np.arange(int(seconds * RATE)) / RATE
    swell = 0.6 + 0.4 * np.sin(2 * np.pi * 3 * t)
    tone = sum(np.sin(2 * np.pi * pitch * k * t) / k for k in range(1, 6))
    return (swell * tone * 6000).astype(np.int16).tobytes()


def run(stretch, pcm, chunk):
    out = b''.join(stretch.push(pcm[i:i + chunk]) for i in range(0, len(pcm), chunk))
    return out + stretch.flush()


def samples(pcm):
    return np.frombuffer(pcm, np.int16).astype(float)


def peak_hz(pcm):
    a = samples(pcm)
    spectrum = np.abs(np.fft.rfft(a * np.hanning(len(a))))
    return np.fft.rfftfreq(len(a), 1 / RATE)[np.argmax(spectrum)]


class StretchTests(unittest.TestCase):
    def test_speed_one_passes_audio_through_untouched(self):
        stretch, pcm = Stretch(1, RATE), voice(0.5)
        self.assertEqual(stretch.push(pcm), pcm)
        self.assertEqual(stretch.flush(), b'')

    def test_slower_speech_lasts_longer_by_the_speed(self):
        pcm = voice(3)
        out = run(Stretch(0.95, RATE), pcm, 3840)
        self.assertAlmostEqual(len(out) / len(pcm), 1 / 0.95, delta=0.01)

    def test_pitch_stays_the_same(self):
        pcm = voice(2, pitch=200)
        self.assertAlmostEqual(peak_hz(run(Stretch(0.9, RATE), pcm, 1920)), peak_hz(pcm), delta=2)

    def test_joins_are_smooth(self):
        t = np.arange(2 * RATE) / RATE
        pcm = (np.sin(2 * np.pi * 200 * t) * 8000).astype(np.int16).tobytes()
        out = samples(run(Stretch(0.95, RATE), pcm, 1920))
        self.assertLess(np.abs(np.diff(out)).max(), np.abs(np.diff(samples(pcm))).max() * 1.2)

    def test_chunk_size_does_not_change_the_result(self):
        pcm = voice(1.5)
        self.assertEqual(run(Stretch(0.95, RATE), pcm, len(pcm)), run(Stretch(0.95, RATE), pcm, 1234))

    def test_utterance_ends_with_its_own_last_samples(self):
        pcm = voice(1.2)
        out = run(Stretch(0.95, RATE), pcm, 1920)
        self.assertEqual(out[-400:], pcm[-400:])

    def test_piece_shorter_than_a_window_is_kept_whole(self):
        stretch, pcm = Stretch(0.95, RATE), voice(0.01)
        self.assertEqual(stretch.push(pcm) + stretch.flush(), pcm)

    def test_flush_starts_the_next_utterance_afresh(self):
        stretch, first, second = Stretch(0.95, RATE), voice(0.7), voice(0.9, pitch=150)
        run(stretch, first, 1920)
        self.assertEqual(run(stretch, second, 1920), run(Stretch(0.95, RATE), second, 1920))


if __name__ == '__main__':
    unittest.main()
