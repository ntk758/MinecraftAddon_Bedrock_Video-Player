"""動画から人物を切り抜く (Phase 12.3: 動画の人物をだまし絵にする)。

AI の切り抜きモデルで各フレームの人物の範囲を求める。モデルは 2 種類:
  - photo: torchvision の DeepLabV3 MobileNetV3-Large (BSD-3、約 42 MB、初回にダウンロード)。写真で学習したので、
    実写向き。アニメ・3DCG では形が丸くつぶれ、強い光を人物と取り違える
  - anime: skytnt/anime-seg の ISNet (Apache-2.0、約 176 MB、初回にダウンロード。onnxruntime で動かし、
    DirectML / CUDA の GPU があれば使う)。アニメ・イラストの人物を、髪や手の先まで切り抜く
次の 2 本の動画を作る:
  - 人物の範囲の動画 (白 = 人物): convert.py の --mask-video に渡すと、その形をふくらませた立体 (inflate) になる
  - 人物を消した背景の動画: だまし絵の後ろの壁に流す (--stage-background)。消した所は、同じ場面で人物がいなかった
    ときの色 (カメラが止まっている映像) か、まわりの色をぼかして埋める

    python scripts/make_person_videos.py in.mp4 mask.mp4 background.mp4 --fps 10 [--model anime]
"""
import hashlib
import os
import subprocess
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

# DeepLabV3 (Pascal VOC の 21 種類) の「人」の番号
PERSON_CLASS = 15
# 切り抜きの計算に使う画像の短い辺 (大きいほど細かいが遅い)。アニメの MV で、320 では全身が小さく映る場面の人物を
# 見落とし、形も丸くつぶれた。520 で形が締まり、小さい人物も一部拾える (CPU で 1 枚約 0.2 秒、320 の 2 倍)
SEGMENT_SHORT_SIDE = 520
# 人物の範囲の時間方向のならし (前のフレームとの指数移動平均の重み。ちらつき (置き直し) を減らす)
MASK_SMOOTHING = 0.4
# 前のフレームとの確からしさの差が大きい画素ほど、ならす重みを減らす (差がこれ以上なら今のフレームの値そのもの)。
# 全部の画素を同じ重みでならすと、速く動く腕や脚に前のフレームの残像 (二重の影) が残った (実写のダンスで確認)。
# 差で重みを切り替えると境目が線になるので、重みは差に比例して減らす
MASK_FOLLOW = 0.3
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


# アニメ向けのモデル (skytnt/anime-seg、Apache-2.0)。入力は 1024 × 1024 の RGB (0〜1)、出力は人物の確からしさ
ANIME_MODEL_URL = "https://huggingface.co/skytnt/anime-seg/resolve/main/isnetis.onnx"
ANIME_MODEL_SHA256 = "f15622d853e8260172812b657053460e20806f04b9e05147d49af7bed31a6e99"
ANIME_MODEL_FILE = "isnetis.onnx"
ANIME_INPUT_SIZE = 1024
# アニメ向けのモデルが人物を見つけなかったとみなす、確からしさ 0.5 以上の画素の割合。3DCG の MV で強い照明が当たった
# 場面や、全身が小さく映った場面では何も返さないことがあるので、そのフレームだけ実写向きのモデルの結果を使う
# (利用者のアニメ MV で、アニメ向けだけでは 12 場面中 6 場面で人物が消えた)
ANIME_MIN_AREA = 0.005


def model_dir():
    """ダウンロードしたモデルを置くフォルダ (環境変数 BVP_MODEL_DIR で変えられる)。"""
    return Path(os.environ.get("BVP_MODEL_DIR") or Path.home() / ".cache" / "block-video-player")


def download_model(url, path, sha256, progress=None):
    """url を path にダウンロードし、SHA-256 を確かめる (違えば消してエラー)。progress(読んだバイト数, 全体) を呼ぶ。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".part")
    digest = hashlib.sha256()
    request = urllib.request.Request(url, headers={"User-Agent": "BlockVideoPlayer"})
    with urllib.request.urlopen(request, timeout=60) as response, open(partial, "wb") as out:
        total = int(response.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = response.read(1 << 20)
            if not chunk:
                break
            out.write(chunk)
            digest.update(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
    if digest.hexdigest() != sha256:
        partial.unlink()
        raise RuntimeError(f"ダウンロードしたモデルが壊れています (SHA-256 が違います): {url}")
    partial.replace(path)
    return path


class AnimeSegmenter:
    """アニメ・イラスト向けの切り抜き (skytnt/anime-seg の ISNet)。PersonSegmenter と同じく、画像から人物の確からしさを返す。

    画像は縦横比を保って 1024 × 1024 の真ん中に置き (余白は黒)、出力の同じ範囲を元の大きさに戻す。"""

    def __init__(self, model_path=None, providers=None, download_progress=None, fallback="auto"):
        """fallback: 人物を見つけなかったフレームに使う切り抜き (既定は PersonSegmenter を最初に要るときに作る。
        PyTorch が無ければ使わない)。None なら使わない。"""
        import onnxruntime as ort

        self.fallback = fallback

        path = Path(model_path) if model_path else model_dir() / ANIME_MODEL_FILE
        if not path.is_file():
            download_model(ANIME_MODEL_URL, path, ANIME_MODEL_SHA256, download_progress)
        if providers is None:
            available = ort.get_available_providers()
            providers = [p for p in ("DmlExecutionProvider", "CUDAExecutionProvider") if p in available]
            providers.append("CPUExecutionProvider")
        self.session = ort.InferenceSession(str(path), providers=providers)
        self.input_name = self.session.get_inputs()[0].name

    def __call__(self, rgb):
        h, w = rgb.shape[:2]
        size = ANIME_INPUT_SIZE
        scale = size / max(h, w)
        nh, nw = max(1, round(h * scale)), max(1, round(w * scale))
        top, left = (size - nh) // 2, (size - nw) // 2
        image = np.zeros((size, size, 3), np.float32)
        image[top:top + nh, left:left + nw] = np.asarray(
            Image.fromarray(rgb).resize((nw, nh), Image.Resampling.BILINEAR), np.float32) / 255
        mask = self.session.run(None, {self.input_name: image.transpose(2, 0, 1)[None]})[0]
        mask = np.asarray(mask, np.float32).reshape(size, size)[top:top + nh, left:left + nw]
        mask = np.asarray(Image.fromarray(mask).resize((w, h), Image.Resampling.BILINEAR), np.float32)
        if (mask >= 0.5).mean() < ANIME_MIN_AREA and self.fallback is not None:
            if self.fallback == "auto":
                try:
                    self.fallback = PersonSegmenter()
                except ImportError:
                    self.fallback = None
                    return mask
            return self.fallback(rgb)
        return mask


# 切り抜きのモデルの種類 → 作り方
SEGMENTERS = {"photo": PersonSegmenter, "anime": AnimeSegmenter}


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
            keep = MASK_SMOOTHING * np.clip(1 - np.abs(prob - self.mask) / MASK_FOLLOW, 0, 1)
            self.mask = keep * self.mask + (1 - keep) * prob
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


# 切り抜きと背景を作る大きさ (長い辺)。ブロックの大きさ (80 × 45 など) で切り抜くと形が粗く、背景も後ろの壁
# (200 × 120 など) へ引き伸ばされてぼやけるので、元の縦横比のままこの大きさで作る (元の方が小さければ元の大きさ)
ANALYSIS_LONG_SIDE = 640


def read_frames(path, fps, duration=None, ffmpeg="ffmpeg", long_side=ANALYSIS_LONG_SIDE):
    """動画を、元の縦横比のまま長い辺 long_side 以下 (幅・高さは偶数) の RGB のフレームとして順に読む。
    大きさは ffmpeg が書く PPM の見出しから読む (回転した動画も ffmpeg が向きを直す)。"""
    scale = (f"scale='if(gte(iw,ih),min({long_side},iw),-2)':'if(gte(iw,ih),-2,min({long_side},ih))'"
             ",scale=trunc(iw/2)*2:trunc(ih/2)*2")
    cmd = [ffmpeg, "-v", "error", "-i", str(path), "-vf", f"fps={fps},{scale}"]
    if duration is not None:
        cmd += ["-t", str(duration)]
    cmd += ["-f", "image2pipe", "-c:v", "ppm", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, **_subprocess_kwargs())
    try:
        while True:
            fields = []
            while len(fields) < 4:
                token = _read_token(proc.stdout)
                if token is None:
                    return
                fields.append(token)
            width, height = int(fields[1]), int(fields[2])
            raw = proc.stdout.read(width * height * 3)
            if len(raw) < width * height * 3:
                return
            yield np.frombuffer(raw, np.uint8).reshape(height, width, 3)
    finally:
        proc.stdout.close()
        proc.wait()


def _read_token(stream):
    """PPM の見出しの 1 語 (P6・幅・高さ・最大値)。最後の 1 語の後の空白 1 文字も読み捨てる。終わりなら None。"""
    token = b""
    while True:
        c = stream.read(1)
        if not c:
            return token.decode() if token else None
        if c.isspace():
            if token:
                return token.decode()
            continue
        token += c


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


def make_person_videos(source, mask_path, background_path, fps, duration=None, ffmpeg="ffmpeg", segmenter=None,
                       progress=None, long_side=ANALYSIS_LONG_SIDE):
    """source から、人物の範囲の動画 (mask_path) と、人物を消した背景の動画 (background_path、元の音声付き) を作る。
    どちらも元の縦横比で、長い辺 long_side 以下 (convert.py は人物の範囲を元の動画と同じように縮めるので位置がそろう)。
    戻り値はフレームの数。progress(i) をフレームごとに呼ぶ。"""
    segmenter = segmenter or PersonSegmenter()
    splitter = PersonSplitter(segmenter)
    writers = []
    count = 0
    try:
        for frame in read_frames(source, fps, duration, ffmpeg, long_side):
            if not writers:
                height, width = frame.shape[:2]
                writers = [_writer(mask_path, width, height, fps, ffmpeg),
                           _writer(background_path, width, height, fps, ffmpeg, audio_from=source, duration=duration)]
            mask_writer, background_writer = writers
            mask, background = splitter(frame)
            gray = (np.clip(mask, 0, 1) * 255).astype(np.uint8)
            mask_writer.stdin.write(np.repeat(gray[..., None], 3, axis=2).tobytes())
            background_writer.stdin.write(background.tobytes())
            count += 1
            if progress:
                progress(count)
    finally:
        for writer in writers:
            writer.stdin.close()
            writer.wait()
    if count == 0:
        raise RuntimeError(f"動画を読めませんでした: {source}")
    return count


def main(argv=None):
    """コマンドライン (scripts/make_person_videos.py と、EXE の --run-person-split)。"""
    import argparse
    parser = argparse.ArgumentParser(description="動画から人物を切り抜き、人物の範囲の動画と、人物を消した背景の動画を作る")
    parser.add_argument("input")
    parser.add_argument("mask_output", help="人物の範囲の動画 (白 = 人物)")
    parser.add_argument("background_output", help="人物を消した背景の動画 (元の音声付き)")
    parser.add_argument("--long-side", type=int, default=ANALYSIS_LONG_SIDE,
                        help="切り抜く大きさ (長い辺)。元の縦横比のまま、これ以下に縮める")
    parser.add_argument("--fps", type=float, default=10)
    parser.add_argument("--duration", type=float, default=None)
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--model", choices=sorted(SEGMENTERS), default="photo",
                        help="photo: 実写向き (DeepLabV3)、anime: アニメ・イラスト向き (anime-seg、初回に約 176 MB をダウンロード)")
    args = parser.parse_args(argv)
    count = make_person_videos(args.input, args.mask_output, args.background_output, args.fps, args.duration,
                               args.ffmpeg, segmenter=SEGMENTERS[args.model](),
                               progress=lambda i: print(f"\r{i} frames", end="", flush=True) if i % 10 == 0 else None,
                               long_side=args.long_side)
    print(f"\n{count} frames -> {args.mask_output}, {args.background_output}")
    return 0
