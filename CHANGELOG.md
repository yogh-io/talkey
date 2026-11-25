# Changelog

## Unreleased

- The subtitles on the talkey camera stay in the middle of the picture, in a 3:4 area, so call apps that crop your video to a portrait tile, like Jitsi, still show them whole (`TALKEY_CAMERA_CAPTION_ASPECT`).
- Add SeamlessStreaming as an optional translate engine: `TALKEY_TRANSLATE_ENGINE=seamless`. One model hears and translates you while you talk, and talkey speaks each whole English word with the same voice and timing. On 19 recorded samples the English started 2.2 seconds after the speaker began, against 3.2 with the default engine, and ended 3.5 seconds after they stopped, against 4.6. It needs about 5 GB of GPU memory, takes no context, and sometimes invents words. Its weights are Meta's, licensed CC-BY-NC-4.0, for non-commercial use only: talkey does not ship them, `talkey-setup seamless` downloads them after asking, and the default engine does not use them. With `TALKEY_SEAMLESS_PAUSE_WHISPER=1` it takes the whisper server's GPU memory while it translates, and dictation waits.
- The translate window names the engine in its bottom-right corner, and `talkey translate status` has an `engine` field.
- When translation stops after an error, or cannot start, the notification says why.
- Show live translation in a window of its own, wider than the dictation HUD: the recent phrases of the session, what was heard and the English that was said, newest at the bottom. The dictation HUD is unchanged.
- Highlight the English your listeners hear right now. talkey knows when each piece of English plays, so which piece is on air is exact; the words inside it follow an estimate. English that is translated but not played yet is shown apart.
- Scroll back through the session with the mouse wheel over the translate window. The window never takes the keyboard focus.
- Speak whole sentences instead of fragments. What English is spoken, and when, now follows measured signals: words whisper keeps hearing the same way, the translation model's own chance that a sentence ends, and how long the voice and the next piece take in your session. The fixed word counts and the comma rule are gone. On 19 recorded samples the waiting per sample went from 3.1 to 2.0 seconds and the longest wait from 2.1 to 1.4 seconds, at the same translation quality. The English starts a little later, about 3 seconds into a sentence; `TALKEY_TRANSLATE_GUARD` trades an earlier start for more breaks.
- The voice's pace follows the session: a little faster while more English waits behind it, slower when nothing is coming soon (`TALKEY_TRANSLATE_SPEED_MIN`, `TALKEY_TRANSLATE_SPEED_MAX`). The soft tick only fills a wait inside a sentence.
- Give the translator context: notes in `~/.config/talkey/translate-notes.txt` with the topic, names and terms, and a live context file any program may write while translating. Whisper gets them too, which helps it spell names.
- `TALKEY_TRANSLATE_TRACE=1` writes a step-by-step trace of each phrase, and `TALKEY_TRANSLATE_SINK` and `TALKEY_TRANSLATE_MIC` run a second, private instance, for testing.
- Translate mode now needs an Ollama that returns token probabilities (0.40.14 is known to work).
- Put the subtitles on your video. The talkey camera is a virtual camera: your webcam, with the phrase on air drawn at the bottom in both languages and the words being spoken marked. It starts with translation when the v4l2loopback device exists, stays on when translation stops so the call keeps its video, and runs your webcam only while an app shows it. `talkey camera start|stop|toggle|status` controls it on its own.

## 0.2.0 - 2026-11-24

- Add live translation in your own voice. Double-tap the memo key, or run `talkey translate toggle`, and talkey speaks what you say in English, in a voice cloned from 25 seconds of you, into a virtual microphone called “talkey English mic” for your call app. Everything runs locally: your whisper server, an Ollama translation model (`translategemma:4b` by default) and Pocket TTS on the CPU.
- Start the English before you finish a sentence. Every quarter second talkey translates what it has heard and speaks only English that later drafts keep, so a verb it would have to guess is never spoken. Each phrase is spoken as one continuous utterance, with a soft tick while it waits for the rest.
- Show live subtitles in the HUD while translating: your words as whisper hears them, with the English under them.
- Add `talkey translate start|stop|toggle|status` for status bars, and `talkey-setup translate` to install the voice, record your sample and clone it.
- Optionally keep each translated phrase’s audio (`TALKEY_TRANSLATE_RECORD=1`): what was heard and what the microphone carried, pauses included, to listen back.
- Keep auto-mode takes at `TALKEY_SAMPLE_RATE` when it is 32 or 48 kHz, while detection still runs at 16 kHz, so archived audio of your voice no longer stops at 8 kHz.
- Remove whisper’s text translation from the memo key, with its whisper-cli path and small model download. A quick memo tap is no longer played back as a memo.

Translate mode needs its own Python environment with Pocket TTS pinned to a commit that can continue an utterance; `talkey-setup translate` shows how. The voice cloning weights are gated on Hugging Face: accept the terms once and log in, and after that nothing leaves your machine. Your voice sample and its clone stay in `~/.local/share/talkey/translate`, readable only by you. Translation is as good as a 4B model: idioms and times like “half elf” can come out wrong, and the subtitles show it when they do.

## 0.1.2 - 2026-11-05

- Add an optional “message complete” cue, checked during pauses without waiting for semantic completion. The final text excludes the cue and follows the Enter setting.
- Add configurable whole-phrase voice commands, with three built-in Enter controls and support for user scripts.
- Add the configurable “stop listening” command and double-Triangle to toggle persistent auto Enter. Double-Square keeps its manual recording restart behavior.
- Make the dictate key finish an active auto phrase without disabling auto mode; a press during final transcription leaves the existing result alone.
- Resolve double taps before starting manual recording, and add distinct auto-mode on/off sounds.
- Switch auto mode off after a configurable idle interval (two minutes by default), without interrupting an unfinished phrase.
- Cap typing time at two seconds (`TALKEY_TYPE_MAX_MS`): long messages type faster, short ones keep the per-character pace.
- Fix the HUD level meter, which stayed empty since takes moved behind symlinks, and stop the HUD flickering when a take starts or ends.
- Fix long dictations where whisper repeated one sentence over the real speech that followed. Takes without a vocabulary prompt now send `max_context=0`.

## 0.1.1 - 2026-09-28

- Add background automatic dictation with Silero speech detection. Double-tap the dictate key to enable it, or use `talkey auto start|stop|toggle`.
- Add optional local Smart Turn v3.2 completion detection for thinking pauses. Confident completion waits for at least two seconds of silence; an incomplete thought can remain open for twenty seconds. Both limits are configurable.
- Add persistent automatic Enter control with `talkey auto enter-on|enter-off|enter-toggle`. The submit key still overrides individual phrases.
- Expose listening, speaking, paused, and processing states through `talkey auto status` for external status bars.
- Preserve unfinished audio on detector or capture failures, and cancel pending output when automatic dictation is stopped.
- Add setup checks, packaging support, and regression coverage for automatic dictation and semantic completion.

Smart Turn is opt-in and requires its model plus NumPy and ONNX Runtime. Run `talkey-setup vad` and `talkey-setup turn` for checks and setup instructions. Completion predictions can still mistake a thinking pause for the end of a turn.

Polybar configuration and the microphone level display remain desktop-local integrations outside this repository.
