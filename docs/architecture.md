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
| `lib/seamless.py`, `bin/talkey-seamless-server` | The optional SeamlessStreaming translate engine: the worker's side, whole-word completion, and the model server in its own Python |
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
runs VAD. An engine turns each phrase into English; the default one,
`WhisperEngine`, works as follows. While a phrase is heard, a preview worker transcribes it every 250 ms
with whisper's `verbose_json`, and translates the words it can trust: the start
that this and the previous transcript share. A word whose probability (that of
its least likely token) is below `TALKEY_TRANSLATE_WORD_P` must also be in the
transcript before that: whisper splits its probability between spellings of
the same word, such as `Er` and `er`, so a low one only asks for more agreement.
It continues the English already spoken. The same Dutch after the same English
is not translated again. `Agreement`
decides which English is settled: the words the draft shares from its start
with the latest draft made from less of the same Dutch. English is also
settled when the model, asked through `/api/generate` as a raw
language model for one token, gives at least `TALKEY_TRANSLATE_END_P` to a
sentence end after the Dutch so far: end of text, a new line, or a token that
starts with `.`, `?` or `!`. Such a whole draft stays settled while later
previews keep its Dutch. The worker then decides from times it measures during
the session: settled English waits while the voice has more audio left than its
mean time to first audio plus the mean time between the engine's looks at the
phrase, previews here (`voice-busy`),
and it waits while the audio in hand, at the slowest pace, would not last the
mean time from one piece being handed over to the next being settled
(`would-run-dry`, scaled by `TALKEY_TRANSLATE_GUARD`). The voice asks for a
pace before each chunk; `lib/stretch.py` moves its nominal input place on by
the speed of each window, so the pace can change mid-piece. It is
`TALKEY_TRANSLATE_SPEED_MAX` while pieces wait behind the one being said, or
the next phrase has begun; otherwise slower, down to `_MIN`, in proportion to
the English in hand against that usual wait. A tick fills a wait only after a
piece that did not end a sentence. When the model carries on inside the last spoken word, as `'ll` after `I`,
the piece is joined to it without a space, in the text and in the voice. At
the pause a recognition thread transcribes the whole phrase and translates the
rest. Both hold the phrase's lock, so the final step waits for a running
preview. A speech thread takes phrases in the order they began and streams
their pieces through Pocket TTS into `pacat`; a piece after the first is
generated with the previous piece's text and frames forced in front, and a
piece that is not the last loses its end-of-speech tail, so a phrase stays one
utterance. Any stage error stops the worker and reports `error`.

The worker's `Interpreter` owns capture, VAD, phrases, the voice, the subtitles
and the trace. `TALKEY_TRANSLATE_ENGINE` picks the engine from the worker's
`ENGINES`, the only place that knows their names. The Interpreter calls the
engine's `begin`, `hear` (each frame) and `end` (the pause) with the phrase,
whose `frames` hold its audio so far. The engine answers through `heard` (the
text in the speaker's language), `gate` (whether a piece goes to the voice now,
or why it waits), `queue` (speak it) and exactly one of `finish` or `drop` per
phrase, and may use `cycle`, `trace` and `guard`; nothing else of the
Interpreter. `start` loads the engine while the voice loads, so `ready` comes
after both; `close` stops it. `tests/test_seamless.py` drives both engines
against a stand-in that has only these members.

`SeamlessEngine` (`lib/seamless.py`, standard library only) runs
`bin/talkey-seamless-server` with the Python in `TALKEY_SEAMLESS_HOME/venv` and
talks JSON lines with it. To the server: `start`, `audio` (base64 16 kHz s16le,
the preroll in the first) and `end`, each with the phrase number. From it:
`ready` (load time, GPU memory held, the time of a full-length step); per model
step `step` (`heard_s`, `compute_s`, the new whole English words, and `end`:
whether the model's next token is end of text), then `source` (new words in the
speaker's language); at the pause `done` with the rest of the English and a
last `source`; `error` with a one-line reason, after which it exits. It steps
once 320 ms of audio waits, with all the audio that waits, so a slow step never
builds a backlog. It runs SeamlessStreaming's agents by hand: features, the
w2v-BERT encoder, and two monotonic decoders on the same encoder output, one
for English and one for the speaker's language. It writes fairseq2 asset cards
into the run directory that point every model file at a local path, with an
empty cache directory, so a missing file is an error and never a download, and
it never builds the Silero agent, which would call `torch.hub`. It moves its
real stdout aside, so a library that prints cannot break the protocol. After
loading it runs one phrase of `TALKEY_TRANSLATE_MAX_S` seconds of quiet noise,
which reserves the memory a long phrase needs and warms the kernels; too little
GPU memory fails then, with the free memory and the other GPU users in the
reason. It exits as soon as its stdin ends, even while it loads, so a killed
worker frees the GPU. A stop while the engine loads closes it before the worker
waits for `start`, so the server has gone before the worker exits.

The decoders give SentencePiece tokens. A token that starts with `▁` begins a
word, any other continues the open word. `Words` releases a word when the next
one begins, when the model's next token is end of text, or at the pause; no
word lists or punctuation rules. On every `step` the engine offers the English
not yet spoken to `gate`, so a held piece grows; at `done` the rest is the last
piece, or the phrase is dropped when it had no English at all. A piece that the
model ends with end of text counts as a sentence end, so no tick follows it. A
server that stops or reports an error stops the worker with `error`; there is
no switch to the other engine.

`translate start` writes the engine to the run directory's `engine` file, which
`translate status` and the translate window read. Before it reports `error`,
the worker writes its reason to `error`, and the notification shows the first
line. With `TALKEY_SEAMLESS_PAUSE_WHISPER=1`, starting the Seamless engine stops
`talkey-whisper.service` when it runs and creates `$STATE_DIR/whisper.paused`;
it refuses to start while `dictation_active`, so no take loses its server.
`translate stop` starts the unit again, and so does `error`, once the worker
has exited. While translate runs, `preflight_ok` reports the pause instead of a
missing server; otherwise it starts the unit and waits up to 10 seconds for it,
which also heals a session that was killed.

The translator also gets the conversation context: the notes file
(`TALKEY_TRANSLATE_NOTES`) and `$STATE_DIR/translate.context`, which any
program may write and which `translate start` removes. Both are re-read, when
their modification time changed, as a phrase starts, and stay fixed for that
phrase, so its prompts stay the same. Their newest `num_ctx` characters go into
the instruction as "Context of the conversation, for reference only", and
before the recent sentences in whisper's prompt. They are never traced or
logged.

With `TALKEY_TRANSLATE_TRACE=1` the worker appends every step of each phrase to
`trace.jsonl` in the run directory: speech start and end, each preview and
draft with its latency, each `Agreement` decision and its reason, pieces queued,
the voice's first audio and the play schedule, and ticks. Times are seconds
since the phrase's first word on the audio clock: capture frame k maps to the
earliest monotonic time it could have arrived, so a busy main loop does not
shift them. The Seamless engine writes `step` (`heard_to`, `compute`, `text`,
`eot`: whether the model's next token is end of text), `source` (`text`),
`agree` (`result` `spoken`, `voice-busy` or `would-run-dry`, `piece`,
`voice_left`, `eot`), `closed` and `final` (`text`, `compute`) instead of the
previews and drafts; the events of the voice are the same for both. The whisper
engine's `agree` has `ends`, the chance that a sentence ends there, instead.

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
downloaded by `talkey-setup translate` and, for the optional Seamless engine,
`talkey-setup seamless`; the worker sets `HF_HUB_OFFLINE=1` before importing
Pocket TTS, and the Seamless server inherits it.

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
