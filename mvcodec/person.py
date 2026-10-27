"""動画から人物を切り抜く (Phase 12.3: 動画の人物をだまし絵にする)。

AI の切り抜きモデルで各フレームの人物の範囲を求める。モデルは 2 種類:
  - photo: torchvision の DeepLabV3 MobileNetV3-Large (BSD-3、約 42 MB、初回にダウンロード)。写真で学習したので、
    実写向き。アニメ・3DCG では形が丸くつぶれ、強い光を人物と取り違える
  - anime: skytnt/anime-seg の ISNet (Apache-2.0、約 176 MB、初回にダウンロード。onnxruntime で動かし、
    DirectML / CUDA の GPU があれば使う)。アニメ・イラストの人物を、髪や手の先まで切り抜く。人物を見つけないフレーム
    だけ別のモデルになってちらつくので、前後 6 フレームを先読みして直す (TemporalGuard)
アニメ向けのモデルが人物を見つけないフレームは、GPU があれば目立つ物の切り抜き (BiRefNet) を使うが、GPU のメモリが足りないときは
その場で写真向けのモデルだけに替える (動画の途中で替えたフレームの番号は「salient dropped at frame N」で知らせる)
次の 2 本の動画を作る:
  - 人物の範囲の動画 (白 = 人物): convert.py の --mask-video に渡すと、その形をふくらませた立体 (inflate) になる
  - 人物を消した背景の動画: だまし絵の後ろの壁に流す (--stage-background)。消した所は、同じ場面で人物がいなかった
    ときの色 (カメラが止まっている映像) か、まわりの色をぼかして埋める

    python scripts/make_person_videos.py in.mp4 mask.mp4 background.mp4 --fps 10 [--model anime] [--stats PATH]

終わる前に 1 行の「stats <JSON>」を出し、--stats PATH があればそのファイル (フォルダならその中) にも書く: 選んだモデルと段階・理由、
出所ごとのフレーム数、予備に頼った理由、空のフレーム、場面の切り替わり、モデルごとの実行プロバイダーと推論の ms (中央値・p95 など)、
工程ごとの ms、最大のメモリ (くわしくは make_person_videos と main の説明、docs/TESTING.md の 2.2)
"""
import gc
import hashlib
import http.client
import json
import math
import os
import subprocess
import time
import urllib.error
import urllib.request
from collections import deque
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


def summarize_ms(values):
    """ms の記録 (並び) の個数 n・平均・中央値・p95・最大 (小数 2 桁)。記録が無ければ None。stats に入れる。"""
    if not len(values):
        return None
    a = np.asarray(values, np.float64)
    return {"n": int(a.size), "mean": round(float(a.mean()), 2), "median": round(float(np.median(a)), 2),
            "p95": round(float(np.percentile(a, 95)), 2), "max": round(float(a.max()), 2)}


def session_providers(session):
    """onnxruntime のセッションが実際に使う実行プロバイダー (get_providers)。読めなければ None。"""
    try:
        return list(session.get_providers())
    except Exception:  # noqa: BLE001 (偽物のセッションなど。統計のためだけなので、読めなければ無いものとする)
        return None


def memory_peaks():
    """このプロセスの最大の確保済みメモリ (commit) と最大の working set (バイトの組)。測れなければ None (Windows 以外など)。"""
    try:
        from mvcodec.resources import process_memory_peaks

        peaks = process_memory_peaks()
    except Exception:  # noqa: BLE001 (統計のためだけ。測れなければ無いものとする)
        return None
    return None if peaks is None else (peaks["peak_commit"], peaks["peak_working_set"])


def peak_growth(before):
    """before (memory_peaks の値) から今までに、プロセスの最大の commit と working set が増えた分 (MB)。stats の
    models.<名前> に入れる {"peak_mb": commit, "peak_ws_mb": working set}。測れなければ None。

    プロセス単位の最大値 (high-water mark) の増え分なので、モデルごとの値としては下限: 先に作ったモデルの山より低ければ 0 になる
    (mvcodec は ISNet → 写真向け → BiRefNet の順に作り、最初の推論までを測る)。"""
    after = memory_peaks()
    if before is None or after is None:
        return None
    mb = 1 << 20
    return {"peak_mb": round(max(0, after[0] - before[0]) / mb, 1), "peak_ws_mb": round(max(0, after[1] - before[1]) / mb, 1)}


class PersonSegmenter:
    """1 枚の画像 (高さ × 幅 × 3 の uint8) から、人物である確からしさ (0〜1、同じ大きさ) を返す。
    inference_ms にモデルを動かした時間 (ms) を残す (report で統計にする)。"""

    def __init__(self, device="cpu"):
        self.peak = None   # 作る前から最初の推論の後までに増えた最大のメモリ (peak_growth)
        peak_start = memory_peaks()
        import torch
        from torchvision.models.segmentation import (DeepLabV3_MobileNet_V3_Large_Weights,
                                                     deeplabv3_mobilenet_v3_large)
        self.torch = torch
        self.device = torch.device(device)
        self.inference_ms = []
        weights = DeepLabV3_MobileNet_V3_Large_Weights.DEFAULT
        try:
            self.model = deeplabv3_mobilenet_v3_large(weights=weights).eval().to(self.device)
        except urllib.error.URLError as error:  # 初回の重みのダウンロードに失敗した
            raise ModelError(f"download failed: {getattr(weights, 'url', 'photo model')} ({error})") from error
        self.mean = torch.tensor([0.485, 0.456, 0.406]).view(1, 3, 1, 1)
        self.std = torch.tensor([0.229, 0.224, 0.225]).view(1, 3, 1, 1)
        self._peak_start = peak_start

    def __call__(self, rgb):
        torch = self.torch
        h, w, _ = rgb.shape
        scale = SEGMENT_SHORT_SIDE / min(h, w)
        size = (max(1, round(w * scale)), max(1, round(h * scale)))
        small = np.asarray(Image.fromarray(rgb).resize(size, Image.Resampling.BILINEAR), np.float32) / 255
        x = (torch.from_numpy(small).permute(2, 0, 1)[None] - self.mean) / self.std
        start = time.perf_counter()
        with torch.no_grad():
            logits = self.model(x.to(self.device))["out"].float().cpu()
        self.inference_ms.append((time.perf_counter() - start) * 1000)
        if self._peak_start is not None:
            self.peak, self._peak_start = peak_growth(self._peak_start), None
        prob = torch.softmax(logits, dim=1)[0, PERSON_CLASS].numpy()
        return np.asarray(Image.fromarray(prob).resize((w, h), Image.Resampling.BILINEAR), np.float32)

    def report(self):
        """stats 用: 使ったモデルと、推論の ms (測れたら、最大のメモリの増え分 peak_mb・peak_ws_mb も)。"""
        return {"models": {"photo": {"providers": [f"torch:{self.device}"], "ms": summarize_ms(self.inference_ms),
                                     **(getattr(self, "peak", None) or {})}}}


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
# 予備のモデルの結果を採らない、人物の面積 (確からしさ 0.5 以上の画素の割合) の上限。アニメ向けが何も見つけない場面で、予備が
# 画面の大部分を人物とした結果 (背景や光を取り違えた) を、そのまま立体にしないため。全身が大きい近接の人物でも 0.6 は超えにくい
FALLBACK_MAX_AREA = 0.6
# 予備の結果の位置を調べる、近くの正常なフレームの箱 (外接箱) をたどる範囲。窓から出た後は、同じ場面の最後の正常なフレームの箱を
# これだけのフレームの間覚えておく (速く動く人物が箱から離れていても外さないよう、窓 (PERSON_LOOKAHEAD) より長くする)
FALLBACK_REF_FRAMES = 30
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
# 目立つ物の切り抜き (BiRefNet) の推論が、直近 SALIENT_SLOW_WINDOW 回の中央値でこの ms を超えたら、写真向けのモデルだけに替える。
# DirectML は VRAM が足りないと、例外を出さずに共有メモリへ退避して遅くなる (この PC の RX 9070 XT で、普段は 81〜115 ms、
# VRAM 10 GiB を別に確保して足りなくしたときは 1021〜2410 ms)。遅いだけで正常な GPU (内蔵 GPU など) では誤って外れるので、
# コマンドラインの --salient-slow-ms で変えられる (外れたときは、フレームの番号と中央値を必ず知らせる)
SALIENT_SLOW_MS = 600.0
SALIENT_SLOW_WINDOW = 5


class InputError(RuntimeError):
    """入力の動画を読めない・ffmpeg を起動できない (別の段階でやり直しても直らない)。
    メッセージは対象 (パスなど) と元の例外の文だけにする (GUI が訳した文の {detail} に入るので、日本語の説明は入れない)。"""


class ModelError(RuntimeError):
    """モデルをダウンロードできない・壊れている。アニメ向けのモデルなら、別のモデルを使う photo の段階でなら続けられる
    (GUI が photo の段階へ進む)。メッセージは InputError と同じく、対象 (URL など) と元の例外の文だけ。"""


def model_dir():
    """ダウンロードしたモデルを置くフォルダ (環境変数 BVP_MODEL_DIR で変えられる)。"""
    return Path(os.environ.get("BVP_MODEL_DIR") or Path.home() / ".cache" / "block-video-player")


def download_model(url, path, sha256, progress=None):
    """url を path にダウンロードし、SHA-256 を確かめる (違えば消してエラー)。progress(読んだバイト数, 全体) を呼ぶ。
    通信や書き込みに失敗したときも、SHA-256 が違うときも ModelError。"""
    path = Path(path)
    partial = path.with_suffix(path.suffix + ".part")
    digest = hashlib.sha256()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
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
    except (OSError, http.client.HTTPException) as error:  # 接続できない・404・途中で切れた・書き込めない
        _remove(partial)
        raise ModelError(f"download failed: {url} ({error})") from error
    if digest.hexdigest() != sha256:
        partial.unlink()
        raise ModelError(f"sha256 mismatch: {url}")
    try:
        partial.replace(path)
    except OSError as error:
        _remove(partial)
        raise ModelError(f"could not save: {path} ({error})") from error
    return path


def _remove(path):
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        pass


class AnimeSegmenter:
    """アニメ・イラスト向けの切り抜き (skytnt/anime-seg の ISNet)。PersonSegmenter と同じく、画像から人物の確からしさを返す。

    画像は縦横比を保って 1024 × 1024 の真ん中に置き (余白は黒)、出力の同じ範囲を元の大きさに戻す。
    使ったモデル・実行プロバイダー・推論の ms・予備の選び方と使った回数は、report で統計にする。"""

    def __init__(self, model_path=None, providers=None, download_progress=None, fallback="auto", salient=True,
                 salient_slow_ms=SALIENT_SLOW_MS):
        """fallback: 人物を見つけなかったフレームに使う切り抜き (既定は PersonSegmenter を最初に要るときに作る。
        PyTorch が無ければ使わない)。None なら使わない。salient が偽なら、GPU があっても目立つ物の切り抜き
        (BiRefNet。最初の推論で RAM の確保済みメモリ約 12 GB・VRAM 約 11 GB を使う。段階 full は PROFILE_NEEDS のとおり
        空きの RAM と VRAM が 12 GiB ずつ要る) は使わない。salient_slow_ms: 目立つ物の切り抜きの推論の直近 5 回の中央値が
        これを超えたら外す (SALIENT_SLOW_MS)。"""
        import onnxruntime as ort

        self.fallback = fallback
        self.salient = salient
        self.salient_slow_ms = salient_slow_ms
        self.salient_dropped = None   # 目立つ物の切り抜きを外したら (フレームの番号, "oom"・"slow"・"error")
        self.frame = None             # 予備を呼んでいるフレームの番号 (外したときの知らせに使う)
        self.fallback_rejected = False   # 最後の fallback_prob が、検査で採らずに空を返したか (SalientPersonSegmenter)
        self.peak = None              # ISNet を作る前から最初の推論の後までに増えた最大のメモリ (peak_growth)
        self._peak_start = None
        self.calls = 0
        # 統計用: アニメ向けの推論の ms、予備の切り抜きが返した回数 (モデル別)、予備に何を選んだかと理由、
        # 作った写真向けのモデルと目立つ物の切り抜き (外した後も、使った記録を読めるよう持っておく)
        self.inference_ms = []
        self.fallback_used = {"salient": 0, "photo": 0}
        self.fallback_why = None
        self.fallback_load_s = None   # 予備のモデルを作る (と試しの推論をする) のにかかった秒数
        self.photo = None
        self.salient_model = None
        self.salient_providers = None

        path = Path(model_path) if model_path else model_dir() / ANIME_MODEL_FILE
        if not path.is_file():
            download_model(ANIME_MODEL_URL, path, ANIME_MODEL_SHA256, download_progress)
        if providers is None:
            available = ort.get_available_providers()
            providers = [p for p in GPU_PROVIDERS if p in available]
            providers.append("CPUExecutionProvider")
        self.providers = providers
        self.download_progress = download_progress
        self._peak_start = memory_peaks()
        self.session = ort.InferenceSession(str(path), providers=providers)
        self.input_name = self.session.get_inputs()[0].name

    def primary(self, rgb):
        """アニメ向けのモデルだけで求めた人物の確からしさ (予備の切り抜きは使わない)。"""
        h, w = rgb.shape[:2]
        size = ANIME_INPUT_SIZE
        scale = size / max(h, w)
        nh, nw = max(1, round(h * scale)), max(1, round(w * scale))
        top, left = (size - nh) // 2, (size - nw) // 2
        image = np.zeros((size, size, 3), np.float32)
        image[top:top + nh, left:left + nw] = np.asarray(
            Image.fromarray(rgb).resize((nw, nh), Image.Resampling.BILINEAR), np.float32) / 255
        start = time.perf_counter()
        mask = self.session.run(None, {self.input_name: image.transpose(2, 0, 1)[None]})[0]
        self.inference_ms.append((time.perf_counter() - start) * 1000)
        if self._peak_start is not None:
            self.peak, self._peak_start = peak_growth(self._peak_start), None
        mask = np.asarray(mask, np.float32).reshape(size, size)[top:top + nh, left:left + nw]
        return np.asarray(Image.fromarray(mask).resize((w, h), Image.Resampling.BILINEAR), np.float32)

    def fallback_prob(self, rgb, index=None):
        """予備の切り抜きで求めた人物の確からしさ。予備を使わない設定 (None)・作れないときは None。
        予備のモデルは、最初に要るときに作る。index は今のフレームの番号 (目立つ物の切り抜きを外したときの知らせに使う)。

        目立つ物の切り抜きの推論 (session.run) がメモリ不足で動かなくなったときも、それ以外の実行時の失敗 (DirectML の
        DXGI_ERROR_DEVICE_HUNG など。理由は "error") のときも、補助のモデルだけの失敗なので、写真向けのモデルだけに替えて
        そのフレームをやり直す。写真向けのモデル (DeepLab) 自身の失敗は、メモリ不足以外は上へ伝える。
        推論が遅くなったとき (VRAM が足りずに共有メモリへ退避した) は、そのフレームはそのままで、次から写真向けだけにする。"""
        self.frame = index
        self.fallback_rejected = False
        if self.fallback == "auto":
            start = time.perf_counter()
            self.fallback = self._make_fallback()
            self.fallback_load_s = time.perf_counter() - start
        fallback = self.fallback
        if fallback is None:
            return None
        if not isinstance(fallback, SalientPersonSegmenter):
            self.fallback_used["photo"] += 1
            return fallback(rgb)
        reason = None
        try:
            prob = fallback(rgb)
        except Exception as error:  # noqa: BLE001 (例外の中の画面の参照を残さないよう、外で外す)
            if is_out_of_memory(error):
                reason = ("oom", str(error))
            elif fallback.stage == "salient":   # 補助のモデルの推論だけの失敗。写真向けのモデル自身の失敗は上へ伝える
                reason = ("error", f"{type(error).__name__}: {error}")
            else:
                raise
        if reason is not None:
            # 写真向けのモデル (DeepLab、CPU) のメモリ不足も同じに扱う: 目立つ物の切り抜きを外せば、そのぶんのメモリが空く
            self._drop_salient(*reason)
            self.fallback_used["photo"] += 1
            return self.fallback(rgb)
        self.fallback_used["salient"] += 1
        self.fallback_rejected = fallback.last_rejected
        if fallback.is_slow():
            self._drop_salient("slow", f"median of the last {SALIENT_SLOW_WINDOW} runs {fallback.median_ms():.0f} ms "
                                       f"> {self.salient_slow_ms:.0f} ms")
        return prob

    def __call__(self, rgb):
        index, self.calls = self.calls, self.calls + 1
        mask = self.primary(rgb)
        if (mask >= 0.5).mean() < ANIME_MIN_AREA:
            prob = self.fallback_prob(rgb, index)
            if prob is not None:
                return prob
        return mask

    def _make_fallback(self):
        """人物を見つけなかったフレームの切り抜き: GPU があれば目立つ物の切り抜き + 写真向けのモデル、無ければ写真向けだけ。
        PyTorch が無ければ None (何もしない)。

        目立つ物の切り抜きは GPU のメモリを最初の推論で確保する (BiRefNet_lite fp16 で約 11 GB) ので、作った直後に 1 回
        推論して (試しの推論)、メモリ不足がここで起きるようにする。動かなければ写真向けのモデルだけにする。"""
        try:
            photo = PersonSegmenter()
        except ImportError:
            self.fallback_why = "no_torch"
            return None
        self.photo = photo
        if not self.salient:
            self.fallback_why = "salient_off"   # この段階では使わない設定
            return photo
        if not any(p in GPU_PROVIDERS for p in self.providers):
            self.fallback_why = "no_gpu"
            return photo
        salient = None
        failure = None
        try:
            salient = SalientPersonSegmenter(photo, providers=self.providers, download_progress=self.download_progress,
                                             slow_ms=self.salient_slow_ms)
            salient.warm_up()
            self.salient_model = salient
            self.salient_providers = session_providers(getattr(salient, "session", None))
            self.fallback_why = "salient"
            return salient
        except Exception as error:  # ダウンロードできない・GPU で動かない・メモリが足りないときは写真向けだけにする
            failure = error
        # 例外が持つ画面の参照 (推論の中のセッション) を手放してから解放する
        out_of_memory = is_out_of_memory(failure)
        message = str(failure)
        failure = None
        if salient is not None:
            salient.release()
            salient = None
            gc.collect()
        if out_of_memory:
            self.fallback_why = "dropped_oom"
            self._report_dropped("oom", message)
        else:
            self.fallback_why = "salient_error"
            print(f"[person] 目立つ物の切り抜きを使えないので、写真向けのモデルだけを使います: {message}", flush=True)
        return photo

    def _drop_salient(self, reason, detail):
        """目立つ物の切り抜きを外して写真向けのモデルだけにし、GPU のメモリを返す。"""
        salient = self.fallback
        self.fallback = salient.photo
        salient.release()
        del salient
        gc.collect()
        self.fallback_why = f"dropped_{reason}"
        self._report_dropped(reason, detail)

    def report(self):
        """stats 用: 使ったモデルごとの実行プロバイダーと推論の ms・測れたら最大のメモリの増え分 (models。peak_growth)、
        予備の選び方・理由・返した回数 (fallback)、目立つ物の切り抜きを外したフレームと理由 (salient_dropped)。"""
        models = {"anime": {"providers": session_providers(self.session), "requested": list(self.providers),
                            "ms": summarize_ms(self.inference_ms), **(self.peak or {})}}
        photo = self.photo
        if photo is not None and getattr(photo, "inference_ms", None) and callable(getattr(photo, "report", None)):
            models["photo"] = photo.report()["models"]["photo"]
        salient = self.salient_model
        if salient is not None:
            models["salient"] = {"providers": self.salient_providers,
                                 "ms": summarize_ms(getattr(salient, "inference_ms", [])),
                                 "no_blob_frames": getattr(salient, "no_blob_frames", 0),
                                 "rejected_frames": getattr(salient, "rejected_frames", 0),
                                 **(getattr(salient, "peak", None) or {})}
        fallback = self.fallback
        if fallback == "auto":
            kind = "unused"   # 人物を見つけないフレームが無く、作らなかった
        elif fallback is None:
            kind = "none"
        else:
            kind = "salient" if isinstance(fallback, SalientPersonSegmenter) else "photo"
        dropped = self.salient_dropped
        load = self.fallback_load_s
        return {"models": models,
                "fallback": {"model": kind, "why": self.fallback_why, "frames": dict(self.fallback_used),
                             "load_s": None if load is None else round(load, 2)},
                "salient_dropped": {"frame": dropped[0], "reason": dropped[1]} if dropped else None}

    def _report_dropped(self, reason, detail):
        """外した理由を 1 行で知らせ、salient_dropped に残す (動画の途中で替えるとマスクの形が変わるので、フレームの番号を出す)。"""
        self.salient_dropped = (self.frame, reason)
        detail = " ".join(detail.split())
        print(f"salient dropped at frame {self.frame if self.frame is not None else '?'}: {reason}"
              f"{f' ({detail[:160]})' if detail else ''}", flush=True)


class SalientPersonSegmenter:
    """目立つ物の切り抜き (BiRefNet_lite) の塊のうち、写真向けのモデル (photo) が人物らしいと見る塊だけを返す。"""

    def __init__(self, photo, model_path=None, providers=None, download_progress=None, slow_ms=SALIENT_SLOW_MS):
        import onnxruntime as ort

        self.photo = photo
        self.slow_ms = slow_ms
        self.times = deque(maxlen=SALIENT_SLOW_WINDOW)   # 直近の推論 (session.run) の ms。試しの推論は入れない
        self.inference_ms = []   # 統計用: 試しの推論を除く、全部の推論の ms
        self.no_blob_frames = 0  # 人物らしい塊が 1 つも無く、写真向けのモデルの結果を返したフレームの数
        self.rejected_frames = 0  # そのうち、面積が FALLBACK_MAX_AREA を超えたので空を返したフレームの数
        self.last_rejected = False   # 最後の __call__ が、写真向けの結果を採らずに空を返したか
        self.stage = None
        self.peak = None         # 作る前から試しの推論の後までに増えた最大のメモリ (peak_growth)
        self._peak_start = memory_peaks()
        path = Path(model_path) if model_path else model_dir() / SALIENT_MODEL_FILE
        if not path.is_file():
            download_model(SALIENT_MODEL_URL, path, SALIENT_MODEL_SHA256, download_progress)
        options = ort.SessionOptions()
        options.log_severity_level = 3  # fp16 のモデルは読み込むときに大量の警告を出す
        self.session = ort.InferenceSession(str(path), options, providers=providers or ["CPUExecutionProvider"])
        self.input_name = self.session.get_inputs()[0].name
        self.mean = np.array([0.485, 0.456, 0.406], np.float32)
        self.std = np.array([0.229, 0.224, 0.225], np.float32)

    def warm_up(self):
        """0 の画像で 1 回推論する (試しの推論)。GPU のメモリは最初の推論で確保される (BiRefNet_lite fp16 で約 11 GB) ので、
        足りなければここで例外になる。時間は記録しない (最初の 1 回は、確保のぶん普段の 10 倍ほどかかる)。"""
        size = SALIENT_INPUT_SIZE
        self.session.run(None, {self.input_name: np.zeros((1, 3, size, size), np.float32)})
        if self._peak_start is not None:
            self.peak, self._peak_start = peak_growth(self._peak_start), None

    def release(self):
        """ORT のセッションを手放す (GPU のメモリと RAM が返る)。これ以後は使えない。"""
        self.session = None

    def median_ms(self):
        """直近の推論の ms の中央値 (記録が SALIENT_SLOW_WINDOW 回に満たなければ 0)。"""
        if len(self.times) < SALIENT_SLOW_WINDOW:
            return 0.0
        return sorted(self.times)[SALIENT_SLOW_WINDOW // 2]

    def is_slow(self):
        """直近 SALIENT_SLOW_WINDOW 回の推論の中央値が slow_ms を超えたか (VRAM が足りずに共有メモリへ退避した)。"""
        return self.median_ms() > self.slow_ms

    def salient(self, rgb):
        """目立つ物の確からしさ (0〜1)。画像は 1024 × 1024 に引き伸ばして渡す (BiRefNet の決まり)。"""
        h, w = rgb.shape[:2]
        size = SALIENT_INPUT_SIZE
        image = np.asarray(Image.fromarray(rgb).resize((size, size), Image.Resampling.BILINEAR), np.float32) / 255
        image = (image - self.mean) / self.std
        start = time.perf_counter()
        logits = self.session.run(None, {self.input_name: image.transpose(2, 0, 1)[None]})[0]
        elapsed = (time.perf_counter() - start) * 1000
        self.times.append(elapsed)
        self.inference_ms.append(elapsed)
        prob = 1 / (1 + np.exp(-np.asarray(logits, np.float32).reshape(size, size)))
        return np.asarray(Image.fromarray(prob).resize((w, h), Image.Resampling.BILINEAR), np.float32)

    def __call__(self, rgb):
        self.last_rejected = False
        self.stage = "salient"   # 失敗したのが目立つ物の推論か、写真向けのモデルかを、fallback_prob が見分ける
        salient = self.salient(rgb)
        self.stage = "photo"
        person = self.photo(rgb)
        keep = person_components(salient >= 0.5, person)
        if not keep.any():
            self.no_blob_frames += 1
            # 人物らしい塊が 1 つも無いときは、空にせず、計算済みの写真向けのモデルの結果を返す (アニメ MV の 30 秒で、
            # 人物が消えるフレームが 80 → 31 に減った)。形は粗く大きさが跳ぶので、TemporalGuard が大きさと位置を合わせる。
            # ただし面積が FALLBACK_MAX_AREA を超える結果は (背景や光を人物とした) 採らず、空を返す。位置の検査は TemporalGuard が行う
            person = np.asarray(person, np.float32)
            if float((person >= 0.5).mean()) > FALLBACK_MAX_AREA:
                self.rejected_frames += 1
                self.last_rejected = True
                return np.zeros_like(person)
            return person
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

# メモリの使い方の段階 (重い順)。実測 (RX 9070 XT 16 GB、onnxruntime 1.24.4、DirectML): 写真向け (CPU) は RAM 0.4 GB、
# アニメ向け (ISNet) は VRAM 0.75 GB・確保済みメモリ (commit) +0.55 GB。目立つ物の切り抜き (BiRefNet_lite、fp16) は、読み込みでは
# VRAM 0.23 GB・commit 1.3 GB だが、最初の推論で VRAM 11.3 GB (11321 MiB)・commit 12.4 GB (12426 MiB) を確保する (入力は
# 1024 × 1024 で固定、セッションの設定を変えても同じ。解放すると 0.06 GB・1.1 GB に戻る。ISNet と合わせた full の VRAM は 12.0 GB
# (12022 MiB))。VRAM が足りないと DirectML は例外を出さず共有メモリへ退避し、1 枚 81〜115 ms が 1021〜2410 ms に遅くなる
# (SalientPersonSegmenter が見張る)
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
# 段階ごとに要る空きメモリ (バイト)。(RAM, VRAM)。目立つ物の切り抜きは最初の推論で RAM と VRAM を大きく使う
# (上の実測。1 機種で測った値なので、足りないときは SALIENT_SLOW_MS の見張りで外れる)
PROFILE_NEEDS = {
    "full": (12 * (1 << 30), 12 * (1 << 30)),
    "gpu": (2 * (1 << 30), 1 * (1 << 30)),
    "cpu": (2 * (1 << 30), 0),
    "photo": (1 * (1 << 30), 0),
}
# 処理中に空きの RAM がこれを下回ったら止める (PC 全体が固まる前に、軽い段階でやり直せるように)
MIN_FREE_RAM = 400 * (1 << 20)
# 上の 2 つ (PROFILE_NEEDS と MIN_FREE_RAM) は 1 機種 (RX 9070 XT 16 GB) の実測なので、環境変数で上書きできる
# (profile_needs・min_free_ram が読む。値が不正なら無視して上の値): BVP_PROFILE_NEEDS_GB は段階ごとの空き RAM:VRAM (GiB)
# 「full=12:12,gpu=2:1」、BVP_MIN_FREE_RAM_MB は MB
ENV_PROFILE_NEEDS = "BVP_PROFILE_NEEDS_GB"
ENV_MIN_FREE_RAM = "BVP_MIN_FREE_RAM_MB"
# メモリ不足で止まったときの終了コード (GUI はこれを見て軽い段階でやり直す)
EXIT_OUT_OF_MEMORY = 3
# 入力の動画を読めない・ffmpeg を起動できないときと、モデルをダウンロードできないときの終了コード
# (軽い段階でやり直しても直らないので、GUI は次の段階へ進まずに知らせる)
EXIT_INPUT_ERROR = 4
EXIT_MODEL_ERROR = 5


def _non_negative(text):
    """text を 0 以上の有限の数にする。できなければ None。"""
    try:
        value = float(text)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and value >= 0 else None


def profile_needs():
    """段階ごとに要る空きメモリ {段階: (RAM, VRAM)} (バイト)。PROFILE_NEEDS を、環境変数 BVP_PROFILE_NEEDS_GB
    (「full=12:12,gpu=2:1」のように「段階=RAM:VRAM」を GiB で、カンマ区切り) で段階ごとに上書きしたもの。
    知らない段階・数でない値・負の値の項目は無視する。"""
    needs = dict(PROFILE_NEEDS)
    for item in os.environ.get(ENV_PROFILE_NEEDS, "").split(","):
        name, _, value = item.partition("=")
        ram, _, vram = value.partition(":")
        name, ram, vram = name.strip(), _non_negative(ram), _non_negative(vram)
        if name in needs and ram is not None and vram is not None:
            needs[name] = (int(ram * (1 << 30)), int(vram * (1 << 30)))
    return needs


def min_free_ram():
    """処理中に要る空きの RAM (バイト)。環境変数 BVP_MIN_FREE_RAM_MB (MB。0 で見張らない) があればそれ。不正なら MIN_FREE_RAM。"""
    megabytes = _non_negative(os.environ.get(ENV_MIN_FREE_RAM))
    return MIN_FREE_RAM if megabytes is None else int(megabytes * (1 << 20))


def profile_ladder(model, ram_free=None, vram_free=None):
    """model ("photo" / "anime") を、空きメモリ (分からなければ None) に収まる一番重い段階から、軽い段階へ順に並べる。
    メモリ不足で止まったら次の段階でやり直す。要る空きメモリは profile_needs (環境変数で上書きできる)。"""
    order = ["full", "gpu", "cpu", "photo"] if model == "anime" else ["photo"]
    ladder = []
    needs = profile_needs()
    for name in order:
        need_ram, need_vram = needs[name]
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


def make_segmenter(model, profile, download_progress=None, salient_slow_ms=SALIENT_SLOW_MS):
    """段階に合わせた切り抜きを作る。"""
    if model == "photo" or profile == "photo":
        return PersonSegmenter()
    settings = PROFILES[profile]
    providers = None if settings["gpu"] else ["CPUExecutionProvider"]
    return AnimeSegmenter(providers=providers, download_progress=download_progress, salient=settings["salient"],
                          salient_slow_ms=salient_slow_ms)


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


# 先読みするフレームの数。フレームの結果を確定するのは、これだけ後のフレームを読んでからにする (出力はこれだけ遅れる)
PERSON_LOOKAHEAD = 6
# アニメ向けのモデルが人物を見つけないフレームの連なりがこの長さ以下なら、前後のフレームの結果で埋める (長ければ予備の切り抜き)
HOLD_MAX_RUN = 2
# 場面の切り替わりを構図で決める: 明るさを 32 × 18 に縮めて平均を引き、長さを 1 にそろえた絵の相関が CUT_CORRELATION 未満、
# または CUT_CORRELATION_SOFT 未満で、色のヒストグラム (各色 3 ビット) の差 (0〜1) が CUT_HISTOGRAM_DISTANCE を超えたら切り替わり。
# 明るさが変わっただけ (照明・フラッシュ) では相関が下がらないので、平均の色の差 (SCENE_CUT_DIFF) と違って切り替わりにならない
# (アニメ MV の 30 秒で、隣り合う 299 組のうち、平均の色の差だと 95 組、構図だと 24 組が切り替わり。構図の 24 組は全部、色の差でも切り替わり)
# ただし暗転・単色・白飛びのような一様な画面 (明るさの標準偏差 0〜255 が CUT_FLAT_STD 未満) は、平均を引くと構図が残らず相関が
# 意味を持たない (ほとんど同じ画面が続いても毎フレーム 0 になる)。両方が一様なら平均の明るさの差が CUT_FLAT_DIFF を超えたときだけ、
# 片方だけが一様なら (模様のある画面から暗転・白への切り替わり) 切り替わりとする
CUT_LUMA_SIZE = (32, 18)
CUT_CORRELATION = 0.5
CUT_CORRELATION_SOFT = 0.75
CUT_HISTOGRAM_DISTANCE = 0.5
CUT_FLAT_STD = 3.0
CUT_FLAT_DIFF = 40.0
# 予備の切り抜きの確からしさを写し直すしきい値 tau の範囲と、人物の外接箱を広げる割合・余白 (画素)
MATCH_TAU_MIN = 0.3
MATCH_TAU_MAX = 0.8
MATCH_BOX_GROW = 0.3
MATCH_BOX_MARGIN = 8


def scene_features(rgb):
    """場面の切り替わりを決める画像の特徴: 32 × 18 の明るさ (平均を引いて長さ 1 にそろえたもの)・色のヒストグラム、
    そろえる前の明るさの標準偏差と平均 (一様な画面を見分ける。is_scene_cut を見よ)。"""
    luma = np.asarray(Image.fromarray(rgb).convert("L").resize(CUT_LUMA_SIZE, Image.Resampling.BOX), np.float32)
    mean, std = float(luma.mean()), float(luma.std())
    luma = luma - luma.mean()
    luma /= np.linalg.norm(luma) + 1e-6
    bins = (rgb[::4, ::4].reshape(-1, 3) >> 5).astype(np.int64)
    histogram = np.bincount(bins[:, 0] * 64 + bins[:, 1] * 8 + bins[:, 2], minlength=512).astype(np.float32)
    return luma, histogram / histogram.sum(), std, mean


def is_scene_cut(previous, current):
    """scene_features を 2 つ (前のフレームと今のフレーム) 比べて、場面が切り替わったか。標準偏差と平均を持たない 2 つ組の
    特徴は、一様でない画面として扱う。"""
    flat = [len(features) > 2 and features[2] < CUT_FLAT_STD for features in (previous, current)]
    if all(flat):
        return abs(previous[3] - current[3]) > CUT_FLAT_DIFF
    if any(flat):
        return True
    correlation = float((previous[0] * current[0]).sum())
    distance = 0.5 * float(np.abs(previous[1] - current[1]).sum())
    return correlation < CUT_CORRELATION or (correlation < CUT_CORRELATION_SOFT and distance > CUT_HISTOGRAM_DISTANCE)


def bounding_box(mask):
    """mask (真偽、1 画素以上は真) の真の画素の外接箱 (上, 下, 左, 右。端を含む)。"""
    rows, columns = np.flatnonzero(mask.any(axis=1)), np.flatnonzero(mask.any(axis=0))
    return int(rows[0]), int(rows[-1]), int(columns[0]), int(columns[-1])


def match_to_found(prob, found):
    """予備の切り抜きの確からしさ prob を、近くで人物を見つけたフレーム found (番号順、箱と面積を持つ) に合わせる。
    人物の面積が found の面積の中央値になるように、確からしさを単調に写し直し (確からしさの高い方から、その割合の画素だけが
    0.5 以上になる)、found の最初と最後の人物の外接箱を広げた範囲の外 (離れた所の塊) は 0 にする。"""
    target = float(np.median([f.area for f in found]))
    tau = float(np.clip(np.quantile(prob, 1 - target), MATCH_TAU_MIN, MATCH_TAU_MAX))
    matched = np.where(prob >= tau, 0.5 + 0.5 * (prob - tau) / (1 - tau), 0.5 * prob / tau).astype(np.float32)
    inside = grown_region(prob.shape, [found[0].box, found[-1].box])
    return np.where(inside, matched, 0).astype(np.float32)


def grown_region(shape, boxes):
    """boxes (上, 下, 左, 右) をそれぞれ MATCH_BOX_GROW と MATCH_BOX_MARGIN だけ広げた範囲の和 (真偽、shape の大きさ)。"""
    inside = np.zeros(shape, bool)
    for top, bottom, left, right in boxes:
        grow_y = int((bottom - top + 1) * MATCH_BOX_GROW) + MATCH_BOX_MARGIN
        grow_x = int((right - left + 1) * MATCH_BOX_GROW) + MATCH_BOX_MARGIN
        inside[max(0, top - grow_y):bottom + grow_y + 1, max(0, left - grow_x):right + grow_x + 1] = True
    return inside


def rejects_fallback(prob, boxes=None):
    """予備の切り抜きの確からしさ prob を採用しないか (真なら採用しない)。人物の面積 (0.5 以上の画素の割合) が ANIME_MIN_AREA
    以上のとき、次のどちらかなら採用しない: 面積が FALLBACK_MAX_AREA を超える・boxes (近くの正常なフレームの外接箱) があり、
    人物がそれを広げた範囲 (grown_region) と重ならない。面積が ANIME_MIN_AREA 未満の結果は空として、そのまま (採用しても何も出ない)。"""
    mask = prob >= 0.5
    area = float(mask.mean())
    if area < ANIME_MIN_AREA:
        return False
    if area > FALLBACK_MAX_AREA:
        return True
    return bool(boxes) and not (mask & grown_region(prob.shape, boxes)).any()


class _GuardFrame:
    """TemporalGuard の窓に入れる 1 フレーム。area は確からしさ 0.5 以上の画素の割合、box はその外接箱 (見つけなかったら None)。"""
    __slots__ = ("index", "rgb", "prob", "area", "box", "flagged", "cut", "scene")

    def __init__(self, index, rgb, prob, area, box, cut, scene):
        self.index, self.rgb, self.prob, self.area, self.box = index, rgb, prob, area, box
        self.flagged = area < ANIME_MIN_AREA
        self.cut, self.scene = cut, scene


class TemporalGuard:
    """アニメ向けの切り抜き (AnimeSegmenter) の、フレームごとのばらつきを前後のフレームで直す。

    アニメ向けのモデルが人物を見つけない (確からしさ 0.5 以上の画素が ANIME_MIN_AREA 未満の) フレームは、そのフレームだけ
    別の予備のモデルの結果になり、形も大きさも違うものが 1 枚だけ混ざって立体がちらついた。フレームを PERSON_LOOKAHEAD 枚
    先まで読んでから確定し、見つけなかったフレームを次のように直す (見つけたフレームはそのまま):
      - 同じ場面で、見つけない連なりが HOLD_MAX_RUN 以下で、すぐ前とすぐ後に見つけたフレームがあれば、近い方 (同じ距離なら
        前) の結果をそのまま使う (予備のモデルは呼ばない。形がその間止まる)
      - それ以外は予備の結果を使う。前後 PERSON_LOOKAHEAD 枚の同じ場面に見つけたフレームがあれば、match_to_found で
        その面積と位置に合わせる。無ければ予備の結果のまま
    場面の切り替わりは構図で決め (scene_features)、切り替わりをまたいでは前後のフレームを使わない。窓に持つのは最大
    2 * PERSON_LOOKAHEAD + 1 フレーム (640 × 360 で約 20 MB)。

    予備の結果は採用する前に検査する (rejects_fallback): 面積が FALLBACK_MAX_AREA を超える、または同じ場面の正常なフレームの箱
    (窓の中の前後。無ければ、窓から出た FALLBACK_REF_FRAMES フレーム以内の最後の正常なフレーム) を広げた範囲と重ならない結果は
    採用せず、空の確からしさにする (前のフレームを持ち続けると残像が出る)。人物のいない動画で、予備が背景や光の筋を人物とした塊が
    立体になる (約 20 フレーム) のを防ぐ。判定は「空 (アニメ向け) + 面積の上限と位置 (予備)」の少ない指標にとどめた。前後のフレームとの
    変化量・輪郭の不自然さなどを足した多指標の判定は、空の判定だけで改善のほとんどが得られ、しきい値が増えるほど手元の動画に
    合わせ込む (過学習) おそれがあり、ほかの動画で本物の人物を外す害のほうが大きいので採らなかった。

    counts に出所ごとのフレーム数を数える:
    anime (そのまま)・filled (前後で埋めた)・matched (合わせた予備)・fallback (そのままの予備)・rejected (検査で採用しなかった予備。
    空にした)・flagged (見つけなかった数)。
    reasons には、見つけなかったフレームを前後で埋めずに予備に頼った理由を数える: long_run (見つけない連なりが
    HOLD_MAX_RUN より長い)・no_neighbour (同じ場面のすぐ前かすぐ後に見つけたフレームが無い。場面や動画の端)・
    no_fallback (予備を使えず、アニメ向けの結果のまま。counts では anime に入る)。

        guard = TemporalGuard(segmenter)  # segmenter は primary(rgb) と fallback_prob(rgb) を持つ (AnimeSegmenter)
        for frame in frames:
            for rgb, prob, cut in guard.push(frame):  # 確定したフレーム (PERSON_LOOKAHEAD 枚遅れる)
                ...
        for rgb, prob, cut in guard.flush():  # 動画の終わりで残りを確定
            ...
    """

    def __init__(self, segmenter, lookahead=PERSON_LOOKAHEAD):
        self.segmenter = segmenter
        self.lookahead = lookahead
        self.window = deque()   # 窓の中のフレーム (番号は続いている)
        self.read = 0           # 読んだフレームの数
        self.settled = 0        # 確定したフレームの数 (次に確定するフレームの番号)
        self.scene = 0
        self.features = None    # 前のフレームの scene_features
        self.counts = dict.fromkeys(("anime", "filled", "matched", "fallback", "rejected", "flagged"), 0)
        self.reasons = dict.fromkeys(("long_run", "no_neighbour", "no_fallback"), 0)
        self.reference = None   # 最後に確定した正常なフレームの (場面, 番号, 箱)。窓から出た後の予備の位置の検査に使う

    def push(self, rgb):
        """フレームを 1 枚読み、確定したフレームの (rgb, 人物の確からしさ, 前のフレームから場面が切り替わったか) の並びを返す。"""
        prob = self.segmenter.primary(rgb)
        mask = prob >= 0.5
        area = float(mask.mean())
        features = scene_features(rgb)
        cut = self.features is not None and is_scene_cut(self.features, features)
        self.features = features
        self.scene += cut
        self.window.append(_GuardFrame(self.read, rgb, prob, area, bounding_box(mask) if area >= ANIME_MIN_AREA else None,
                                       cut, self.scene))
        self.read += 1
        ready = []
        while self.settled + self.lookahead < self.read:
            ready.append(self._settle())
        return ready

    def flush(self):
        """動画の終わりで、確定していない残りのフレームを確定して返す。"""
        ready = []
        while self.settled < self.read:
            ready.append(self._settle())
        return ready

    def _at(self, index):
        """窓の中のフレーム。窓に無ければ None。"""
        offset = index - self.window[0].index
        return self.window[offset] if 0 <= offset < len(self.window) else None

    def _settle(self):
        frame = self._at(self.settled)
        if frame.flagged:
            self.counts["flagged"] += 1
            prob, source = self._repair(frame)
        else:
            prob, source = frame.prob, "anime"
            self.reference = (frame.scene, frame.index, frame.box)
        self.counts[source] += 1
        self.settled += 1
        while self.window and self.window[0].index < self.settled - self.lookahead:
            self.window.popleft()
        return frame.rgb, prob, frame.cut

    def _repair(self, frame):
        """人物を見つけなかったフレームの確からしさと、出所 (counts のキー) を返す。"""
        t, scene = frame.index, frame.scene

        def lost(index):
            other = self._at(index)
            return other is not None and other.scene == scene and other.flagged

        def found(other):
            return other is not None and other.scene == scene and not other.flagged

        first = last = t
        while lost(first - 1):
            first -= 1
        while lost(last + 1):
            last += 1
        before, after = self._at(first - 1), self._at(last + 1)
        if last - first + 1 <= HOLD_MAX_RUN and found(before) and found(after):
            return (before if t - before.index <= after.index - t else after).prob, "filled"
        self.reasons["long_run" if last - first + 1 > HOLD_MAX_RUN else "no_neighbour"] += 1
        prob = self.segmenter.fallback_prob(frame.rgb, frame.index)
        if prob is None:  # 予備の切り抜きを使えない
            self.reasons["no_fallback"] += 1
            return frame.prob, "anime"
        near = [other for other in self.window if found(other) and abs(other.index - t) <= self.lookahead]
        if near:
            boxes = [near[0].box, near[-1].box]   # match_to_found が合わせる範囲と同じ
        elif self.reference and self.reference[0] == scene and t - self.reference[1] <= FALLBACK_REF_FRAMES:
            boxes = [self.reference[2]]
        else:
            boxes = []
        if getattr(self.segmenter, "fallback_rejected", False) or rejects_fallback(prob, boxes):
            return np.zeros(prob.shape, np.float32), "rejected"
        if not near:
            return prob, "fallback"
        return match_to_found(prob, near), "matched"


class PersonSplitter:
    """フレームを順に受け取り、(人物の範囲 0〜1、人物を消した背景) を返す。"""

    def __init__(self, segmenter):
        self.segmenter = segmenter
        self.mask = None
        self.previous = None
        self.background = None   # 人物がいなかったときの色の記憶 (場面ごと)
        self.known = None        # 記憶のある画素
        self.cut = False         # 最後に渡されたフレームが、場面の切り替わりだったか (最初のフレームも真)

    def __call__(self, rgb, prob=None, cut=None):
        """prob は人物の確からしさ (省くと segmenter で求める)。cut は前のフレームから場面が切り替わったか (省くと、
        平均の色の差が SCENE_CUT_DIFF を超えたかで決める)。切り替わりでは、ならしと背景の記憶を捨てる。"""
        if prob is None:
            prob = self.segmenter(rgb)
        frame = rgb.astype(np.float32)
        if cut is None:
            cut = self.previous is None or np.abs(frame - self.previous).mean() > SCENE_CUT_DIFF
        self.cut = bool(cut or self.mask is None)
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
    proc = _start_ffmpeg(cmd, ffmpeg, stdout=subprocess.PIPE)
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


def _start_ffmpeg(cmd, ffmpeg, **kwargs):
    """ffmpeg を起動する。起動できなければ (見つからない・実行できない) InputError。"""
    try:
        return subprocess.Popen(cmd, **kwargs, **_subprocess_kwargs())
    except OSError as error:
        raise InputError(f"could not start ffmpeg: {ffmpeg} ({error})") from error


def _writer(path, width, height, fps, ffmpeg, audio_from=None, duration=None):
    cmd = [ffmpeg, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}",
           "-r", str(fps), "-i", "-"]
    if audio_from is not None:
        # 映像は受け取ったフレームの数が正: 出力全体を -t や -shortest で切ると、duration が 1 フレームの長さの倍数でないとき
        # (0.35 秒を 10 fps など) に最後の 1 フレームが落ちる。音声の入力だけを duration で切る
        if duration is not None:
            cmd += ["-t", str(duration)]
        cmd += ["-i", str(audio_from), "-map", "0:v", "-map", "1:a?", "-c:a", "aac"]
    # 色を正確に残す (人物の範囲は白黒、背景はブロックの色を選ぶ元になる)
    cmd += ["-c:v", "libx264", "-crf", "12", "-pix_fmt", "yuv444p", str(path)]
    return _start_ffmpeg(cmd, ffmpeg, stdin=subprocess.PIPE)


def _check_free_ram():
    """空きの RAM が min_free_ram() (既定は MIN_FREE_RAM) を下回っていたら MemoryError (PC 全体が固まる前に止める)。"""
    from mvcodec.resources import system_memory

    memory = system_memory()
    if memory is not None and memory[1] < min_free_ram():
        raise MemoryError(f"空きの RAM が少なくなりました ({memory[1] >> 20} MB)")


def _timed(frames, samples):
    """frames から次の 1 枚を取り出すのにかかった ms を samples に残しながら、そのまま順に返す。"""
    iterator = iter(frames)
    try:
        while True:
            start = time.perf_counter()
            try:
                item = next(iterator)
            except StopIteration:
                return
            samples.append((time.perf_counter() - start) * 1000)
            yield item
    finally:
        close = getattr(iterator, "close", None)
        if close is not None:
            close()


def make_person_videos(source, mask_path, background_path, fps, duration=None, ffmpeg="ffmpeg", segmenter=None,
                       progress=None, long_side=ANALYSIS_LONG_SIDE, stats=None):
    """source から、人物の範囲の動画 (mask_path) と、人物を消した背景の動画 (background_path、元の音声付き) を作る。
    どちらも元の縦横比で、長い辺 long_side 以下 (convert.py は人物の範囲を元の動画と同じように縮めるので位置がそろう)。
    戻り値はフレームの数。progress(i) を書き出したフレームごとに呼ぶ。

    アニメ向けの切り抜き (primary と fallback_prob を持つもの) は TemporalGuard を通すので、書き出しは PERSON_LOOKAHEAD
    フレーム遅れる (動画の終わりで全部書き出す。フレームの数・音声・背景の動画は変わらない)。

    stats (dict) を渡すと、次を入れる (途中で失敗しても、そこまでの分が入る):
      frames: 書き出したフレームの数、run_s: この関数の秒数
      sources: 出所ごとのフレーム数。アニメ向け: anime (そのまま)・filled (前後で埋めた)・matched (合わせた予備)・
        fallback (そのままの予備)・rejected (検査で採らずに空にした予備)。写真向けだけのとき: photo
      flagged: アニメ向けが人物を見つけなかったフレームの数 (アニメ向けだけ)
      fallback: reasons (予備に頼った理由と数。TemporalGuard.reasons)、model (予備に何を使っているか)・why (その理由)・
        frames (予備が返したフレームの数、モデル別) (segmenter.report があるとき)
      empty_frames: 書き出したマスクの人物が画面の ANIME_MIN_AREA 未満だったフレームの数
      scene_cuts: 場面の切り替わりの数 (最初のフレームは数えない)
      stages_ms: 工程 (read / segment / split / write) ごとの、呼び出し 1 回の ms の n・mean・median・p95・max と total_s
        (合計の秒数)。read は ffmpeg を待った時間、segment は切り抜き (アニメ向けは先読みの TemporalGuard を含む。動画の
        終わりで残りを確定する分は flush_ms に別に入れ、n・mean などには入れない)、split は背景の組み立て、write は書き出し
      models・salient_dropped: segmenter.report の、使ったモデルごとの実行プロバイダーと推論の ms・目立つ物の切り抜きを
        外したフレームと理由"""
    segmenter = segmenter or PersonSegmenter()
    splitter = PersonSplitter(segmenter)
    guard = TemporalGuard(segmenter) if all(callable(getattr(segmenter, name, None))
                                           for name in ("primary", "fallback_prob")) else None
    writers = []
    count = empty = cuts = 0
    flush_ms = 0.0
    started = time.perf_counter()
    timings = {name: [] for name in ("read", "segment", "split", "write")}

    def write(settled):
        nonlocal count, empty, cuts
        for rgb, prob, cut in settled:
            mask_writer, background_writer = writers
            start = time.perf_counter()
            mask, background = splitter(rgb, prob, cut)
            split_done = time.perf_counter()
            gray = (np.clip(mask, 0, 1) * 255).astype(np.uint8)
            mask_writer.stdin.write(np.repeat(gray[..., None], 3, axis=2).tobytes())
            background_writer.stdin.write(background.tobytes())
            timings["split"].append((split_done - start) * 1000)
            timings["write"].append((time.perf_counter() - split_done) * 1000)
            # 書き出したマスクで数える (動画に読み戻して 0.5 以上を人物とした数と同じ)。最初のフレームは切り替わりに数えない
            empty += bool((gray >= 128).mean() < ANIME_MIN_AREA)
            cuts += bool(count > 0 and splitter.cut)
            count += 1
            if count % 10 == 0:
                _check_free_ram()
            if progress:
                progress(count)

    def collect_stats():
        stages = {}
        for name, values in timings.items():
            stages[name] = summarize_ms(values)
            if values:
                stages[name]["total_s"] = round((sum(values) + (flush_ms if name == "segment" else 0)) / 1000, 2)
        if guard and stages["segment"]:
            stages["segment"]["flush_ms"] = round(flush_ms, 1)
        stats.update(frames=count, run_s=round(time.perf_counter() - started, 2), empty_frames=empty, scene_cuts=cuts,
                     stages_ms=stages)
        if guard:
            stats["sources"] = {key: guard.counts[key] for key in ("anime", "filled", "matched", "fallback", "rejected")}
            stats["flagged"] = guard.counts["flagged"]
        else:
            stats["sources"] = {"photo": count}
        report = segmenter.report() if callable(getattr(segmenter, "report", None)) else {}
        stats["models"] = report.get("models", {})
        if guard:
            stats["fallback"] = {**report.get("fallback", {}), "reasons": dict(guard.reasons)}
        stats["salient_dropped"] = report.get("salient_dropped")

    try:
        for frame in _timed(read_frames(source, fps, duration, ffmpeg, long_side), timings["read"]):
            if not writers:
                height, width = frame.shape[:2]
                writers = [_writer(mask_path, width, height, fps, ffmpeg),
                           _writer(background_path, width, height, fps, ffmpeg, audio_from=source, duration=duration)]
            start = time.perf_counter()
            settled = guard.push(frame) if guard else [(frame, segmenter(frame), None)]
            timings["segment"].append((time.perf_counter() - start) * 1000)
            write(settled)
        if guard:
            start = time.perf_counter()
            settled = guard.flush()
            flush_ms = (time.perf_counter() - start) * 1000
            write(settled)
    finally:
        if stats is not None:
            collect_stats()
        for writer in writers:
            writer.stdin.close()
            writer.wait()
    if count == 0:
        raise InputError(f"could not read the video: {source}")
    return count


def skipped_profiles(model, profile, ram_free, vram_free):
    """profile より重い段階を選ばなかった理由 ({段階: 理由})。空きメモリが足りなかった段階は足りなかった量を、足りていたのに
    選ばなかった段階 (指定された・メモリ不足や落ちた後のやり直し) は "not chosen"。空きが分からない (None) ときは足りないとみなさない。"""
    order = ["full", "gpu", "cpu", "photo"] if model == "anime" else ["photo"]
    gib = 1 << 30
    skipped = {}
    needs = profile_needs()
    for name in order[:order.index(profile)] if profile in order else []:
        need_ram, need_vram = needs[name]
        why = []
        if ram_free is not None and ram_free < need_ram:
            why.append(f"RAM {ram_free / gib:.1f} < {need_ram / gib:.0f} GiB")
        if vram_free is not None and vram_free < need_vram:
            why.append(f"VRAM {vram_free / gib:.1f} < {need_vram / gib:.0f} GiB")
        skipped[name] = ", ".join(why) or "not chosen"
    return skipped


def _emit_stats(stats, started, destination=None, error=None):
    """統計 (stats) に全体の秒数・最大のメモリ・失敗の理由を足し、1 行の「stats <JSON>」で出す。destination (ファイル、または
    フォルダ。フォルダなら person-stats-<日時>.json) があれば、そこにも書く (windowed の EXE では標準出力が捨てられることがあるので)。"""
    from mvcodec.resources import process_memory_peaks

    mb = 1 << 20
    peaks = process_memory_peaks()
    stats["wall_s"] = round(time.perf_counter() - started, 2)
    stats["memory"] = None if peaks is None else {"peak_working_set_mb": round(peaks["peak_working_set"] / mb, 1),
                                                  "peak_commit_mb": round(peaks["peak_commit"] / mb, 1)}
    stats["error"] = error
    print("stats " + json.dumps(stats, separators=(",", ":")), flush=True)
    if not destination:
        return
    path = Path(destination)
    if path.is_dir() or str(destination).endswith(("/", "\\")):
        path = path / f"person-stats-{time.strftime('%Y%m%d-%H%M%S')}.json"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(stats, indent=1) + "\n", encoding="utf-8")
    except OSError as problem:
        print(f"[person] 統計のファイルを書けませんでした: {path} ({problem})", flush=True)


def main(argv=None):
    """コマンドライン (scripts/make_person_videos.py と、EXE の --run-person-split)。

    GUI は別のプロセスとしてこれを起動し、標準出力の「<数> frames」「download <済み> <全体>」を読む。
    メモリ不足で止まったら EXIT_OUT_OF_MEMORY で終わる (GUI は軽い段階でやり直す)。入力を読めない・ffmpeg を起動できない
    ときは EXIT_INPUT_ERROR、モデルをダウンロードできないときは EXIT_MODEL_ERROR で終わる (どちらも 1 行の知らせ付き。知らせは
    対象と元の例外の文だけで、説明は GUI が訳す。入力の問題は軽い段階では直らないので GUI はやり直さない。モデルの問題は、
    アニメ向けなら別のモデルを使う photo の段階で続けられる)。目立つ物の切り抜きを外したときは
    「salient dropped at frame <番号>: oom|slow|error」を出す。

    終わる前 (done や失敗の知らせの前) に 1 行の「stats <JSON>」を出す。選んだモデルと段階とその理由、始めたときの空きメモリ、
    出所ごとのフレーム数、予備に頼った理由、空のフレームの数、場面の切り替わりの数、モデルごとの実行プロバイダーと推論の ms
    (n・mean・median・p95・max)、工程ごとの ms、秒数、最大の working set と最大の commit (Windows のみ)。--stats PATH があれば
    同じ JSON をそのファイルにも書く。--free-ram・--free-vram (バイト) が渡されたときは (GUI が直前に測った値)、空きメモリを測り直さない。
    モデルごとの最大のメモリは、プロセスの最大値 (high-water mark) の増え分 (models.<名前>.peak_mb。peak_growth) で、
    プロセス単位でしか測れないので下限の目安。"""
    import argparse

    from mvcodec.resources import gpu_memory, system_memory, use_utf8_for_pipes

    use_utf8_for_pipes()
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
    parser.add_argument("--profile-reason", default=None,
                        help="段階を選んだ理由 (統計にそのまま入れる。GUI が、最初の段階か、メモリ不足・落ちた後のやり直しかを渡す)")
    parser.add_argument("--salient-slow-ms", type=float, default=SALIENT_SLOW_MS,
                        help=f"目立つ物の切り抜き (BiRefNet) の推論の直近 {SALIENT_SLOW_WINDOW} 回の中央値がこの ms を超えたら、"
                             f"写真向けのモデルだけにする (既定 {SALIENT_SLOW_MS:.0f}。遅いが正常な GPU では大きくする)")
    parser.add_argument("--free-ram", type=int, default=None, metavar="BYTES",
                        help="空きの RAM (バイト。GUI が測った値)。渡されたら測り直さない (負なら、分からないものとして扱う)")
    parser.add_argument("--free-vram", type=int, default=None, metavar="BYTES",
                        help="空きの VRAM (バイト。GUI が測った値)。渡されたら、測り直さない (測るのに約 2.6 秒かかる。負なら分からない)")
    parser.add_argument("--stats", default=None, metavar="PATH",
                        help="統計 (JSON) を書くファイル。フォルダなら、その中に person-stats-<日時>.json を作る")
    args = parser.parse_args(argv)
    started = time.perf_counter()
    profile = args.profile
    # 始めたときの空きメモリ。GUI が測った値 (--free-ram・--free-vram) があればそれを使う。VRAM は測るのに約 2.6 秒かかるので、
    # 渡されていないときは、GPU を使う段階 (と、段階を選ぶとき) だけ測る
    if args.free_ram is not None:
        ram_free = args.free_ram if args.free_ram >= 0 else None
    else:
        ram = system_memory()
        ram_free = ram and ram[1]
    if args.free_vram is not None:
        vram_free = args.free_vram if args.free_vram >= 0 else None
    else:
        vram = gpu_memory() if profile is None or (args.model == "anime" and PROFILES[profile]["gpu"]) else None
        vram_free = vram and vram[1]
    reason = args.profile_reason
    if profile is None:
        profile = profile_ladder(args.model, ram_free, vram_free)[0]
        reason = reason or "auto"
    long_side = args.long_side or PROFILES[profile]["long_side"]
    print(f"profile {profile}", flush=True)
    stats = {"version": 1, "model": args.model, "profile": profile, "profile_reason": reason or "given",
             "profile_skipped": skipped_profiles(args.model, profile, ram_free, vram_free),
             "free_at_start": {"ram_mb": ram_free and ram_free >> 20, "vram_mb": vram_free and vram_free >> 20},
             "fps": args.fps, "long_side": long_side}

    def finish(error=None):
        _emit_stats(stats, started, args.stats, error and {"kind": error[0], "message": " ".join(error[1].split())[:300]})

    try:
        load_start = time.perf_counter()
        segmenter = make_segmenter(args.model, profile,
                                   download_progress=lambda done, total: print(f"download {done} {total}", flush=True),
                                   salient_slow_ms=args.salient_slow_ms)
        stats["load_s"] = round(time.perf_counter() - load_start, 2)
        count = make_person_videos(args.input, args.mask_output, args.background_output, args.fps, args.duration,
                                   args.ffmpeg, segmenter=segmenter,
                                   progress=lambda i: print(f"{i} frames", flush=True) if i % 10 == 0 else None,
                                   long_side=long_side, stats=stats)
    except InputError as error:
        finish(("input", str(error)))   # 知らせの行を最後にする (GUI は終わりの行を理由として読む)
        print(f"input error: {' '.join(str(error).split())}", flush=True)
        return EXIT_INPUT_ERROR
    except ModelError as error:
        finish(("model", str(error)))
        print(f"model error: {' '.join(str(error).split())}", flush=True)
        return EXIT_MODEL_ERROR
    except Exception as error:  # noqa: BLE001 (メモリ不足だけ、決まった終了コードで知らせる)
        if is_out_of_memory(error):
            finish(("out_of_memory", str(error)))
            print(f"out of memory: {error}", flush=True)
            return EXIT_OUT_OF_MEMORY
        finish(("crash", f"{type(error).__name__}: {error}"))
        raise
    if "flagged" in stats:  # 出所ごとのフレーム数 (anime=そのまま filled=前後で埋めた matched=合わせた予備 fallback=そのままの予備)
        print("sources " + " ".join(f"{key}={value}" for key, value in {**stats["sources"], "flagged": stats["flagged"]}.items()),
              flush=True)
    finish()
    print(f"done {count} frames -> {args.mask_output}, {args.background_output}", flush=True)
    return 0
