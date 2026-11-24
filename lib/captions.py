"""Subtitles over a video frame: the phrase on air in Dutch and English, with the words being spoken marked."""
import subprocess

import numpy as np
from PIL import Image, ImageDraw, ImageFont

# The translate window's colours (lib/hud.sh): dim Dutch, bright English, the piece on air on teal.
LOOK = {'nl': ('#b4bcc3', None), 'heard': ('#9aa3ab', None), 'wait': ('#8d969d', None),
        'played': ('#f2f4f5', None), 'said': ('#ffffff', '#1e5e68'), 'next': ('#a9dde4', '#1e5e68'),
        'queued': ('#8d969d', None)}
# Seconds per character of English until some has played, as in the translate window.
RATE = 0.065
ROWS = 2


def on_air(piece, now):
    start, end = piece.get('start'), piece.get('end')
    return start is not None and start <= now and (end is None or now < end)


def current(lines, now):
    """The phrase whose English is on air, else the newest."""
    for line in reversed(lines):
        if any(on_air(piece, now) for piece in line.get('pieces', [])):
            return line
    return lines[-1] if lines else None


def rate(lines):
    played = [piece for line in lines for piece in line.get('pieces', [])
              if piece.get('start') is not None and piece.get('end') is not None and piece['end'] > piece['start']]
    chars = sum(len(piece['text']) for piece in played)
    return sum(piece['end'] - piece['start'] for piece in played) / chars if chars else RATE


def piece_words(piece, now, speed):
    """Where the voice is inside a piece is estimated from its characters."""
    words = piece['text'].split()
    start, end = piece.get('start'), piece.get('end')
    if start is None or now < start:
        return [(word, 'queued') for word in words]
    if end is not None and now >= end:
        return [(word, 'played') for word in words]
    length = end - start if end is not None else len(piece['text']) * speed
    at = (now - start) / max(length, 0.001) * len(piece['text'])
    out, chars = [], 0
    for i, word in enumerate(words):
        out.append((word, 'said' if i == 0 or chars <= at else 'next'))
        chars += len(word) + 1
    return out


def words(lines, now):
    """The Dutch and English words to show, each with how it looks."""
    line = current(lines, now)
    if line is None:
        return [], []
    dutch = [(word, 'heard' if line.get('live') else 'nl') for word in line.get('dutch', '').split()]
    speed = rate(lines)
    english = [word for piece in line.get('pieces', []) for word in piece_words(piece, now, speed)]
    if not line.get('done'):
        english.append(('…', 'wait'))
    return dutch or [('…', 'wait')], english


def font(pattern, size):
    path = subprocess.run(['fc-match', '-f', '%{file}', pattern], capture_output=True, text=True, check=True).stdout
    return ImageFont.truetype(path, size)


def wrap(words, face, width):
    rows, row, x = [], [], 0
    space = face.getlength(' ')
    for word, look in words:
        w = face.getlength(word)
        if row and x + w > width:
            rows.append(row)
            row, x = [], 0
        row.append((word, look, x))
        x += w + space
    return rows + [row] if row else rows


def following(rows):
    """The rows around the voice: the row it is in and the next, or the last rows once it is done."""
    marks = [(r, look) for r, row in enumerate(rows) for _, look, _ in row]
    said = [r for r, look in marks if look == 'said']
    played = [r for r, look in marks if look == 'played']
    at = said[-1] if said else played[-1] if played else 0
    first = max(0, min(at, len(rows) - ROWS))
    return rows[first:first + ROWS]


class Captions:
    """Draws the subtitles into I420 frames of one size."""

    def __init__(self, width, height):
        self.width, self.height = width, height
        s = height / 720
        self.dutch = font('sans-serif', round(25 * s))
        self.heard = font('sans-serif:italic', round(25 * s))
        self.english = font('sans-serif:medium', round(36 * s))
        self.margin, self.pad, self.gap = round(90 * s), round(22 * s), round(10 * s)
        self.dutch_h, self.english_h, self.bottom = round(34 * s), round(48 * s), round(34 * s)
        self.key = None
        self.layer = None

    def draw(self, frame, dutch, english):
        """Blend the subtitles into an I420 frame (a writable buffer) in place."""
        if not dutch and not english:
            return
        key = (tuple(dutch), tuple(english))
        if key != self.key:
            self.key, self.layer = key, self.render(dutch, english)
        top, left, y, y_keep, u, v, c_keep = self.layer
        w, h = self.width, self.height
        pixels = np.frombuffer(frame, dtype=np.uint8)
        planes = (pixels[:w * h].reshape(h, w), pixels[w * h:w * h * 5 // 4].reshape(h // 2, w // 2),
                  pixels[w * h * 5 // 4:].reshape(h // 2, w // 2))
        for plane, (row, col), value, keep in ((planes[0], (top, left), y, y_keep),
                                               (planes[1], (top // 2, left // 2), u, c_keep),
                                               (planes[2], (top // 2, left // 2), v, c_keep)):
            area = plane[row:row + value.shape[0], col:col + value.shape[1]]
            area[:] = np.rint(area * keep + value).astype(np.uint8)

    def render(self, dutch, english):
        """The subtitle box as planes to blend: premultiplied Y, U, V and what is kept of the frame."""
        width = self.width - 2 * self.margin - 2 * self.pad
        face = self.heard if any(look == 'heard' for _, look in dutch) else self.dutch
        nl = wrap(dutch, face, width)[-ROWS:]
        en = following(wrap(english, self.english, width))
        box_h = 2 * self.pad + len(nl) * self.dutch_h + (self.gap if nl and en else 0) + len(en) * self.english_h
        box_h += box_h % 2
        box_w = (self.width - 2 * self.margin) // 2 * 2
        layer = Image.new('RGBA', (box_w, box_h), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        d.rounded_rectangle((0, 0, box_w - 1, box_h - 1), round(14 * self.height / 720), fill=(8, 12, 16, 165))
        y = self.pad
        for row in nl:
            for word, look, x in row:
                d.text((self.pad + x, y), word, font=face, fill=LOOK[look][0])
            y += self.dutch_h
        y += self.gap if nl and en else 0
        space = self.english.getlength(' ')
        for row in en:
            for i, (word, look, x) in enumerate(row):
                if bg := LOOK[look][1]:
                    joined = i + 1 < len(row) and LOOK[row[i + 1][1]][1]
                    right = x + self.english.getlength(word) + (space if joined else 0)
                    d.rectangle((self.pad + x - 4, y + 4, self.pad + right + 4, y + self.english_h - 2), fill=bg)
            for word, look, x in row:
                d.text((self.pad + x, y), word, font=self.english, fill=LOOK[look][0])
            y += self.english_h
        top = (self.height - self.bottom - box_h) // 2 * 2
        left = (self.width - box_w) // 2 // 2 * 2
        return (top, left) + planes(np.asarray(layer))


def planes(rgba):
    """BT.601 limited-range I420 of an RGBA image, premultiplied by its alpha, with chroma averaged over 2x2."""
    rgb = rgba[..., :3].astype(np.float32)
    a = rgba[..., 3].astype(np.float32) / 255
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    y = 16 + 0.257 * r + 0.504 * g + 0.098 * b
    u = 128 - 0.148 * r - 0.291 * g + 0.439 * b
    v = 128 + 0.439 * r - 0.368 * g - 0.071 * b

    def half(x):
        return (x[0::2, 0::2] + x[1::2, 0::2] + x[0::2, 1::2] + x[1::2, 1::2]) / 4

    return y * a, 1 - a, half(u * a), half(v * a), 1 - half(a)
