"""お試し用アドオン (サンプル・ショーケース) の平面スクリーンに入れる見本の映像を作る。

絵も音もすべてこのスクリプトで作る (他人の作品・Mojang のキャラクターは使わない)。ブロックの画面 (128×72) で
見栄えがするよう、大きな形・鮮やかな色・なめらかな動きにしている:

    0〜5 秒   夕焼け: 沈む太陽、流れる雲、奥行きのある丘、オレンジ色の猫が下から顔を出す
    5〜10 秒  夜: 星がまたたき、花火が上がる (色の多さを見せる)
    10〜15 秒 昼: 虹と青空、タイトルの文字が流れる

音は自作の短い曲 (矩形波のメロディー・ベース・ノイズの打楽器)。

    python scripts/make_demo_video.py out.mp4 [--width 384 --height 216 --fps 30]
"""
import argparse
import subprocess
import tempfile
import wave
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

DURATION = 15.0
SAMPLE_RATE = 44100
FONT_CANDIDATES = ["C:/Windows/Fonts/ariblk.ttf", "C:/Windows/Fonts/arialbd.ttf",
                   "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/System/Library/Fonts/Helvetica.ttc"]


def smooth(t):
    t = np.clip(t, 0.0, 1.0)
    return t * t * (3 - 2 * t)


def mix(a, b, t):
    return np.asarray(a, np.float32) * (1 - t) + np.asarray(b, np.float32) * t


def load_font(size):
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


class Scene:
    def __init__(self, w, h, seed=3):
        self.w, self.h = w, h
        rng = np.random.default_rng(seed)
        self.yy, self.xx = np.mgrid[0:h, 0:w].astype(np.float32)
        self.stars = np.c_[rng.uniform(0, w, 70), rng.uniform(0, h * 0.6, 70), rng.uniform(0, 2 * np.pi, 70)]
        self.clouds = np.c_[rng.uniform(0, w, 6), rng.uniform(h * 0.08, h * 0.35, 6), rng.uniform(0.6, 1.3, 6)]
        # 花火: (打ち上げ時刻, x, y, 色)
        palette = [(255, 80, 80), (255, 210, 60), (80, 220, 120), (90, 160, 255), (230, 110, 255), (255, 150, 60)]
        self.fireworks = [(5.2 + 0.5 * i, rng.uniform(0.45, 0.85) * w, rng.uniform(0.15, 0.38) * h, palette[i % 6],
                           np.linspace(0, 2 * np.pi, 24, endpoint=False) + rng.uniform(0, 1)) for i in range(7)]
        # タイトルが画面の幅の 9 割に収まる大きさ
        size = int(h * 0.3)
        while size > 8 and ImageDraw.Draw(Image.new("RGB", (1, 1))).textlength("BLOCK VIDEO PLAYER", font=load_font(size)) > w * 0.9:
            size -= 1
        self.font = load_font(size)

    # ------------------------------------------------------------ 空
    def sky(self, t):
        h = self.h
        y = self.yy / h
        sunset = (mix([70, 40, 150], [255, 120, 70], y[..., None]))
        night = (mix([8, 10, 40], [40, 30, 90], y[..., None]))
        day = (mix([60, 150, 255], [180, 225, 255], y[..., None]))
        a = smooth((t - 4.0) / 1.2)
        b = smooth((t - 9.2) / 1.2)
        img = sunset * (1 - a) + night * a
        img = img * (1 - b) + day * b
        return img

    def sun(self, img, t):
        if t > 5.5:
            return
        cx = self.w * 0.68
        cy = self.h * (0.42 + 0.06 * t)
        r = self.h * 0.16
        d = np.hypot(self.xx - cx, self.yy - cy)
        glow = np.clip(1 - (d - r) / (r * 1.4), 0, 1)[..., None] * 0.5 * (1 - smooth((t - 4.0) / 1.2))
        img[:] = img * (1 - glow) + np.array([255, 200, 90]) * glow
        img[d <= r] = mix([255, 230, 120], [255, 170, 80], smooth(t / 5))

    def moon_and_stars(self, img, t):
        a = smooth((t - 4.4) / 1.0) * (1 - smooth((t - 9.2) / 0.8))
        if a <= 0:
            return
        for x, y, phase in self.stars:
            tw = 0.5 + 0.5 * np.sin(t * 6 + phase)
            xi, yi = int(x), int(y)
            img[yi, xi] = mix(img[yi, xi], [255, 255, 230], a * (0.5 + 0.5 * tw))
        cx, cy, r = self.w * 0.2, self.h * 0.22, self.h * 0.09
        d = np.hypot(self.xx - cx, self.yy - cy)
        cut = np.hypot(self.xx - cx - r * 0.45, self.yy - cy + r * 0.2) > r * 0.85
        moon = (d <= r) & cut
        img[moon] = mix(img[moon], [250, 245, 210], a)

    def clouds_layer(self, img, t):
        a = 1 - smooth((t - 4.4) / 1.0) * (1 - smooth((t - 9.2) / 0.8))
        colour = mix([255, 190, 170], [255, 255, 255], smooth((t - 9.2) / 1.0))
        for x0, y0, s in self.clouds:
            x = (x0 + t * 14 * s) % (self.w + 80) - 40
            for dx, dy, rr in ((0, 0, 16), (14, 4, 12), (-14, 5, 11), (6, -6, 11)):
                m = np.hypot(self.xx - x - dx * s, (self.yy - y0 - dy * s) * 1.4) <= rr * s
                img[m] = mix(img[m], colour, 0.85 * a)

    def hills(self, img, t):
        night = smooth((t - 4.4) / 1.0) * (1 - smooth((t - 9.2) / 0.8))
        layers = [(0.62, 0.05, 9, [120, 70, 140], [30, 30, 70], [110, 190, 120]),
                  (0.72, 0.08, 18, [80, 50, 110], [20, 25, 55], [70, 160, 80]),
                  (0.84, 0.05, 32, [50, 35, 80], [12, 15, 35], [50, 130, 60])]
        for base, amp, speed, dusk, dark, green in layers:
            colour = mix(dusk, dark, night)
            colour = mix(colour, green, smooth((t - 9.2) / 1.0))
            x = self.xx[0] + t * speed
            ridge = self.h * (base - amp * (np.sin(x / 37.0) * 0.6 + np.sin(x / 13.0 + 1.3) * 0.4))
            img[self.yy >= ridge[None, :]] = colour

    def rainbow(self, img, t):
        a = smooth((t - 10.0) / 1.0)
        if a <= 0:
            return
        cx, cy = self.w * 0.5, self.h * 1.05
        d = np.hypot(self.xx - cx, self.yy - cy)
        bands = [(255, 60, 60), (255, 150, 40), (255, 230, 60), (80, 210, 90), (70, 140, 255), (150, 90, 230)]
        r0 = self.h * 0.78
        width = self.h * 0.045
        for i, colour in enumerate(bands):
            m = (d >= r0 - (i + 1) * width) & (d < r0 - i * width) & (self.yy < self.h * 0.8)
            img[m] = mix(img[m], colour, 0.85 * a)

    def fireworks_layer(self, img, t):
        for start, x, y, colour, angles in self.fireworks:
            age = t - start
            if age < 0 or age > 1.6:
                continue
            if age < 0.4:  # 打ち上げ
                py = self.h * 0.9 - (self.h * 0.9 - y) * (age / 0.4)
                m = np.hypot(self.xx - x, self.yy - py) <= 2.2
                img[m] = [255, 240, 200]
                continue
            k = (age - 0.4) / 1.2
            radius = self.h * 0.26 * np.sqrt(k)
            fade = 1 - k * k
            glow = np.clip(1 - np.hypot(self.xx - x, self.yy - y) / (radius + 6), 0, 1)[..., None] * 0.35 * fade
            img[:] = img * (1 - glow) + np.array(colour) * glow
            for ring in (1.0, 0.6):
                for a in angles:
                    px, py = x + np.cos(a) * radius * ring, y + np.sin(a) * radius * ring + 12 * k * k
                    m = np.hypot(self.xx - px, self.yy - py) <= 4.0 * (1.2 - k * 0.6)
                    img[m] = mix(img[m], mix(colour, [255, 255, 255], 0.3 * (1 - ring)), fade)

    # ------------------------------------------------------------ 猫 (自作のドット絵、正面の顔)
    def cat(self, img, t):
        h = self.h
        size = h * 0.42
        rise = smooth((t - 0.8) / 1.2) - smooth((t - 13.4) / 1.0)
        cx = self.w * 0.32 + np.sin(t * 0.8) * self.w * 0.03
        cy = h + size * 0.55 - rise * size * 1.05 + np.abs(np.sin(t * 3.2)) * -h * 0.02
        X = (self.xx - cx) / size
        Y = (self.yy - cy) / size
        orange, stripe, cream, pink = [250, 150, 50], [205, 95, 30], [255, 240, 225], [255, 170, 190]
        face = (X / 0.55) ** 2 + (Y / 0.45) ** 2 <= 1
        for s in (-1, 1):
            ear = (Y < -0.2) & (Y > -0.72) & (np.abs(X - s * 0.32) <= (Y + 0.72) * 0.6)
            inner = (Y < -0.28) & (Y > -0.62) & (np.abs(X - s * 0.32) <= (Y + 0.62) * 0.35)
            img[ear] = orange
            img[inner] = pink
        img[face] = orange
        for dx in (-0.1, 0.0, 0.1):
            img[face & (np.abs(X - dx) < 0.025) & (Y < -0.18)] = stripe
        img[((X / 0.24) ** 2 + ((Y - 0.2) / 0.17) ** 2 <= 1)] = cream
        blink = (t % 3.7) < 0.15 or (12.0 < t < 12.4)
        for s in (-1, 1):
            ex = s * 0.2
            if blink:
                img[(np.abs(X - ex) < 0.08) & (np.abs(Y + 0.02) < 0.02)] = [30, 30, 40]
            else:
                eye = ((X - ex) / 0.06) ** 2 + ((Y + 0.02) / 0.1) ** 2 <= 1
                img[eye] = [30, 30, 40]
                img[eye & (X - ex < -0.01) & (Y + 0.02 < -0.04)] = [255, 255, 255]
        img[(np.abs(X) < 0.05) & (np.abs(Y - 0.1) < 0.03)] = [240, 100, 120]

    def title(self, img, t):
        a = smooth((t - 10.6) / 0.6)
        if a <= 0:
            return
        layer = Image.new("RGBA", (self.w, self.h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)
        text = "BLOCK VIDEO PLAYER"
        tw = draw.textlength(text, font=self.font)
        # 右から流れてきて、真ん中で止まる
        x = max(self.w - (t - 10.6) * self.w * 0.9, (self.w - tw) / 2)
        y = self.h * 0.05
        draw.text((x + 3, y + 3), text, font=self.font, fill=(30, 40, 90, int(255 * a)))
        draw.text((x, y), text, font=self.font, fill=(255, 255, 255, int(255 * a)))
        rgba = np.asarray(layer, np.float32) / 255
        img[:] = img * (1 - rgba[..., 3:]) + rgba[..., :3] * 255 * rgba[..., 3:]

    def frame(self, t):
        img = self.sky(t)
        self.moon_and_stars(img, t)
        self.sun(img, t)
        self.rainbow(img, t)
        self.clouds_layer(img, t)
        self.fireworks_layer(img, t)
        self.hills(img, t)
        self.cat(img, t)
        self.title(img, t)
        return np.clip(img, 0, 255).astype(np.uint8)


def music(duration=DURATION, rate=SAMPLE_RATE):
    """自作の短い曲: ペンタトニックのメロディー (矩形波)・ベース (三角波)・ハイハット (ノイズ)。120 BPM。"""
    n = int(duration * rate)
    t = np.arange(n) / rate
    out = np.zeros(n, np.float32)
    beat = 0.25  # 16 分音符 (120 BPM)
    scale = [0, 2, 4, 7, 9, 12, 14, 16]
    chords = [0, -3, 5, 7]  # C, Am, F, G (の根音)
    melody = [0, 2, 4, 2, 5, 4, 2, 1, 0, 2, 4, 5, 7, 5, 4, 2]
    rng = np.random.default_rng(1)

    def note(freq, start, length, wave_kind, volume):
        a, b = int(start * rate), min(n, int((start + length) * rate))
        if a >= n:
            return
        tt = t[a:b] - start
        if wave_kind == "square":
            w = np.sign(np.sin(2 * np.pi * freq * tt)) * 0.5
        else:
            w = 2 * np.abs(2 * ((freq * tt) % 1) - 1) - 1
        env = np.minimum(1, tt / 0.01) * np.exp(-tt * 3.0)
        out[a:b] += w * env * volume

    base = 261.63
    steps = int(duration / beat)
    for i in range(steps):
        start = i * beat
        bar = (i // 16) % 4
        if i % 2 == 0:
            degree = scale[melody[(i // 2) % 16]]
            note(base * 2 ** ((degree + chords[bar] * (bar % 2)) / 12), start, beat * 1.8, "square", 0.10)
        if i % 4 == 0:
            note(base / 2 * 2 ** (chords[bar] / 12), start, beat * 3.5, "triangle", 0.22)
        if i % 2 == 1:
            a, b = int(start * rate), min(n, int((start + 0.05) * rate))
            out[a:b] += rng.uniform(-1, 1, b - a) * np.exp(-np.arange(b - a) / rate * 60) * 0.05
    fade = np.minimum(1, np.minimum(t / 0.3, (duration - t) / 1.0))
    return np.clip(out * fade, -1, 1)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("output")
    parser.add_argument("--width", type=int, default=384)
    parser.add_argument("--height", type=int, default=216)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--ffmpeg", default="ffmpeg")
    args = parser.parse_args(argv)
    scene = Scene(args.width, args.height)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        for i in range(int(DURATION * args.fps)):
            Image.fromarray(scene.frame(i / args.fps)).save(tmp / f"f_{i:04d}.png")
        audio = (music() * 32767).astype(np.int16)
        with wave.open(str(tmp / "music.wav"), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(audio.tobytes())
        subprocess.run([args.ffmpeg, "-v", "error", "-y", "-framerate", str(args.fps), "-i", str(tmp / "f_%04d.png"),
                        "-i", str(tmp / "music.wav"), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "16",
                        "-c:a", "aac", "-shortest", args.output], check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
