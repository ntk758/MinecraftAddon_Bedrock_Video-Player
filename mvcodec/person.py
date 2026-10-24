"""動画から人物を切り抜く (Phase 12.3: 動画の人物をだまし絵にする)。

AI の切り抜きモデル (torchvision の DeepLabV3 MobileNetV3-Large、BSD-3、約 42 MB、初回にダウンロード) で各フレームの
人物の範囲を求め、次の 2 本の動画を作る:
  - 人物の範囲の動画 (白 = 人物): convert.py の --mask-video に渡すと、その形をふくらませた立体 (inflate) になる
  - 人物を消した背景の動画: だまし絵の後ろの壁に流す (--stage-background)。消した所は、同じ場面で人物がいなかった
    ときの色 (カメラが止まっている映像) か、まわりの色をぼかして埋める

    python scripts/make_person_videos.py in.mp4 mask.mp4 background.mp4 --width 160 --height 90 --fps 10
"""
import subprocess

import numpy as np
from PIL import Image, ImageFilter

# DeepLabV3 (Pascal VOC の 21 種類) の「人」の番号
PERSON_CLASS = 15
# 切り抜きの計算に使う画像の短い辺 (大きいほど細かいが遅い)
SEGMENT_SHORT_SIDE = 320
# 人物の範囲の時間方向のならし (前のフレームとの指数移動平均の重み。ちらつき (置き直し) を減らす)
MASK_SMOOTHING = 0.4
# 場面の切り替わりとみなす、前のフレームとの平均の色の差 (0〜255)。切り替わったら背景の記憶を捨てる
SCENE_CUT_DIFF = 40.0
# 背景の記憶を新しい色へ寄せる重み
BACKGROUND_LEARNING = 0.2


class PersonSegmenter:
    """1 枚の画像 (高さ × 幅 × 3 の uint8) から、人物である確からしさ (0〜1、同じ大きさ) を返す。"""

    def __init__(self, device="cpu"):
        import torch
        from torchvision.models.segmentation import (DeepLabV3_MobileNet_V3_Large_Weights,
                                                     deeplabv3_mobilenet_v3_large)
        self.torch = torch
        self.device = torch.device(device)
        weights = DeepLabV3_MobileNet_V3_Large_Weights.DEFAULT
        self.model = deeplabv3_mobilenet_v3_large(weights=weights).eval().to(self.device)
        self.mean = torch.tensor([0.485, 0.456, 0.406]).view(1, 3, 1, 1)
        self.std = torch.tensor([0.229, 0.224, 0.225]).view(1, 3, 1, 1)

    def __call__(self, rgb):
        torch = self.torch
        h, w, _ = rgb.shape
        scale = SEGMENT_SHORT_SIDE / min(h, w)
        size = (max(1, round(w * scale)), max(1, round(h * scale)))
        small = np.asarray(Image.fromarray(rgb).resize(size, Image.Resampling.BILINEAR), np.float32) / 255
        x = (torch.from_numpy(small).permute(2, 0, 1)[None] - self.mean) / self.std
        with torch.no_grad():
            logits = self.model(x.to(self.device))["out"].float().cpu()
        prob = torch.softmax(logits, dim=1)[0, PERSON_CLASS].numpy()
        return np.asarray(Image.fromarray(prob).resize((w, h), Image.Resampling.BILINEAR), np.float32)


def _box_blur(a, size, axis):
    """axis の方向に幅 size の箱の平均をとる (端の外は 0)。"""
    a = np.moveaxis(a, axis, 0)
    padded = np.concatenate([np.zeros((size,) + a.shape[1:], a.dtype), a, np.zeros((size,) + a.shape[1:], a.dtype)])
    total = np.cumsum(padded, axis=0)
    half = size // 2
    n = a.shape[0]
    out = (total[size + half: size + half + n] - total[half: half + n]) / size
    return np.moveaxis(out, 0, axis)


def _blur(a, radius):
    """箱の平均を縦横 3 回ずつかけた、ガウスに近いぼかし (float のまま)。"""
    size = max(1, int(radius))
    for _ in range(3):
        for axis in (0, 1):
            a = _box_blur(a, size, axis)
    return a


def fill_masked(rgb, hole, radius):
    """hole (真の画素) を、まわりの色をぼかして埋める (穴の外の色だけの重み付きの平均)。大きな穴は、ぼかしの半径を
    倍にしながら、外の色が届くまで繰り返す。"""
    out = rgb.astype(np.float32)
    remaining = hole.copy()
    known = (~hole).astype(np.float32)
    colour = rgb.astype(np.float32) * known[..., None]
    limit = 2 * max(rgb.shape[:2])
    while remaining.any() and radius <= limit:
        weight = _blur(known, radius)
        reached = remaining & (weight > 0.02)
        blurred = _blur(colour, radius)
        out[reached] = blurred[reached] / weight[reached][:, None]
        remaining &= ~reached
        radius *= 2
    return np.clip(out, 0, 255).astype(np.uint8)


class PersonSplitter:
    """フレームを順に受け取り、(人物の範囲 0〜1、人物を消した背景) を返す。"""

    def __init__(self, segmenter):
        self.segmenter = segmenter
        self.mask = None
        self.previous = None
        self.background = None   # 人物がいなかったときの色の記憶 (場面ごと)
        self.known = None        # 記憶のある画素

    def __call__(self, rgb):
        prob = self.segmenter(rgb)
        frame = rgb.astype(np.float32)
        cut = self.previous is None or np.abs(frame - self.previous).mean() > SCENE_CUT_DIFF
        if cut or self.mask is None:
            self.mask = prob
            self.background = frame.copy()
            self.known = np.zeros(prob.shape, bool)
        else:
            self.mask = MASK_SMOOTHING * self.mask + (1 - MASK_SMOOTHING) * prob
        self.previous = frame
        person = self.mask >= 0.5
        # 人物のまわりは少し広めに消す (縁の色が背景に残らないように)
        hole = np.asarray(Image.fromarray(person.astype(np.uint8) * 255).filter(ImageFilter.MaxFilter(5))) > 0
        visible = ~hole
        self.background[visible] = (1 - BACKGROUND_LEARNING) * self.background[visible] + BACKGROUND_LEARNING * frame[visible]
        self.background[visible & ~self.known] = frame[visible & ~self.known]
        self.known |= visible
        filled = fill_masked(rgb, hole, max(2, min(rgb.shape[:2]) // 8))
        background = np.where((hole & self.known)[..., None], self.background, filled).astype(np.uint8)
        return self.mask, background


def read_frames(path, width, height, fps, duration=None, ffmpeg="ffmpeg"):
    """動画を width × height (縦横比は保って余白を黒く) の RGB のフレームとして順に読む。"""
    vf = f"fps={fps},scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black"
    cmd = [ffmpeg, "-v", "error", "-i", str(path), "-vf", vf]
    if duration is not None:
        cmd += ["-t", str(duration)]
    cmd += ["-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, **_subprocess_kwargs())
    size = width * height * 3
    try:
        while True:
            raw = proc.stdout.read(size)
            if len(raw) < size:
                break
            yield np.frombuffer(raw, np.uint8).reshape(height, width, 3)
    finally:
        proc.stdout.close()
        proc.wait()


def _subprocess_kwargs():
    import os
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


def _writer(path, width, height, fps, ffmpeg, audio_from=None, duration=None):
    cmd = [ffmpeg, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}",
           "-r", str(fps), "-i", "-"]
    if audio_from is not None:
        cmd += ["-i", str(audio_from), "-map", "0:v", "-map", "1:a?", "-c:a", "aac", "-shortest"]
        if duration is not None:
            cmd += ["-t", str(duration)]
    # 色を正確に残す (人物の範囲は白黒、背景はブロックの色を選ぶ元になる)
    cmd += ["-c:v", "libx264", "-crf", "12", "-pix_fmt", "yuv444p", str(path)]
    return subprocess.Popen(cmd, stdin=subprocess.PIPE, **_subprocess_kwargs())


def make_person_videos(source, mask_path, background_path, width, height, fps, duration=None, ffmpeg="ffmpeg",
                       segmenter=None, progress=None):
    """source から、人物の範囲の動画 (mask_path) と、人物を消した背景の動画 (background_path、元の音声付き) を作る。
    戻り値はフレームの数。progress(i) をフレームごとに呼ぶ。"""
    segmenter = segmenter or PersonSegmenter()
    splitter = PersonSplitter(segmenter)
    mask_writer = _writer(mask_path, width, height, fps, ffmpeg)
    background_writer = _writer(background_path, width, height, fps, ffmpeg, audio_from=source, duration=duration)
    count = 0
    try:
        for frame in read_frames(source, width, height, fps, duration, ffmpeg):
            mask, background = splitter(frame)
            gray = (np.clip(mask, 0, 1) * 255).astype(np.uint8)
            mask_writer.stdin.write(np.repeat(gray[..., None], 3, axis=2).tobytes())
            background_writer.stdin.write(background.tobytes())
            count += 1
            if progress:
                progress(count)
    finally:
        for writer in (mask_writer, background_writer):
            writer.stdin.close()
            writer.wait()
    if count == 0:
        raise RuntimeError(f"動画を読めませんでした: {source}")
    return count
