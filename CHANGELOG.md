# Changelog

## Unreleased

- Add an optional “message complete” cue, checked during pauses without waiting for semantic completion. The final text excludes the cue and follows the Enter setting.
- Add configurable whole-phrase voice commands, with three built-in Enter controls and support for user scripts.
- Add the configurable “stop listening” command and double-Triangle to toggle persistent auto Enter. Double-Square keeps its manual recording restart behavior.
- Make the dictate key finish an active auto phrase without disabling auto mode; a press during final transcription leaves the existing result alone.
- Resolve double taps before starting manual recording, and add distinct auto-mode on/off sounds.
- Switch auto mode off after a configurable idle interval (two minutes by default), without interrupting an unfinished phrase.
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
