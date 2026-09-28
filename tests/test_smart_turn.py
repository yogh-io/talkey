"""Optional feature extraction and semantic completion policy regression tests."""
import importlib.util
import math
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import test_auto

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'lib'))
from smart_turn import SmartTurn

try:
    import numpy as np
except ImportError:
    np = None


class TurnPolicyTests(unittest.TestCase):
    def test_twenty_second_thinking_pause_then_speech_stays_in_same_turn(self):
        endpoint = test_auto.auto.Endpoint(start_ms=32, silence_ms=1200, wait_ms=20000)
        self.assertEqual(endpoint.update(True), 'start')
        events = [endpoint.update(False) for _ in range(600)]  # 19.2 seconds
        self.assertNotIn('end', events)
        self.assertIn('pause', events)
        self.assertIsNone(endpoint.update(True))
        self.assertTrue(endpoint.active)
        for _ in range(624):
            self.assertNotEqual(endpoint.update(False), 'end')
        self.assertEqual(endpoint.update(False), 'end')

    def test_invalid_wait_bounds_rejected_before_loading_model(self):
        with patch.dict(os.environ, TALKEY_SMART_TURN='1', TALKEY_TURN_MAX_SILENCE_MS='1000',
                        TALKEY_TURN_MIN_SILENCE_MS='2000'):
            with self.assertRaisesRegex(ValueError, 'TURN_MAX_SILENCE_MS'):
                test_auto.auto.load_turn()

    def test_disabled_semantic_mode_does_not_load_dependencies(self):
        with patch.dict(os.environ, TALKEY_SMART_TURN='0'):
            self.assertIsNone(test_auto.auto.load_turn())


@unittest.skipIf(np is None, 'optional NumPy dependency not installed')
class FeatureTests(unittest.TestCase):
    def model(self, probability=.9):
        model = object.__new__(SmartTurn)
        model.np = np
        self.audio = None
        def features(audio, **kwargs):
            self.audio = audio
            return np.zeros((80, 800), dtype=np.float32)
        model.features = features
        class Session:
            def run(self, outputs, inputs):
                assert inputs['input_features'].shape == (1, 80, 800)
                return [np.array([[probability]])]
        model.session = Session()
        return model

    def test_short_utterance_is_left_padded(self):
        model = self.model()
        model.probability(np.full(16000, 16384, dtype='<i2').tobytes())
        self.assertEqual(len(self.audio), 128000)
        self.assertTrue(np.all(self.audio[:-16000] == 0))
        self.assertTrue(np.all(self.audio[-16000:] == .5))

    def test_long_utterance_keeps_latest_eight_seconds(self):
        model = self.model()
        pcm = np.concatenate((np.zeros(32000, dtype='<i2'), np.full(128000, 8192, dtype='<i2')))
        model.probability(pcm.tobytes())
        self.assertEqual(len(self.audio), 128000)
        self.assertTrue(np.all(self.audio == .25))

    def test_invalid_model_probabilities_fail_closed(self):
        for probability in (math.nan, math.inf, -1, 2):
            with self.subTest(probability=probability):
                with self.assertRaises(RuntimeError):
                    self.model(probability).probability(bytes(32000))

    def test_real_features_are_finite_and_have_model_shape(self):
        from vendor.smart_turn_features import compute_whisper_log_mel_features
        signal = np.sin(np.arange(128000, dtype=np.float32) * (2 * np.pi * 440 / 16000))
        for audio in (signal, np.zeros(128000, dtype=np.float32)):
            result = compute_whisper_log_mel_features(audio)
            self.assertEqual(result.shape, (80, 800))
            self.assertEqual(result.dtype, np.float32)
            self.assertTrue(np.isfinite(result).all())


if __name__ == '__main__':
    unittest.main()
