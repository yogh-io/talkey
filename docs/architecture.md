# Runtime and maintenance

Talkey uses Bash for desktop integration and a Python standard-library worker for
live transcription. The model, microphone, desktop and renderer remain external
processes. No resident controller daemon is required.

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
