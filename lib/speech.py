"""Shared local Whisper requests and explicit end-of-message cue matching."""
import io
import re
import sys
import urllib.request
import uuid
import wave


def wav_bytes(pcm, sample_rate=16000, channels=1):
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm[:len(pcm) // (2 * channels) * (2 * channels)])
    return output.getvalue()


def transcribe(pcm, url, language, prompt, sample_rate, channels):
    boundary = uuid.uuid4().hex
    fields = {"language": language, "temperature": "0", "response_format": "text"}
    if prompt:
        fields["prompt"] = prompt
    parts = []
    for key, value in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="audio.wav"\r\nContent-Type: audio/wav\r\n\r\n'.encode())
    parts += [wav_bytes(pcm, sample_rate, channels), f'\r\n--{boundary}--\r\n'.encode()]
    request = urllib.request.Request(url, data=b"".join(parts), headers={
        "Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(request, timeout=20) as response:
        text = response.read().decode("utf-8")
    text = re.sub(r"\[[^\]]*\]|\*[^*]*\*", "", text)
    return re.sub(r"\s+", " ", text).strip()


def strip_end_cue(text, phrase):
    """Return text without an exact word suffix, or None when it is absent."""
    pattern = r"\w+(?:['’]\w+)*"
    cue = re.findall(pattern, phrase.casefold())
    words = list(re.finditer(pattern, text))
    if not cue or len(words) < len(cue):
        return None
    if [word.group().casefold() for word in words[-len(cue):]] != cue:
        return None
    start = words[-len(cue)].start()
    while len(words) >= len(cue) and [word.group().casefold() for word in words[-len(cue):]] == cue:
        start = words[-len(cue)].start()
        del words[-len(cue):]
    return text[:start].rstrip(' ,;:-')


if __name__ == '__main__':
    if len(sys.argv) != 3 or sys.argv[1] != 'strip-cue':
        raise SystemExit('usage: speech.py strip-cue PHRASE < transcript')
    text = sys.stdin.read().strip()
    stripped = strip_end_cue(text, sys.argv[2])
    print(text if stripped is None else stripped, end='')
