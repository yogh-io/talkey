# Live translation in your own voice

![the HUD translating as you speak](screenshots/translate-demo.gif)

Talk in your own language, and the people in your call hear you in English, in
your own voice, about two to three seconds behind you. talkey listens to your
microphone, translates what you say, and speaks it into a virtual microphone
called **talkey English mic**. You pick that microphone in your call app, just
like a headset. A window shows what it heard and what it said, and which English
your listeners hear right now.

[Watch the demo with sound](media/translate-demo.mp4) (22 seconds, Dutch in,
English out; the demo uses a stock voice, not a cloned one).

Everything runs on your own machine. Nothing is sent anywhere, and there is no
account or subscription. The one exception is a one-time download of the voice
model, described below.

- [How it works](#how-it-works)
- [Install](#install)
- [Getting your voice right](#getting-your-voice-right)
- [Use it in a call](#use-it-in-a-call)
- [What to expect](#what-to-expect)
- [Settings](#settings)
- [Listening back and debugging](#listening-back-and-debugging)
- [Privacy](#privacy)
- [Troubleshooting](#troubleshooting)

## How it works

```mermaid
flowchart LR
  mic[Your microphone] --> vad[Silero: where phrases start and end]
  vad --> whisper[whisper server: what you said]
  whisper --> llm[Ollama, translategemma:4b: the English]
  llm --> tts[Pocket TTS on the CPU: your voice]
  tts --> sink[talkey English mic]
  sink --> call[Your call app]
  whisper --> hud[Translate window]
  llm --> hud
  tts --> hud
```

1. **Phrases.** Silero, the same speech detector auto mode uses, notices when
   you start talking and when you pause.
2. **Transcribing while you talk.** Every quarter second, your whisper server
   transcribes everything you have said in the phrase so far. Your last two
   sentences go along as context, which helps whisper pick words that fit.
3. **Translating while you talk.** Each time, TranslateGemma translates the whole
   phrase so far, continuing the English that has already been spoken.
4. **Only speaking what is settled.** English is spoken once a later translation,
   made after you said more, still starts with it. Many languages, Dutch and
   German among them, put the verb at the end of the sentence. Until you say it,
   the model guesses one, and the guess changes when the real verb arrives. So a
   guess is never spoken. The first piece of a phrase goes out as soon as two
   words are settled; later pieces wait until the voice is about to run out, and
   end where the model put a comma or a period.
5. **One continuous voice.** Pocket TTS speaks each piece as a continuation of
   the piece before it, so a sentence spoken in three pieces still sounds like
   one sentence. While it waits for the rest of a sentence, a soft tick tells
   the listeners you have not stopped talking.
6. **At your pause.** The whole phrase is transcribed once more, and whatever is
   still untranslated is translated and spoken.

The voice is **cloned, not trained**. Pocket TTS is a general English voice model.
Before it speaks, it is given a short recording of you, and it carries on as the
same speaker: your timbre, your accent, your intonation. The model itself never
changes. That is why a few seconds of recording are enough, and why the quality
of that recording matters so much (see [Getting your voice right](#getting-your-voice-right)).

## Install

This assumes talkey itself already works: dictation types text, so your whisper
server runs. Each step ends with a check that shows what you should see. If you
use an AI assistant to help you install, point it at this section; the checks
tell it whether a step worked.

### What you need

| | |
|---|---|
| talkey | working dictation, with its whisper server running |
| Audio | PulseAudio, or PipeWire with `pipewire-pulse` (`pactl` and `pacat`, from `libpulse`) |
| Ollama | running locally, with room for a 3.3 GB model |
| Python | 3.10 to 3.14, with `venv` and `pip`; and `git`, because pip installs the voice model from git |
| GPU | the translator wants about 2.9 GB of VRAM next to your whisper model. An 8 GB card with whisper large-v3-turbo works. |
| CPU | the voice runs on one CPU core, about 2.5 times faster than real time on a desktop CPU |
| Disk | about 1.5 GB for the voice environment, 3.3 GB for the translation model |
| An account at huggingface.co | free, needed once to download the voice cloning model |

### 1. Get the translation model

```bash
ollama pull translategemma:4b
```

Check: `ollama list` shows `translategemma:4b`.

### 2. Install the voice in its own Python environment

The voice needs PyTorch, which talkey itself does not, so it gets its own
environment. Find talkey's `config` folder first: `/usr/share/talkey/config`
for the AUR package, or `config/` in your checkout.

```bash
python3 -m venv ~/.local/share/talkey/translate-venv
~/.local/share/talkey/translate-venv/bin/python -m pip install \
  -r /usr/share/talkey/config/translate-requirements.txt \
  --extra-index-url https://download.pytorch.org/whl/cpu
```

The extra index matters: without it, pip downloads the CUDA build of PyTorch,
several gigabytes the voice does not use. The requirements pin Pocket TTS to a
git commit that is newer than its latest release, because only that version can
continue speech across pieces.

Check: this prints `True`:

```bash
~/.local/share/talkey/translate-venv/bin/python -c 'import inspect; from pocket_tts import TTSModel
print("forced_latents" in inspect.signature(TTSModel._generate_audio_stream_short_text).parameters)'
```

If it prints `False`, pip kept an older Pocket TTS with the same version number.
Reinstall just that package with the line from the requirements file:

```bash
~/.local/share/talkey/translate-venv/bin/python -m pip install --force-reinstall --no-deps \
  "$(grep '^pocket-tts' /usr/share/talkey/config/translate-requirements.txt)"
```

### 3. Tell talkey where the environment is

Add this line to `~/.config/talkey/talkey.env`, with your own home folder. Values
in that file are taken literally, so `~` and `$HOME` do not work there.

```
TALKEY_TRANSLATE_PYTHON=/home/YOU/.local/share/talkey/translate-venv/bin/python
```

### 4. Unlock voice cloning, once

Kyutai, who make Pocket TTS, hand out the part that clones voices only after you
accept their terms, which forbid cloning anyone's voice without their consent.

1. Log in at https://huggingface.co, open https://huggingface.co/kyutai/pocket-tts
   and accept the terms. Access is granted immediately.
2. Create a token with read access at https://huggingface.co/settings/tokens.
3. Log in from the terminal and paste the token when asked:

   ```bash
   ~/.local/share/talkey/translate-venv/bin/hf auth login
   ```

Check: `~/.local/share/talkey/translate-venv/bin/hf auth whoami` shows your
account name.

### 5. Record your voice and clone it

```bash
talkey-setup translate
```

It shows a short English text, records 25 seconds of you reading it, warns if the
recording was too quiet, then downloads the cloning model and makes your voice.
Read the section below before you record; the recording decides how much the
result sounds like you. You can record again later by running the same command
and answering **y** to "record a new voice sample?".

Check: it ends with `+ translate mode ready: talkey translate toggle`.

### 6. Make sure the translator runs on the GPU

Start translation once (step 7 below), say a sentence, and run `ollama ps`. The
`PROCESSOR` column should say `100% GPU`. Ollama is careful with VRAM and may
put part of the model on the CPU, shown as for example `23%/77% CPU/GPU`, which
makes each translation about three times slower. If you have the room, force it
fully onto the GPU in `talkey.env`, then switch translation off and on:

```
TALKEY_TRANSLATE_GPU_LAYERS=99
```

### 7. Try it

Double-tap the memo key, or run `talkey translate toggle`. After a few seconds you
hear the auto-mode "on" sound. Say a sentence.

Check: `talkey translate status` shows `{"enabled":true,"state":"listening",...}`,
and `pactl list short sources` lists `talkey_translate_mic`. The translate window
opens while you speak.

## Getting your voice right

The clone copies whatever is in your recording: not just your voice, but your
microphone, the room, the noise and the speaking style. These are the things that
made the difference while this feature was developed, from "not my voice at all"
to "sounds like me, accent included":

1. **Record in English**, the language it will speak, reading the text at your
   normal pace, the way you talk in a call. Your accent in English is exactly
   what it will copy.
2. **Use the microphone you use in calls**, in a quiet room, close to your mouth.
   No music, fan or keyboard noise. `talkey-setup translate` warns when the peak
   level is below -20 dB; aim for a peak around -6 dB.
3. **Do not use old recordings**, such as talkey's dictation archive. Those are
   often 16 kHz audio, which cuts everything above 8 kHz, and the clone then
   sounds muffled. Do not join clips from different microphones either.
4. **Set the voice temperature to 0.45** in `talkey.env`:

   ```
   TALKEY_TRANSLATE_TEMP=0.45
   ```

   It controls how much variation the voice allows. Measured against real
   recordings with a speaker-similarity model, 0.45 sounded most like the
   speaker; Pocket's default of 0.3 sounded flatter, and at 1.0 words start to
   slur.
5. **Listen back** with recordings switched on (see
   [Listening back and debugging](#listening-back-and-debugging)), and record
   again if it does not sound like you.

**What about training a voice model?** It was tried, and it is not needed. Short
fine-tuning of Pocket TTS on half an hour of a speaker's recordings did not make
the clone sound more like them. Kyutai's repository does include a full
fine-tuning recipe (`training/configs/finetune.yaml` followed by
`depth_distill.yaml` in https://github.com/kyutai-labs/pocket-tts), but it wants
many hours of clean speech at 24 kHz or higher with transcripts, a GPU with about
16 GB, and hours of training. A good 25-second recording gets you most of the
way there in a minute.

## Use it in a call

1. Double-tap the memo key to start translating. Double-tap it again to stop.
2. In your call app (Discord, Zoom, Teams, Jitsi, a browser), choose
   **talkey English mic** as your microphone. It appears the first time you start
   translation, and it stays when you stop, silent, so your app keeps it selected.
3. Use headphones, so the English you hear from others does not reach your
   microphone.
4. Watch the translate window. It shows your words as they are heard and the
   English under them. If something was misheard or comes out wrong, say it
   again.

![the translate window while you speak](screenshots/translate-hearing.png)

### The translate window

The window opens when you start talking and closes a few seconds after the last
English has played. It keeps the conversation, newest at the bottom:

- **»** what was heard, in grey. Slanted grey words are still being heard and can
  change.
- **→** the English. What your listeners hear right now has a teal background.
  Inside it, the words already spoken are white and the rest light teal.
- Slanted grey English is translated but not played yet. A **…** means more
  English is coming.
- **▶ ON AIR** in the corner means English is playing.

Which piece of English is playing is exact: talkey knows when it hands each piece
to the microphone. Where the voice is inside a piece is an estimate, from the
length of the words. Your call app adds its own small delay on top.

Scroll with the mouse wheel over the window to read back through the session,
up to the last 100 phrases. The corner then shows how many newer phrases are
below; scroll down to the end to follow along again. The window never takes the
keyboard focus, not even when you scroll it, so you keep typing in your call
app.

![the window with several phrases, the last one on air](screenshots/translate-done.png)

Translate mode and auto mode both act on everything you say, so starting one
stops the other. Push-to-talk dictation still works, but what you dictate is
also translated. While you dictate, the dictation HUD takes the translate
window's place.

### Subtitles on your camera

talkey can also put the subtitles on your video, so the people in your call can
read along and you can check yourself in your own preview. It is a virtual
camera called **talkey camera**: your webcam, with the phrase on air drawn at
the bottom. What you said is on top, slanted while it is still being heard; the
English is under it, marked the same way as in the translate window.

It needs the v4l2loopback kernel module, set up once:

```bash
sudo pacman -S v4l2loopback-dkms   # elsewhere: your distribution's v4l2loopback, with kernel headers
echo v4l2loopback | sudo tee /etc/modules-load.d/v4l2loopback.conf
echo 'options v4l2loopback devices=1 video_nr=10 card_label="talkey camera" exclusive_caps=1' \
  | sudo tee /etc/modprobe.d/v4l2loopback.conf
```

Then reboot. Browsers only list the camera with `exclusive_caps=1`, and talkey
finds it by the name `talkey camera`.

1. Start translating. When the talkey camera exists, it starts too.
2. In your call app, choose **talkey camera** as your camera.
3. Stopping translation leaves the camera on, without subtitles, so your call
   keeps its video. `talkey camera stop` turns it off, and `talkey camera start`
   turns it on without translating.

Your webcam only runs while an app shows the talkey camera, so its light goes off
when you leave the call, even with the camera on. Until the webcam has started,
the app gets a black picture. The subtitles appear when you start talking and stay
a few seconds after the English has played, like the translate window.

Most call apps mirror your own preview, so the subtitles look backwards to you
but read normally for everyone else. Most apps can switch the mirroring off in
their video settings.

## What to expect

- **Delay.** The English usually starts 2 to 3.5 seconds after you start a
  sentence, often well before you finish it. A short sentence starts about half a
  second after you pause. It cannot start much earlier without speaking guesses:
  the first two words of a phrase are only heard reliably after about 1.3
  seconds.
- **Pauses in the English.** When the rest of a sentence is not settled yet, the
  voice waits, with a soft tick so listeners know you are still talking. If you
  talk without pausing for a long time, the English can fall a few seconds behind
  and catch up at your next pause.
- **Mistakes.** Whisper sometimes mishears the first words of a phrase, and a 4B
  translation model gets some idioms and expressions wrong, for example Dutch
  times: "half elf" is 10:30, not 11:30. The translate window shows both what
  was heard and what was said.
- **Your voice.** The listeners hear your voice, so they will take its mistakes as
  yours. Where it matters, tell people you are using live translation.

## Settings

All of these go in `~/.config/talkey/talkey.env`. Switch translation off and on
after changing them.

| Setting | Default | Meaning |
|---|---|---|
| `TALKEY_TRANSLATE_PYTHON` | `python3` | The Python with Pocket TTS installed |
| `TALKEY_TRANSLATE_VOICE` | `~/.local/share/talkey/translate/voice.safetensors` | Your cloned voice |
| `TALKEY_TRANSLATE_FROM` | `nl` | The language you speak, as a whisper code; `auto` lets whisper guess per phrase |
| `TALKEY_TRANSLATE_LLM` | `translategemma:4b` | The Ollama model that translates |
| `TALKEY_TRANSLATE_GPU_LAYERS` | unset | `99` forces the whole translation model onto the GPU |
| `TALKEY_TRANSLATE_URL` | `http://127.0.0.1:11434/api/chat` | Ollama's chat endpoint |
| `TALKEY_TRANSLATE_TTS` | `english` | The Pocket TTS model |
| `TALKEY_TRANSLATE_TEMP` | Pocket's `0.3` | Voice temperature; `0.45` sounded most like the speaker |
| `TALKEY_TRANSLATE_SPEED` | `0.95` | Pace of the voice, without changing its pitch; below 1 is slower and gives the next piece more time to arrive. `1` keeps Pocket's own pace |
| `TALKEY_TRANSLATE_STREAM` | `1` | Speak settled English before you pause; `0` waits for the pause |
| `TALKEY_TRANSLATE_PREVIEW_MS` | `250` | How often the phrase so far is transcribed and translated |
| `TALKEY_TRANSLATE_TICK` | `1` | Tick softly while the voice waits for the rest of a sentence; `0` stays silent |
| `TALKEY_TRANSLATE_SILENCE_MS` | `500` | The pause that ends a phrase |
| `TALKEY_TRANSLATE_QUICK_AFTER_S` | `4` | After this much speech in one phrase... |
| `TALKEY_TRANSLATE_QUICK_SILENCE_MS` | `250` | ...this shorter pause ends it |
| `TALKEY_TRANSLATE_MAX_S` | `15` | The longest phrase; it is cut here even without a pause |
| `TALKEY_TRANSLATE_MONITOR` | `0` | `1` also plays the English on your own output; use headphones |
| `TALKEY_TRANSLATE_RECORD` | `0` | `1` keeps each phrase's audio, see below |
| `TALKEY_TRANSLATE_CAMERA` | `1` | Start the talkey camera with translation when it exists; `0` leaves it to `talkey camera start` |
| `TALKEY_CAMERA_INPUT` | your first webcam | The webcam the talkey camera shows, like `/dev/video0` |
| `TALKEY_CAMERA_SIZE` | `1280x720` | Size of the talkey camera; the webcam is scaled and cropped to fill it |
| `TALKEY_CAMERA_FPS` | `30` | Frames per second of the talkey camera |

For a status bar, `talkey translate status` prints one JSON object, for example
`{"enabled":true,"state":"hearing","microphone":"talkey_translate_mic"}`. The
state is `off`, `starting`, `listening`, `hearing` or `error`.
`talkey translate toggle` switches it from a click. `talkey camera status`
prints `{"enabled":true,"device":"/dev/video10"}`, with an empty device when
there is no talkey camera.

## Listening back and debugging

Every phrase is logged while translation runs:

```bash
tail -f "$XDG_RUNTIME_DIR/talkey/translate.current/lines.log"
```

Each entry shows what was heard, the English, the pieces it was spoken in, when
the voice began (counted from when you began, and from your pause; negative means
before it), how long the voice waited between pieces, and whether the voice ever
stuttered because it was generated too slowly.

With `TALKEY_TRANSLATE_RECORD=1`, every phrase also keeps two files in
`~/.local/share/talkey/recordings/translate/<session start>/`:

- `001-nl.wav`: what you said, exactly as whisper got it. Listen to this when
  something was misheard.
- `001-en.wav`: the English exactly as the microphone carried it, with the real
  pauses between pieces and the ticks in them. This is what your call heard.

The session folder has its own `lines.log`, whose entries name the two files.

To check that the English really reaches the microphone, record it directly while
you speak:

```bash
parecord --device=talkey_translate_mic check.wav
```

## Privacy

- Nothing leaves your machine while translating. A session runs with
  `HF_HUB_OFFLINE=1`; only `talkey-setup translate` downloads the voice model.
- Your recording and your cloned voice live in `~/.local/share/talkey/translate`,
  readable only by you. They are never part of talkey's repository or package.
- Recordings of your calls are off by default (`TALKEY_TRANSLATE_RECORD`).
- `lines.log` with the text of each phrase lives in `$XDG_RUNTIME_DIR/talkey`,
  which is normally cleared when you log out.
- The talkey camera never stores a frame. Your webcam's picture goes straight to
  the virtual camera, and only while an app shows it.

## Troubleshooting

**"translate mode unavailable - run: talkey-setup translate".** Something in
steps 2 to 5 is missing. `talkey-setup translate` checks each step and says which
one.

**"your input is the talkey English mic".** Your system's default microphone is
set to the virtual one, so talkey would translate its own output. Set your real
microphone as the default.

**It takes long to start, or the English is slow.** Run `ollama ps` while
translating. If it does not say `100% GPU`, see step 6. Something else using the
GPU, like a game, can push the model onto the CPU.

**Nobody hears you.** Check that your call app has **talkey English mic**
selected and is not muted. To rule out talkey, record the microphone as shown
above while you speak.

**It sounds choppy in a browser call.** Browsers run every microphone through
noise suppression, automatic gain and echo cancellation, which are tuned for
human voices. Switch noise suppression off for this microphone if your call app
allows it.

**Testing on your own.** Joining your own call twice in the same browser does
not work well: the browser's echo cancellation hears the translation playing in
one tab and removes it from the microphone in the other. Join from your phone
to listen, or record the microphone as shown above.

**The voice does not sound like you.** See
[Getting your voice right](#getting-your-voice-right).

**Your call app does not list the talkey camera.** `talkey camera status` should
show a device; if the device is empty, the module is not loaded or has another
name (see [Subtitles on your camera](#subtitles-on-your-camera)). The camera must
be on before the app looks for cameras: run `talkey camera start`, then pick the
camera again in the app. If the picture stays black, another app may have your
webcam open; the log says so.

**Anything else.** talkey's log is `$XDG_RUNTIME_DIR/talkey/log`, and
`talkey doctor` checks the rest of talkey.
