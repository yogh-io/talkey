# Smart Turn preprocessing

`smart_turn_features.py` is copied unchanged from Pipecat's
`src/pipecat/audio/turn/smart_turn/_whisper_features.py` at commit
`2716ecb780470bcdc56f3e5ac01057a04104a519`:
https://github.com/pipecat-ai/pipecat/blob/2716ecb780470bcdc56f3e5ac01057a04104a519/src/pipecat/audio/turn/smart_turn/_whisper_features.py

SHA-256: `c4eb415855fc2c66ea09ad1189a18396ce9b26d060b2b9ced39b9b81a0f60342`.

Copyright 2024–2026 Daily, BSD-2-Clause (see LICENSE.pipecat). The upstream
file describes its derivation from Hugging Face Transformers' Apache-2.0
Whisper feature extraction; LICENSE.transformers is included as well.
Vendoring this NumPy implementation avoids loading Transformers or PyTorch
just to prepare input for the CPU ONNX turn detector.
