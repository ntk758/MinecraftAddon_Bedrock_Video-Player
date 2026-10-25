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


# 切り抜きと背景を作る大きさ (長い辺) の既定 (下の ANALYSIS_LONG_SIDE と同じ)
ANALYSIS_LONG_SIDE_DEFAULT = 640
# アニメ向けのモデル (skytnt/anime-seg、Apache-2.0)。入力は 1024 × 1024 の RGB (0〜1)、出力は人物の確からしさ
ANIME_MODEL_URL = "https://huggingface.co/skytnt/anime-seg/resolve/main/isnetis.onnx"
ANIME_MODEL_SHA256 = "f15622d853e8260172812b657053460e20806f04b9e05147d49af7bed31a6e99"
ANIME_MODEL_FILE = "isnetis.onnx"
ANIME_INPUT_SIZE = 1024
# アニメ向けのモデルが人物を見つけなかったとみなす、確からしさ 0.5 以上の画素の割合。3DCG の MV で強い照明が当たった
# 場面や、全身が小さく映った場面では何も返さないことがあるので、そのフレームだけ実写向きのモデルの結果を使う
# (利用者のアニメ MV で、アニメ向けだけでは 12 場面中 6 場面で人物が消えた)
ANIME_MIN_AREA = 0.005
# アニメ向けのモデルが人物を見つけないフレームに、GPU があれば使う「目立つ物」の切り抜き (onnx-community の BiRefNet_lite、
# MIT、fp16 で約 115 MB、GPU で 1 枚約 0.15 秒。CPU では約 6 秒かかるので使わない)。3DCG の MV の強い照明の場面や
# 全身が小さい場面でも人物を切り抜けるが、字幕・光の筋なども拾うので、写真向けのモデルが人物らしいと見る塊だけ残す
SALIENT_MODEL_URL = "https://huggingface.co/onnx-community/BiRefNet_lite-ONNX/resolve/main/onnx/model_fp16.onnx"
SALIENT_MODEL_SHA256 = "d39b897ceb16ae654c1731f3dba0cf9b368d9cae74b5a57459b455cc8bfec402"
SALIENT_MODEL_FILE = "birefnet_lite_fp16.onnx"
SALIENT_INPUT_SIZE = 1024
# 塊を残す、写真向けのモデルの確からしさの平均の下限と、塊の大きさの下限 (画面の割合)。利用者のアニメ MV で、人物の塊は
# 0.38〜0.71、字幕・光の筋・背景の塊は 0.01〜0.19 だった
SALIENT_MIN_PERSON = 0.35
SALIENT_MIN_AREA = 0.001
# 塊を数えるときの画像の長い辺 (細かい所は塊の判定に要らない)
COMPONENT_LONG_SIDE = 160
GPU_PROVIDERS = ("DmlExecutionProvider", "CUDAExecutionProvider")


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

    def __init__(self, model_path=None, providers=None, download_progress=None, fallback="auto", salient=True):
        """fallback: 人物を見つけなかったフレームに使う切り抜き (既定は PersonSegmenter を最初に要るときに作る。
        PyTorch が無ければ使わない)。None なら使わない。salient が偽なら、GPU があっても目立つ物の切り抜き
        (BiRefNet、RAM を 5〜10 GB 使う) は使わない。"""
        import onnxruntime as ort

        self.fallback = fallback
        self.salient = salient

        path = Path(model_path) if model_path else model_dir() / ANIME_MODEL_FILE
        if not path.is_file():
            download_model(ANIME_MODEL_URL, path, ANIME_MODEL_SHA256, download_progress)
        if providers is None:
            available = ort.get_available_providers()
            providers = [p for p in GPU_PROVIDERS if p in available]
            providers.append("CPUExecutionProvider")
        self.providers = providers
        self.download_progress = download_progress
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
                self.fallback = self._make_fallback()
                if self.fallback is None:
                    return mask
            return self.fallback(rgb)
        return mask

    def _make_fallback(self):
        """人物を見つけなかったフレームの切り抜き: GPU があれば目立つ物の切り抜き + 写真向けのモデル、無ければ写真向けだけ。
        PyTorch が無ければ None (何もしない)。"""
        try:
            photo = PersonSegmenter()
        except ImportError:
            return None
        if not self.salient or not any(p in GPU_PROVIDERS for p in self.providers):
            return photo
        try:
            return SalientPersonSegmenter(photo, providers=self.providers, download_progress=self.download_progress)
        except Exception as error:  # ダウンロードできない・GPU で動かないときは写真向けだけにする
            print(f"[person] 目立つ物の切り抜きを使えないので、写真向けのモデルだけを使います: {error}")
            return photo


class SalientPersonSegmenter:
    """目立つ物の切り抜き (BiRefNet_lite) の塊のうち、写真向けのモデル (photo) が人物らしいと見る塊だけを返す。"""

    def __init__(self, photo, model_path=None, providers=None, download_progress=None):
        import onnxruntime as ort

        self.photo = photo
        path = Path(model_path) if model_path else model_dir() / SALIENT_MODEL_FILE
        if not path.is_file():
            download_model(SALIENT_MODEL_URL, path, SALIENT_MODEL_SHA256, download_progress)
        options = ort.SessionOptions()
        options.log_severity_level = 3  # fp16 のモデルは読み込むときに大量の警告を出す
        self.session = ort.InferenceSession(str(path), options, providers=providers or ["CPUExecutionProvider"])
        self.input_name = self.session.get_inputs()[0].name
        self.mean = np.array([0.485, 0.456, 0.406], np.float32)
        self.std = np.array([0.229, 0.224, 0.225], np.float32)

    def salient(self, rgb):
        """目立つ物の確からしさ (0〜1)。画像は 1024 × 1024 に引き伸ばして渡す (BiRefNet の決まり)。"""
        h, w = rgb.shape[:2]
        size = SALIENT_INPUT_SIZE
        image = np.asarray(Image.fromarray(rgb).resize((size, size), Image.Resampling.BILINEAR), np.float32) / 255
        image = (image - self.mean) / self.std
        logits = self.session.run(None, {self.input_name: image.transpose(2, 0, 1)[None]})[0]
        prob = 1 / (1 + np.exp(-np.asarray(logits, np.float32).reshape(size, size)))
        return np.asarray(Image.fromarray(prob).resize((w, h), Image.Resampling.BILINEAR), np.float32)

    def __call__(self, rgb):
        salient = self.salient(rgb)
        person = self.photo(rgb)
        keep = person_components(salient >= 0.5, person)
        return np.where(keep, salient, 0).astype(np.float32)


def person_components(mask, person):
    """mask (真偽) のつながった塊のうち、person (確からしさ) の平均が SALIENT_MIN_PERSON 以上で、画面の SALIENT_MIN_AREA
    以上の大きさの塊だけを真にした配列。塊は長い辺 COMPONENT_LONG_SIDE に縮めて数える。"""
    h, w = mask.shape
    step = max(1, -(-max(h, w) // COMPONENT_LONG_SIDE))
    small = mask[::step, ::step]
    small_person = person[::step, ::step]
    labels = _label(small)
    keep = np.zeros(int(labels.max()) + 1, bool)
    if labels.max():
        counts = np.bincount(labels.reshape(-1), minlength=len(keep))
        sums = np.bincount(labels.reshape(-1), weights=small_person.reshape(-1), minlength=len(keep))
        mean = sums / np.maximum(counts, 1)
        keep = (mean >= SALIENT_MIN_PERSON) & (counts >= SALIENT_MIN_AREA * small.size)
        keep[0] = False
    # 縮めた画像で 1 マス広げてから元の大きさに戻す (塊の縁の画素が、縮めたときに抜けて四角く欠けないように)
    small_keep = keep[labels]
    grown = small_keep.copy()
    grown[1:] |= small_keep[:-1]
    grown[:-1] |= small_keep[1:]
    grown[:, 1:] |= small_keep[:, :-1]
    grown[:, :-1] |= small_keep[:, 1:]
    big = np.repeat(np.repeat(grown, step, axis=0), step, axis=1)[:h, :w]
    return big & mask


def _label(mask):
    """上下左右でつながった真の画素に、塊ごとの番号 (1 から) を付ける (0 は偽)。小さい画像用 (numpy だけで、番号を
    隣どうしで小さい方にそろえることを変わらなくなるまで繰り返す)。"""
    h, w = mask.shape
    big = h * w + 1
    labels = np.where(mask, np.arange(1, h * w + 1).reshape(h, w), big)
    while True:
        smallest = labels.copy()
        smallest[1:] = np.minimum(smallest[1:], labels[:-1])
        smallest[:-1] = np.minimum(smallest[:-1], labels[1:])
        smallest[:, 1:] = np.minimum(smallest[:, 1:], labels[:, :-1])
        smallest[:, :-1] = np.minimum(smallest[:, :-1], labels[:, 1:])
        smallest = np.where(mask, smallest, big)
        if np.array_equal(smallest, labels):
            break
        labels = smallest
    labels = np.where(mask, labels, 0)
    _, compact = np.unique(labels, return_inverse=True)
    return compact.reshape(h, w)


# 切り抜きのモデルの種類 → 作り方
SEGMENTERS = {"photo": PersonSegmenter, "anime": AnimeSegmenter}

# メモリの使い方の段階 (重い順)。実測 (2026-10-25、RX 9070 XT): 写真向け (CPU) は RAM 0.4 GB、アニメ向けは RAM 0.9 GB・
# VRAM 0.24 GB (GPU)、目立つ物の切り抜き (BiRefNet、fp16、DirectML) を足すと RAM 5.6〜10.4 GB・VRAM 1.8〜4.8 GB
#   full: アニメ向け (GPU) + 見つけないフレームは目立つ物の切り抜き
#   gpu: アニメ向け (GPU)、見つけないフレームは写真向け
#   cpu: アニメ向けも CPU で (1 枚約 0.75 秒)
#   photo: 写真向けだけ (アニメ向けが全部だめだったとき)
PROFILES = {
    "full": {"gpu": True, "salient": True, "long_side": ANALYSIS_LONG_SIDE_DEFAULT},
    "gpu": {"gpu": True, "salient": False, "long_side": ANALYSIS_LONG_SIDE_DEFAULT},
    "cpu": {"gpu": False, "salient": False, "long_side": 480},
    "photo": {"gpu": False, "salient": False, "long_side": 480},
}
# 段階ごとに要る空きメモリ (バイト)。(RAM, VRAM)。目立つ物の切り抜きは読み込むときに RAM を大きく使う
PROFILE_NEEDS = {
    "full": (12 * (1 << 30), 3 * (1 << 30)),
    "gpu": (2 * (1 << 30), 1 * (1 << 30)),
    "cpu": (2 * (1 << 30), 0),
    "photo": (1 * (1 << 30), 0),
}
# 処理中に空きの RAM がこれを下回ったら止める (PC 全体が固まる前に、軽い段階でやり直せるように)
MIN_FREE_RAM = 400 * (1 << 20)
# メモリ不足で止まったときの終了コード (GUI はこれを見て軽い段階でやり直す)
EXIT_OUT_OF_MEMORY = 3


def profile_ladder(model, ram_free=None, vram_free=None):
    """model ("photo" / "anime") を、空きメモリ (分からなければ None) に収まる一番重い段階から、軽い段階へ順に並べる。
    メモリ不足で止まったら次の段階でやり直す。"""
    order = ["full", "gpu", "cpu", "photo"] if model == "anime" else ["photo"]
    ladder = []
    for name in order:
        need_ram, need_vram = PROFILE_NEEDS[name]
        if ram_free is not None and ram_free < need_ram and name != order[-1]:
            continue
        if vram_free is not None and vram_free < need_vram:
            continue
        ladder.append(name)
    return ladder or [order[-1]]


_OUT_OF_MEMORY_WORDS = ("out of memory", "outofmemory", "e_outofmemory", "8007000e", "not enough memory",
                        "insufficient memory", "allocation failed", "failed to allocate", "887a0005",
                        "device_removed", "device removed", "memoryerror", "bad_alloc")


def is_out_of_memory(error):
    """例外 (かその文字列) がメモリ不足 (GPU のメモリ不足・GPU の切り離しを含む) によるものか。"""
    if isinstance(error, MemoryError):
        return True
    text = f"{type(error).__name__} {error}".lower()
    return any(word in text for word in _OUT_OF_MEMORY_WORDS)


def make_segmenter(model, profile, download_progress=None):
    """段階に合わせた切り抜きを作る。"""
    if model == "photo" or profile == "photo":
        return PersonSegmenter()
    settings = PROFILES[profile]
    providers = None if settings["gpu"] else ["CPUExecutionProvider"]
    return AnimeSegmenter(providers=providers, download_progress=download_progress, salient=settings["salient"])


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
ANALYSIS_LONG_SIDE = ANALYSIS_LONG_SIDE_DEFAULT


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


def _check_free_ram():
    """空きの RAM が MIN_FREE_RAM を下回っていたら MemoryError (PC 全体が固まる前に止める)。"""
    from mvcodec.resources import system_memory

    memory = system_memory()
    if memory is not None and memory[1] < MIN_FREE_RAM:
        raise MemoryError(f"空きの RAM が少なくなりました ({memory[1] >> 20} MB)")


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
            if count % 10 == 0:
                _check_free_ram()
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
    """コマンドライン (scripts/make_person_videos.py と、EXE の --run-person-split)。

    GUI は別のプロセスとしてこれを起動し、標準出力の「<数> frames」「download <済み> <全体>」を読む。
    メモリ不足で止まったら EXIT_OUT_OF_MEMORY で終わる (GUI は軽い段階でやり直す)。"""
    import argparse
    parser = argparse.ArgumentParser(description="動画から人物を切り抜き、人物の範囲の動画と、人物を消した背景の動画を作る")
    parser.add_argument("input")
    parser.add_argument("mask_output", help="人物の範囲の動画 (白 = 人物)")
    parser.add_argument("background_output", help="人物を消した背景の動画 (元の音声付き)")
    parser.add_argument("--long-side", type=int, default=None,
                        help="切り抜く大きさ (長い辺)。元の縦横比のまま、これ以下に縮める (既定は段階による)")
    parser.add_argument("--fps", type=float, default=10)
    parser.add_argument("--duration", type=float, default=None)
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--model", choices=sorted(SEGMENTERS), default="photo",
                        help="photo: 実写向き (DeepLabV3)、anime: アニメ・イラスト向き (anime-seg、初回に約 176 MB をダウンロード)")
    parser.add_argument("--profile", choices=list(PROFILES), default=None,
                        help="メモリの使い方 (full / gpu / cpu / photo)。既定は空きメモリから選ぶ")
    args = parser.parse_args(argv)
    profile = args.profile
    if profile is None:
        from mvcodec.resources import gpu_memory, system_memory

        ram, vram = system_memory(), gpu_memory()
        profile = profile_ladder(args.model, ram and ram[1], vram and vram[1])[0]
    long_side = args.long_side or PROFILES[profile]["long_side"]
    print(f"profile {profile}", flush=True)
    try:
        segmenter = make_segmenter(args.model, profile,
                                   download_progress=lambda done, total: print(f"download {done} {total}", flush=True))
        count = make_person_videos(args.input, args.mask_output, args.background_output, args.fps, args.duration,
                                   args.ffmpeg, segmenter=segmenter,
                                   progress=lambda i: print(f"{i} frames", flush=True) if i % 10 == 0 else None,
                                   long_side=long_side)
    except Exception as error:  # noqa: BLE001 (メモリ不足だけ、決まった終了コードで知らせる)
        if is_out_of_memory(error):
            print(f"out of memory: {error}", flush=True)
            return EXIT_OUT_OF_MEMORY
        raise
    print(f"done {count} frames -> {args.mask_output}, {args.background_output}", flush=True)
    return 0
