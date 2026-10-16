#!/usr/bin/env python3
"""Render docs/demo/demo.cast (written by record.py) to docs/demo/demo.gif.

A tiny terminal model: printable text, CR/LF, line wrap, scrolling and a few SGR
colours (the prompt). Needs Pillow and a monospace TTF (DejaVu Sans Mono by default,
override with LNT_DEMO_FONT). Dev-only; lnt itself has no dependencies.
"""
import json
import os
import re
import sys
import tempfile

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
FONT_PATHS = [os.environ.get("LNT_DEMO_FONT", ""), "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
              "/Library/Fonts/DejaVuSansMono.ttf", "/System/Library/Fonts/Menlo.ttc"]
BG, FG, GREEN, DIM = (24, 26, 33), (220, 223, 228), (120, 200, 120), (130, 136, 150)
SGR = re.compile(r"\x1b\[([0-9;]*)m")


class Term:
    def __init__(self, cols, rows):
        self.cols, self.rows = cols, rows
        self.lines = [[]]
        self.x = 0
        self.color = FG

    def feed(self, text):
        pos = 0
        for m in SGR.finditer(text):
            self._text(text[pos:m.start()])
            code = m.group(1) or "0"
            self.color = {"0": FG, "32": GREEN, "2": DIM}.get(code, self.color)
            pos = m.end()
        self._text(text[pos:])

    def _text(self, s):
        for ch in s:
            if ch == "\r":
                self.x = 0
            elif ch == "\n":
                self.lines.append([])
                self.x = 0
            else:
                if self.x >= self.cols:
                    self.lines.append([])
                    self.x = 0
                line = self.lines[-1]
                while len(line) < self.x:
                    line.append((" ", FG))
                if self.x < len(line):
                    line[self.x] = (ch, self.color)
                else:
                    line.append((ch, self.color))
                self.x += 1
        self.lines = self.lines[-self.rows:]


def main():
    cast = os.path.join(HERE, "demo.cast")
    with open(cast, encoding="utf-8") as fh:
        header = json.loads(fh.readline())
        events = [json.loads(line) for line in fh if line.strip()]
    font_path = next(p for p in FONT_PATHS if p and os.path.exists(p))
    font = ImageFont.truetype(font_path, 14)
    cw = font.getlength("M")
    ch = 18
    cols, rows = header["width"], header["height"]
    pad = 16
    W, H = int(cols * cw + 0.999) + 2 * pad, rows * ch + 2 * pad + 22
    term = Term(cols, rows)

    def frame():
        img = Image.new("RGB", (W, H), BG)
        d = ImageDraw.Draw(img)
        for i, c in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
            d.ellipse([pad + i * 18, 8, pad + i * 18 + 10, 18], fill=c)
        d.text((W // 2 - 90, 5), header.get("title", ""), font=font, fill=DIM)
        for r, line in enumerate(term.lines):
            y = pad + 22 + r * ch
            run, run_color, x0 = "", None, 0
            for x, (c, color) in enumerate(line + [(" ", None)]):
                if color != run_color and run:
                    d.text((pad + round(x0 * cw), y), run, font=font, fill=run_color)
                    run, x0 = "", x
                if not run:
                    x0, run_color = x, color
                run += c
        return img.quantize(colors=16, method=Image.Quantize.MEDIANCUT)

    frames, durations = [], []
    i = 0
    while i < len(events):
        t = events[i][0]
        # group events that land within 60 ms into one frame
        while i < len(events) and events[i][0] - t < 0.06:
            term.feed(events[i][2])
            i += 1
        nxt = events[i][0] if i < len(events) else t + 4.0
        frames.append(frame())
        durations.append(max(40, int((nxt - t) * 1000)))
    out = os.path.join(HERE, "demo.gif")
    frames[0].save(out, save_all=True, append_images=frames[1:], duration=durations, loop=0, optimize=True)
    # stills for checking the render by eye
    if "--stills" in sys.argv:
        for k in (len(frames) // 3, len(frames) // 2, len(frames) - 1):
            path = os.path.join(tempfile.gettempdir(), "lnt-demo-still-%d.png" % k)
            frames[k].convert("RGB").save(path)
            print("still:", path)
    print("wrote %s: %d frames, %.1fs, %dx%d, %d KB" % (out, len(frames), sum(durations) / 1000, W, H,
                                                        os.path.getsize(out) // 1024))


if __name__ == "__main__":
    main()
