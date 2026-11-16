"""ブラウザ版のページ (web/) で見せる見本を作る: 最初の画面の「元の映像」と「この変換ツールの本物の出力」、設定ごとの出力の見本。

見本の映像はこのスクリプトが描く (夕方の湖と気球。4 秒で元に戻る。素材の権利の心配が無い)。それを実際の convert.py に通し、
出来た FRAME_DATA をそのまま web/media/ に置く。ページはそれをゲームと同じ codec.js で復元して描く (web/ui.js)。
つまりページに出るブロックの絵は「似せた絵」ではなく、この版の変換の出力そのもの。

    python scripts/make_web_media.py            # web/media/ を作り直す (ffmpeg が要る)

出来るもの (web/media/):
    hero.mp4              元の映像 (640×360、30 fps、4 秒、音声なし)
    hero_poster.jpg       その最初のコマ (読み込む前に出す)
    hero_blocks.json      { spec: mvcodec.preview.preview_spec, frameData: FRAME_DATA, settings }  128×72・10 fps・既定の設定
    hero_blocks.png       その最初のコマ (読み込む前に出す)
    showcase/<大きさ>_<パレット>_<ディザ>.png   1 コマを設定を変えて変換した出力 (1 マス = 1 画素)
    showcase.json         その一覧 (大きさ・ブロック数・使った色の数)

変換の設定を変えた・減色を直したときは、動かし直して見本を今の出力に合わせる (tests/test_web_ui.py が一覧とファイルの対応を確かめる)。
"""
import argparse
import base64
import json
import math
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mvcodec.decode import iter_decoded_frames  # noqa: E402
from mvcodec.preview import preview_spec  # noqa: E402

WIDTH, HEIGHT = 640, 360
SOURCE_FPS, SECONDS = 30, 4
BLOCK_FPS = 10
HERO_SIZE = (128, 72)
# 設定ごとの見本 (ページの「設定でどう変わるか」)。値は web/index.html の選択肢と同じ名前
SHOWCASE_SIZES = [(64, 36), (128, 72), (256, 144)]
SHOWCASE_PALETTES = ["auto", "concrete"]
SHOWCASE_DITHERS = ["blue_noise", "floyd", "none"]
SHOWCASE_FRAME = 0  # 何コマ目を見本にするか (元の映像の 10 fps で数える)


def _mix(a, b, t):
    return a + (b - a) * t


def _smooth(edge0, edge1, x):
    t = np.clip((x - edge0) / (edge1 - edge0), 0, 1)
    return t * t * (3 - 2 * t)


def _gradient(v, stops):
    """v (0〜1) → 色。stops は [(位置, (r, g, b)), …]。"""
    out = np.zeros(v.shape + (3,), np.float32)
    for (p0, c0), (p1, c1) in zip(stops, stops[1:]):
        t = np.clip((v - p0) / (p1 - p0), 0, 1)[..., None]
        seg = ((v >= p0) & (v <= p1))[..., None] if p0 > stops[0][0] else (v <= p1)[..., None]
        out = np.where(seg, _mix(np.array(c0, np.float32), np.array(c1, np.float32), t), out)
    out = np.where((v > stops[-1][0])[..., None], np.array(stops[-1][1], np.float32), out)
    return out


_RNG = np.random.default_rng(7)
_STARS = [(float(x), float(y), float(p), float(s)) for x, y, p, s in zip(
    _RNG.uniform(0.02, 0.98, 46), _RNG.uniform(0.02, 0.36, 46), _RNG.uniform(0, 1, 46), _RNG.uniform(0.5, 1.0, 46))]
_CLOUDS = [(0.16, 0.335, 0.105, 0.014, 0.0), (0.24, 0.352, 0.07, 0.010, 0.3), (0.80, 0.25, 0.12, 0.013, 0.55), (0.90, 0.268, 0.06, 0.009, 0.8),
           (0.47, 0.405, 0.09, 0.009, 0.15)]


def _balloon(scale=6):
    """気球の絵 (RGBA)。大きく描いて縮める。"""
    w, h = 46 * scale, 66 * scale
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cx, r = w / 2, 21 * scale
    stripes = [(47, 107, 255), (240, 240, 250), (138, 92, 255), (240, 240, 250), (47, 107, 255)]
    env = Image.new("L", (w, h), 0)
    ImageDraw.Draw(env).ellipse((cx - r, 1 * scale, cx + r, 1 * scale + 2 * r * 1.08), fill=255)
    ImageDraw.Draw(env).polygon([(cx - r * 0.62, 34 * scale), (cx + r * 0.62, 34 * scale), (cx + 5 * scale, 49 * scale), (cx - 5 * scale, 49 * scale)], fill=255)
    band = Image.new("RGBA", (w, h))
    bd = ImageDraw.Draw(band)
    for i, colour in enumerate(stripes):
        x0 = cx - r + i * (2 * r / len(stripes))
        bd.rectangle((x0, 0, x0 + 2 * r / len(stripes) + 1, h), fill=colour + (255,))
    img.paste(band, (0, 0), env)
    # 右側を少し暗く (丸み)
    shade = Image.new("L", (w, h), 0)
    ImageDraw.Draw(shade).ellipse((cx - r * 0.2, 0, cx + r * 2.2, 2.6 * r), fill=70)
    img.paste(Image.new("RGBA", (w, h), (20, 12, 50, 255)), (0, 0), Image.composite(shade, Image.new("L", (w, h), 0), env))
    d.line((cx - 5 * scale, 49 * scale, cx - 4 * scale, 57 * scale), fill=(30, 22, 50, 255), width=scale)
    d.line((cx + 5 * scale, 49 * scale, cx + 4 * scale, 57 * scale), fill=(30, 22, 50, 255), width=scale)
    d.rounded_rectangle((cx - 5 * scale, 57 * scale, cx + 5 * scale, 64 * scale), radius=scale, fill=(62, 38, 30, 255))
    return img.resize((w // scale, h // scale), Image.LANCZOS)


_BALLOON = _balloon()


def render(t):
    """t 秒 (0〜SECONDS で元に戻る) の絵を RGB の配列で返す。"""
    phase = 2 * math.pi * t / SECONDS
    y, x = np.mgrid[0:HEIGHT, 0:WIDTH].astype(np.float32)
    u, v = (x + 0.5) / WIDTH, (y + 0.5) / HEIGHT
    horizon = 0.66
    sky = _gradient(v / horizon, [(0.0, (40, 42, 128)), (0.34, (72, 40, 140)), (0.6, (112, 44, 140)), (0.8, (226, 86, 124)),
                                  (0.93, (255, 150, 96)), (1.0, (255, 196, 120))])
    img = sky
    # 星 (またたく)
    for sx, sy, sp, ss in _STARS:
        tw = 0.55 + 0.45 * math.sin(phase * 2 + sp * 2 * math.pi)
        d2 = ((u - sx) * WIDTH) ** 2 + ((v - sy) * HEIGHT) ** 2
        img = img + (np.exp(-d2 / (0.9 * ss)) * 210 * tw * (1 - _smooth(0.2, 0.4, v)))[..., None]
    # 太陽 (少し上下する) と光
    sun_x, sun_y = 0.655, 0.525 + 0.012 * math.sin(phase)
    dist = np.hypot((u - sun_x) * WIDTH, (v - sun_y) * HEIGHT)
    radius = 0.118 * HEIGHT
    img = img + np.exp(-(dist / (radius * 2.6)) ** 2)[..., None] * np.array([120, 70, 40], np.float32)
    disc = 1 - _smooth(radius - 1.2, radius + 1.2, dist)
    img = _mix(img, np.array([255, 240, 196], np.float32), disc[..., None])
    # 雲 (ゆっくり左右に)
    for cx, cy, rw, rh, cp in _CLOUDS:
        ox = cx + 0.012 * math.sin(phase + cp * 2 * math.pi)
        body = np.exp(-(((u - ox) / rw) ** 2 + ((v - cy) / rh) ** 2) * 1.6)
        lit = _mix(np.array([92, 52, 128], np.float32), np.array([255, 148, 132], np.float32), _smooth(0.2, 0.46, v)[..., None])
        img = _mix(img, lit, (np.clip(body * 1.25, 0, 1) * 0.86)[..., None])

    def ridge(base, amp, freq, shift, sway, colour, haze):
        uu = u + sway * math.sin(phase)
        line = base + amp * (0.6 * np.sin(uu * freq + shift) + 0.3 * np.sin(uu * freq * 2.3 + shift * 1.7) + 0.1 * np.sin(uu * freq * 5.1))
        mask = _smooth(line - 0.6 / HEIGHT, line + 0.6 / HEIGHT, v)
        tint = _mix(np.array(colour, np.float32), np.array(haze, np.float32), np.clip((horizon - v) * 6, 0, 1)[..., None] * 0.35)
        return _mix(img, tint, mask[..., None])

    img = ridge(0.565, 0.050, 6.0, 0.8, 0.004, (96, 56, 140), (200, 110, 150))
    img = ridge(0.615, 0.040, 9.0, 2.1, 0.008, (58, 36, 104), (140, 80, 140))
    img = ridge(0.672, 0.026, 4.0, 4.0, 0.014, (44, 34, 110), (70, 46, 120))
    # 湖 (太陽の光がゆらぐ)
    water_top = 0.705
    depth = np.clip((v - water_top) / (1 - water_top), 0, 1)
    water = _gradient(depth, [(0.0, (84, 52, 140)), (0.35, (52, 46, 136)), (1.0, (38, 40, 120))])
    width = 0.035 + 0.11 * depth
    column = np.exp(-((u - sun_x) / width) ** 2)
    ripple = np.sin(v * 210 - phase * 3 + np.sin(u * 38 + phase) * 1.4) * 0.5 + 0.5
    ripple = _smooth(0.52, 0.9, ripple)
    glint = column * ripple * (1 - 0.55 * depth)
    water = water + glint[..., None] * np.array([230, 150, 110], np.float32)
    far = np.sin(v * 150 + phase * 2 + u * 9) * 0.5 + 0.5
    water = water + (_smooth(0.75, 1.0, far) * (1 - column) * 16 * (1 - depth))[..., None]
    img = _mix(img, water, _smooth(water_top - 0.7 / HEIGHT, water_top + 0.7 / HEIGHT, v)[..., None])
    frame = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8), "RGB")
    # 気球 (ふわふわ上下する)
    bx = int(WIDTH * (0.27 + 0.006 * math.cos(phase)) - _BALLOON.width / 2)
    by = int(HEIGHT * (0.215 + 0.022 * math.sin(phase)) - _BALLOON.height / 2)
    frame.paste(_BALLOON, (bx, by), _BALLOON)
    return frame


def convert(frames_dir, output, size, palette="auto", dither="blue_noise"):
    """ブラウザ版の既定と同じ引数で convert.py を動かし、FRAME_DATA (dict) を返す。"""
    args = [sys.executable, str(ROOT / "convert.py"), "--frames-dir", str(frames_dir), "--output", str(output),
            "--width", str(size[0]), "--height", str(size[1]), "--fps", str(BLOCK_FPS), "--palette", palette,
            "--dither-method", dither, "--device", "cpu", "--keyframe-interval", "30", "--threads", "1", "--mode", "screen",
            "--depth", "16", "--lang", "en"]
    subprocess.run(args, check=True, stdout=subprocess.DEVNULL)
    text = Path(output).read_text(encoding="utf-8")
    return json.loads(text[text.index("{"):text.rindex("}") + 1])


def first_frame(data):
    for _index, rgb in iter_decoded_frames(data):
        return np.asarray(rgb, np.uint8)
    raise ValueError("no frames")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default=str(ROOT / "web" / "media"))
    parser.add_argument("--keep", help="途中のコマを残すフォルダ (確認用)")
    args = parser.parse_args(argv)
    out = Path(args.out)
    (out / "showcase").mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(args.keep) if args.keep else Path(tmp)
        full, blocks, single = tmp / "full", tmp / "blocks", tmp / "single"
        for folder in (full, blocks, single):
            folder.mkdir(parents=True, exist_ok=True)
        step = SOURCE_FPS // BLOCK_FPS
        for n in range(SOURCE_FPS * SECONDS):
            frame = render(n / SOURCE_FPS)
            frame.save(full / f"frame_{n:04d}.png")
            if n % step == 0:
                frame.save(blocks / f"frame_{n // step:04d}.png")
        render(SHOWCASE_FRAME / BLOCK_FPS).save(single / "frame_0000.png")
        # 元の映像 (小さく、どのブラウザでも再生できる H.264)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-framerate", str(SOURCE_FPS), "-i", str(full / "frame_%04d.png"),
                        "-c:v", "libx264", "-preset", "veryslow", "-crf", "23", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                        "-an", str(out / "hero.mp4")], check=True)
        Image.open(full / "frame_0000.png").save(out / "hero_poster.jpg", quality=82, optimize=True, progressive=True)
        # この変換ツールの出力 (既定の設定)
        data = convert(blocks, tmp / "hero.js", HERO_SIZE)
        spec = preview_spec(data)
        spec["top"] = base64.b64encode(spec["top"]).decode("ascii")
        settings = {"width": HERO_SIZE[0], "height": HERO_SIZE[1], "fps": BLOCK_FPS, "palette": "auto", "dither": "blue_noise"}
        (out / "hero_blocks.json").write_text(json.dumps({"settings": settings, "spec": spec, "frameData": data},
                                                         ensure_ascii=False, separators=(",", ":")), encoding="utf-8", newline="\n")
        Image.fromarray(first_frame(data), "RGB").save(out / "hero_blocks.png", optimize=True)
        # 設定ごとの見本
        items = []
        for size in SHOWCASE_SIZES:
            for palette in SHOWCASE_PALETTES:
                for dither in SHOWCASE_DITHERS:
                    rgb = first_frame(convert(single, tmp / "one.js", size, palette, dither))
                    name = f"{size[0]}x{size[1]}_{palette}_{dither}.png"
                    Image.fromarray(rgb, "RGB").save(out / "showcase" / name, optimize=True)
                    colours = len(np.unique(rgb.reshape(-1, 3), axis=0))
                    items.append({"width": size[0], "height": size[1], "palette": palette, "dither": dither, "file": name,
                                  "blocks": size[0] * size[1], "colours": int(colours)})
                    print(name, colours, "colours")
        (out / "showcase.json").write_text(json.dumps({"items": items}, indent=1) + "\n", encoding="utf-8", newline="\n")
    total = sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
    print(f"{out}: {total / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
