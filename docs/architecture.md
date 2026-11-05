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
controller. An auto take's recorder PID identifies the listener for live preview;
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
