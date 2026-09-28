"""Local Smart Turn v3.2 CPU inference, loaded only when explicitly enabled."""
import math
import os
from pathlib import Path


class SmartTurn:
    def __init__(self):
        # Recent ONNX Runtime builds initialize telemetry during import.
        # Opt out before importing, as well as through the API below.
        os.environ['ORT_DISABLE_TELEMETRY'] = '1'
        try:
            import numpy as np
            import onnxruntime as ort
            from vendor.smart_turn_features import compute_whisper_log_mel_features
        except ImportError as error:
            raise ValueError('Smart Turn needs numpy and onnxruntime in TALKEY_AUTO_PYTHON; '
                             'run talkey-setup turn') from error
        self.np = np
        self.features = compute_whisper_log_mel_features
        ort.disable_telemetry_events()
        data = Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share'))
        model = Path(os.environ.get('TALKEY_TURN_MODEL', data / 'talkey/models/smart-turn-v3.2-cpu.onnx'))
        if not model.is_file():
            raise ValueError('Smart Turn model missing; run talkey-setup turn')
        options = ort.SessionOptions()
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.inter_op_num_threads = options.intra_op_num_threads = 1
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.session = ort.InferenceSession(str(model), sess_options=options,
                                           providers=['CPUExecutionProvider'])

    def probability(self, pcm):
        """Use the last eight seconds, left-padding short utterances as trained."""
        np = self.np
        audio = np.frombuffer(pcm, dtype='<i2').astype(np.float32) / 32768.0
        audio = audio[-128000:]
        if audio.size < 128000:
            audio = np.pad(audio, (128000 - audio.size, 0))
        features = self.features(audio, do_normalize=True)[None, :, :]
        output = self.session.run(None, {'input_features': features})
        probability = float(np.asarray(output[0]).reshape(-1)[0])
        if not math.isfinite(probability) or not 0 <= probability <= 1:
            raise RuntimeError('Smart Turn returned an invalid completion probability')
        return probability
