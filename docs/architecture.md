# Runtime and maintenance

Talkey uses Bash for desktop integration and a Python standard-library worker for
live transcription. The model, microphone, desktop and renderer remain external
processes. Manual dictation needs no resident controller daemon; opt-in auto mode
runs a background Python capture/VAD worker.

## Modules

| Location | Responsibility |
|---|---|
| `bin/talkey` | Validate the command, initialize modules, acquire the event lock |
| `lib/controller.sh` | Key events, take lifecycle, final result and submit decisions |
| `lib/session.sh` | Per-take paths, atomic selection, locking and process identities |
| `lib/recording.sh` | Recorder/pump startup and shutdown, WAV conversion, memo playback |
| `lib/transcription.sh` | Final inference and acoustic measurements |
| `lib/output.sh` | Capture the target, insert text and guard Enter against focus changes |
| `lib/archive.sh` | Unique private archives, transcript sidecars and optional titles |
| `lib/display.sh` | Publish the take's display state and metrics |
| `lib/common.sh`, `config.sh`, `settings.sh`, `whisper.sh` | Shared initialization, safe config parsing, recorder defaults and discovery |
| `lib/setup/` | Interactive setup, split into UI, dependencies, config, models, service and bindings |
| `bin/talkey-live-pump` | Interruptible HTTP inference, two-pass agreement, atomic preview publication |
| `bin/talkey-auto-worker`, `lib/auto.sh` | Continuous capture, Silero VAD, endpoint detection, ordered delivery and external status |
| `bin/talkey-translate-worker`, `lib/translate.sh` | Live translation: capture, recognition, Ollama translation, Pocket TTS and the virtual microphone |
| `lib/vad.py` | Silero VAD through whisper.cpp and audio-time endpoints, shared by both workers |
| `lib/stretch.py` | Changes the pace of the translated voice without changing its pitch (WSOLA), as it streams |
| `bin/talkey-camera-worker`, `lib/camera.sh`, `lib/captions.py` | The talkey camera: the webcam with the translation's subtitles, written to a v4l2loopback device |
| `lib/smart_turn.py`, `lib/vendor/` | Optional CPU ONNX completion inference and pinned Whisper feature extraction |
| `lib/hud.sh`, `integrations/eww/` | Presentation, meter rendering and window lifecycle |
| `lib/install.sh`, `setup/`, `packaging/aur/` | Unit rendering, checkout setup, build and packaging |

## Ownership rules

Every recording receives a unique directory under `$STATE_DIR/takes/`. Audio, its format,
language, worker identities, timing, control flags, metrics, live text and final
text belong to that directory. A worker resolves it once and never switches to a
newer take. Recording language therefore survives release from another binding.

`dictate.current` selects the displayed dictation; `memo.current` selects a memo.
They are symlinks replaced with an atomic rename. Compatibility paths such as
`dictate.raw` and `dictate.transcript` pass through `dictate.current`. Renderers
can continue reading those names. `session.changed` signals lifecycle and display
updates to the root directory's inotify watcher.

The command lock serializes key-event state changes and desktop output. Recording
and HTTP inference do not retain the lock. Long-lived children close its file
descriptor. Two finish events cannot both claim a recorder, and a completed old
take cannot remove a newer take's timestamp, controls, or pending marker.

On release, the controller stops the recorder and live worker before making the
final request. A worker identity includes the PID and Linux process start time;
stale PID files cannot authorize a signal to a reused PID. Shutdown is bounded,
with forced termination only while that identity still matches.

The finisher reacquires the command lock after inference. It rereads that take's
cancel/submit flags, so a control press during inference still takes effect. It
only inserts text if the result is nonempty, inference succeeded, and this remains
the current take. A superseded result stays in its own `final.txt` and archive;
it is never injected into the newer dictation. Enter follows successful insertion
at most once. Focus movement can still redirect X11 typing mid-stream; Enter is
skipped if focus no longer matches the captured target.

## Recovery and privacy

Auto mode selects a unique run directory through `auto.current`. Internal
`_auto` callbacks must match that run and its live worker identity. One worker
captures 16 kHz PCM into a bounded queue while finalization uses the existing
controller. At 32 or 48 kHz, the same ffmpeg writes a second stream to a pipe;
each 16 kHz frame is paired with its full-rate audio, so detection, Smart Turn
and the end cue are unchanged while the take keeps the full rate. The rate is
fixed when the worker starts and reaches `_auto begin` through its environment. An auto take's recorder PID identifies the listener for live preview;
`record_stop` removes this take's marker without stopping continuous capture.
Only auto-mode shutdown stops that worker. Stopping invalidates the run before
signalling it and cancels an in-flight take under the command lock. Endpoint
markers are written before final inference. The next take is published only
after the preceding finalizer returns, preserving ordered output and the
existing current-take guard. Errors leave unfinished PCM recoverable.

The VAD uses whisper.cpp's shared C API through ctypes. Newer libraries retain
Silero state between frames, initialized explicitly before the first frame;
older VAD-capable libraries classify a rolling one-second context. The detector
does not invoke transcription while waiting for speech. Idle audio is bounded
in memory and is not archived. A physical mute can either deliver zero samples
or stall the stream; both count toward an active phrase's silence timer.

With `TALKEY_SMART_TURN=1`, a VAD pause schedules a single semantic prediction on
a background thread. The main loop continues capture/VAD and increments a
revision on every speech frame, invalidating predictions made before resumed
speech. Input uses the last eight seconds of audio, left-padded when short; the
silent tail is capped at about half a second so increasing patience cannot erase
the linguistic context. The PCM context buffer is bounded by eight seconds plus
the configured maximum pause. Predictions are not repeatedly rerun on silence.

A confident result commits only after the minimum silence and after already
queued audio has been consumed. An incomplete result holds the same take until
speech resumes or the maximum silence elapses. The recording cap remains an
independent limit. Model errors, invalid probabilities and five-second inference
timeouts fail closed, leaving the take recoverable without typing or Enter.
The `paused` status distinguishes a held phrase from idle listening. The same
finalizer and submit flags apply after either semantic completion or the long
silence timeout, so cancellation and persistent Enter retain their behavior.

Smart Turn is opt-in and uses `TALKEY_AUTO_PYTHON` for the auto worker. Its model
is loaded once and inference stays on CPU. Dependencies and model are checked
before a manual recording is interrupted when enabling auto mode. The optional
libraries are not imported in silence-only mode. The package ships the adapter,
pinned feature extractor, requirements and licenses, but not model weights.

An optional `TALKEY_END_PHRASE` adds a separate bounded Whisper cue check after
a short pause. It uses the same HTTP/WAV helpers as the live pump, but does not
depend on the live preview or its duration cap. A background request receives
at most eight seconds of audio. Matching requires a complete word suffix; capture
revisions invalidate stale results and queued audio is drained before finishing.
Request failures leave the normal endpoint policy in charge. Final transcription
independently strips a matching cue before desktop output; an empty result never
presses Enter. Manual dictation does not strip cue text.

The dictate key requests a finish using a marker in the current auto take. The
capture worker closes that take and invokes the existing finalizer, retaining the
Enter/cancellation policy and returning to listening. A press during finalization
does not start a new take. While idle, the same key stops auto mode; explicit
`auto stop` continues to cancel unfinished work.

Dictate presses first create a pending gesture with a timestamp, language and
intended take/run. A timer resolves a single press only after the double-tap
window. A second press after a release cancels that gesture and toggles auto mode
directly, without manual recorder/HUD side effects. Timer callbacks verify their
gesture identity, and finish requests verify the captured take/run so a newer
phrase cannot be finished accidentally. Auto mode has separate on/off tones and
an idle timeout measured since the latest detected speech; active takes finish
before this timeout can stop the worker.

Triangle presses have a separate timer: one press retains the submit/cancel/plain
cycle, while two presses toggle the persistent auto Enter preference without
performing the single-press action. Finalizers resolve any applicable pending
triangle gesture before output, releasing the lock while waiting for another tap.
Its callback runs with isolated shell state so it cannot replace the finalizer's
take paths if a newer take appears. Square retains the manual restart gesture.

Opt-in voice command dispatch runs after final transcription, cue stripping,
cancellation and current-take checks, before keyboard output. Built-in Enter
actions call the same preference handler as the CLI without reacquiring its lock.
User JSON definitions overlay packaged defaults by filename. External actions
receive only predefined argument lists; a detached runner closes the command
lock and has no cleanup trap for the parent take. Script completion notifications
and result files do not overwrite a newer take's HUD. Unknown commands never fall
through to keyboard output. A literal escape bypasses matching explicitly.

Archive conversion first reserves a private, unique temporary WAV. Only completed
files become visible archive entries. Successful dictations also have a `.txt`
sidecar, independent of whether live preview is enabled. Cancelled/restarted takes
retain their audio and any available preview under the cancelled archive directory.
Ollama titling runs separately and renames both sidecars without holding the event
lock. Disabling `TALKEY_ARCHIVE` disables persistent audio and transcript archives.

If conversion or archiving fails, the source PCM remains in the take directory.
Final text remains there if desktop insertion fails or another take supersedes it.
Completed metadata and recovery files remain until the runtime directory is cleared
(normally at logout). The `/tmp` fallback and custom directories depend on the
machine's cleanup policy. No task automatically deletes a directory containing
unrecovered takes.

## Translate mode

`translate.current` selects the run directory, fenced the same way as auto mode.
The worker owns no takes and types nothing. Its main thread captures audio and
runs VAD. While a phrase is heard, a preview worker transcribes it every 250 ms
and translates all of it, continuing the English already spoken; `Agreement`
decides which earlier draft is safe to speak (a later preview heard more Dutch
after the same words, and the later draft still starts with that English). At
the pause a recognition thread transcribes the whole phrase and translates the
rest. Both hold the phrase's lock, so the final step waits for a running
preview. A speech thread takes phrases in the order they began and streams
their pieces through Pocket TTS into `pacat`; a piece after the first is
generated with the previous piece's text and frames forced in front, and a
piece that is not the last loses its end-of-speech tail, so a phrase stays one
utterance. Any stage error stops the worker and reports `error`.

With `TALKEY_TRANSLATE_TRACE=1` the worker appends every step of each phrase to
`trace.jsonl` in the run directory: speech start and end, each preview and
draft with its latency, each `Agreement` decision and its reason, pieces queued,
the voice's first audio and the play schedule, and ticks. Times are seconds
since the phrase's first word on the audio clock: capture frame k maps to the
earliest monotonic time it could have arrived, so a busy main loop does not
shift them.

The worker publishes what the HUD shows in `$STATE_DIR`, each file replaced with
an atomic rename:

| File | Contents |
|---|---|
| `translate.subtitles` | JSON `{"lines": [...]}`, the session's last 100 phrases, oldest first |
| `translate.showing` | exists while a phrase is heard, translated or spoken, until its English has finished playing |
| `translate.raw` | the last 0.13 s of microphone audio, refreshed about 8 times a second while `translate.showing` exists |
| `translate.scroll` | written by the HUD, not the worker: the number of the phrase at the bottom of a view scrolled back |

A line is `{"n", "dutch", "live", "english", "done", "pieces"}`: its number in the
session, what was heard, whether it is still being heard, the English so far,
whether more English can follow, and the pieces the English is spoken in. A
piece is `{"text", "start", "end"}`. `start` is the Unix time, in seconds, at
which its first sample is due to play, and `end` the time its last sample has
played; `start` is null until the voice has made the first audio, `end` while it
is still making it. Both come from the schedule `send` keeps for the player,
converted from the monotonic clock when they are published, so a renderer can
compare them with its own clock. Ticks between pieces are not pieces. The call
hears the audio after the player's 60 ms buffer and its own delay.

`talkey-hud` opens the `talkeytranslate` window while `translate.showing`
exists, unless a dictation or an alert needs the HUD, and closes it a linger
after. `eww-feed translate` draws a fixed number of rows as Pango markup, so the
window never changes size: a piece is played, on air or queued by comparing its
times with the clock, and the words spoken inside the piece on air are estimated
from their share of its characters (for a piece still being made, from the
session's seconds per character). It redraws ten times a second while a piece is
on air or due, and otherwise waits for a state file to change. The window is
`wm-ignore`, so the window manager never gives it focus, and its mouse wheel
runs `eww-feed scroll` with the phrases the frame names for up and down.

The null sink and its remap source are created on start when missing and are not
removed on stop, so applications keep the microphone selected. Start refuses a
capture source that is the translation microphone itself. Model weights are only
downloaded by `talkey-setup translate`; the worker sets `HF_HUB_OFFLINE=1`
before importing Pocket TTS.

The talkey camera is the v4l2loopback device named `talkey camera`.
`talkey-camera-worker` opens it as the writer, sets an I420 format and writes one
black frame, because with `exclusive_caps=1` apps only list a device someone
writes to. It subscribes to v4l2loopback's client usage event, which says whether
an app is reading the camera. Only then does it run ffmpeg on the webcam, scaled
and cropped to the camera's size, and it stops ffmpeg when the app stops reading.
Until the first webcam frame it writes black frames at the frame rate. On each
frame it draws the subtitles from `translate.subtitles` while
`translate.showing` exists and a linger after, with the translate window's rules
for which phrase and words are on air (`lib/captions.py`). The subtitle box is
rendered again only when its words or their looks change; per frame it is a blend
into the rows it covers. Starting translation starts the worker when the device
exists (`TALKEY_TRANSLATE_CAMERA`); stopping translation leaves it running, so a
call keeps its video.

## Configuration and installation

All commands, including systemd services, use one config parser. It accepts
single-line `TALKEY_KEY=value` assignments, optional quotes and comments. Double
quotes support escaped quotes and backslashes. Values are literal: no shell
expansion or execution. The inherited environment wins; the last duplicate in a
file wins. Setup writes values through a temporary file and atomic rename.
This is a documented subset, not a full systemd EnvironmentFile parser.

The server, client, doctor and setup share model/binary/URL discovery. The checkout
and package both expose `bin/talkey-hud`; it launches the adapter from the data
root. Systemd unit rendering quotes executable paths and escapes specifiers.

## Verification

Run `setup/check.sh`. It checks Bash syntax, runs ShellCheck errors when installed,
and runs the offline Python unittest suite. CI installs ShellCheck and jq and runs
the same command. Tests use temporary state/config directories and fake recording,
inference and keyboard programs; they do not access the user's microphone, desktop,
model, service manager or configuration.

The suite covers failure-to-submit behavior, overlapping recordings, process
identity, cancellation recovery, worker shutdown, config parsing and package
layout. Audio accuracy, real X11 focus behavior, GPU performance and eww appearance
still require a desktop smoke test.
