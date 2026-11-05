# Changelog

## Unreleased

- Cap typing time at two seconds (`TALKEY_TYPE_MAX_MS`): long messages type faster, short ones keep the per-character pace.
- Fix the HUD level meter, which stayed empty since takes moved behind symlinks, and stop the HUD flickering when a take starts or ends.

## 0.1.1 - 2026-09-28

- Add background automatic dictation with Silero speech detection. Double-tap the dictate key to enable it, or use `talkey auto start|stop|toggle`.
- Add optional local Smart Turn v3.2 completion detection for thinking pauses. Confident completion waits for at least two seconds of silence; an incomplete thought can remain open for twenty seconds. Both limits are configurable.
- Add persistent automatic Enter control with `talkey auto enter-on|enter-off|enter-toggle`. The submit key still overrides individual phrases.
- Expose listening, speaking, paused, and processing states through `talkey auto status` for external status bars.
- Preserve unfinished audio on detector or capture failures, and cancel pending output when automatic dictation is stopped.
- Add setup checks, packaging support, and regression coverage for automatic dictation and semantic completion.

Smart Turn is opt-in and requires its model plus NumPy and ONNX Runtime. Run `talkey-setup vad` and `talkey-setup turn` for checks and setup instructions. Completion predictions can still mistake a thinking pause for the end of a turn.

Polybar configuration and the microphone level display remain desktop-local integrations outside this repository.
