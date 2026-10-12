"""MVCodec - 動画 / PNG連番を Minecraft Bedrock 用のフレーム差分データ (varint_rle_v4) へ変換する CLI。

処理は 2 パスのストリーミングで行い、動画全体をメモリへ載せない。
  Pass 1: シーン検出で GOP 境界を決め、自動パレット時は GOP ごとのパレットを生成
  Pass 2: 減色 (GPU / CPU) → RLE 差分エンコード → GOP 単位でチャンク化
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image

from mvcodec.color import (
    ALL_BLOCKS,
    PALETTES,
    create_palette_image,
    process_single_frame_img,
    rgb_to_oklab_torch,
)
from gui_i18n import LOCALES, Translator, detect_system_language
from mvcodec.device import DEVICE_CHOICES, detect_gpu, empty_cache, squared_distances
from mvcodec.dither_motion import DitherAligner
# 条件付き更新のしきい値 (RGB の距離)。前のブロックの色がこれ以内しか悪くならなければ変えない
TEMPORAL_HYSTERESIS = 24.0
from mvcodec.encode import MAX_PALETTE_COLORS
from mvcodec.motion import MV_BLOCK, MV_RANGE
from mvcodec.stream import StreamEncoder
from mvcodec.planes import LAYOUT as PLANES_LAYOUT, planes_supported, solid_rule_for, volume_change_ratio, volume_to_planes
from mvcodec.volume import (
    AIR_LEVEL,
    RELIEF_HYSTERESIS,
    VolumeLifter,
    load_vox_frames_with_palettes,
    luminance01,
    nearest_levels,
    vox_to_volume,
    vox_volume_shape,
)
from mvcodec.evaluate import calculate_ssim_global

WIDTH = 64
HEIGHT = 64
OUTPUT_DIR = "BP/scripts"
OUTPUT_FILE = f"{OUTPUT_DIR}/frames_data.js"
FORMAT_NAME = "varint_rle_v5"
MIN_GOP_SIZE = 20
MAX_GOP_SIZE = 300
SCENE_CUT_THRESHOLD = 30.0
AUTO_PALETTE_MAX_COLORS = 64
AUTO_PALETTE_SAMPLE_STRIDE = 10
AUTO_PALETTE_MAX_SAMPLES = 30
SSIM_SAMPLE_INTERVAL = 10
SSIM_MAX_SAMPLES = 50
CPU_BATCH_SIZE = 256
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".bmp", ".webp")
GPU_DITHER_METHODS = ("none", "ordered", "blue_noise")

# ログの表示言語 (main で --lang に合わせて切り替える)
_tr = Translator(detect_system_language())


# ---------------------------------------------------------------------------
# 入力 (FFmpeg / 画像フォルダ)
# ---------------------------------------------------------------------------

def _subprocess_kwargs():
    """Windows の GUI (EXE) から呼ばれた時にコンソール窓が開かないようにする。"""
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


def build_video_filter(width, height, fps=None):
    """アスペクト比を保って縮小し、余白を黒で中央に埋める FFmpeg フィルタ文字列。"""
    filters = []
    if fps is not None:
        filters.append(f"fps={fps}")
    filters.append(f"scale={width}:{height}:force_original_aspect_ratio=decrease")
    filters.append(f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black")
    return ",".join(filters)


def ffmpeg_frame_generator(video_path, width, height, fps, duration=None, ffmpeg="ffmpeg"):
    cmd = [ffmpeg, "-v", "error", "-i", str(video_path), "-vf", build_video_filter(width, height, fps)]
    if duration is not None:
        cmd.extend(["-t", str(duration)])
    cmd.extend(["-f", "rawvideo", "-pix_fmt", "rgb24", "-"])

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **_subprocess_kwargs())
    frame_size = width * height * 3
    completed = False
    try:
        while True:
            raw = proc.stdout.read(frame_size)
            if len(raw) < frame_size:
                break
            yield np.frombuffer(raw, dtype=np.uint8).reshape((height, width, 3))
        completed = True
    finally:
        if not completed:
            proc.kill()
        proc.stdout.close()
        stderr = proc.stderr.read().decode("utf-8", errors="replace")
        proc.stderr.close()
        returncode = proc.wait()
    if completed and returncode != 0:
        raise RuntimeError(f"ffmpeg がエラー終了しました (code {returncode}): {stderr.strip()}")


def letterbox_image(img, width, height):
    """FFmpeg の scale(decrease)+pad と同じ規則で画像を収める。"""
    img = img.convert("RGB")
    if img.size == (width, height):
        return img
    ratio = min(width / img.width, height / img.height)
    new_size = (max(1, round(img.width * ratio)), max(1, round(img.height * ratio)))
    resized = img.resize(new_size, Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (width, height), (0, 0, 0))
    canvas.paste(resized, ((width - new_size[0]) // 2, (height - new_size[1]) // 2))
    return canvas


def image_dir_frame_generator(frames_dir, width, height):
    files = sorted(p for p in Path(frames_dir).iterdir() if p.suffix.lower() in IMAGE_EXTENSIONS)
    for path in files:
        with Image.open(path) as img:
            yield np.asarray(letterbox_image(img, width, height), dtype=np.uint8)


def make_frame_source(args):
    """呼ぶたびに先頭からフレームを流すイテレータを返す関数 (2 パス処理用)。"""
    if args.input_video:
        return lambda: ffmpeg_frame_generator(
            args.input_video, args.width, args.height, args.fps, args.duration, args.ffmpeg
        )
    return lambda: image_dir_frame_generator(args.frames_dir, args.width, args.height)


# ---------------------------------------------------------------------------
# Pass 1: シーン検出 / GOP 分割 / 自動パレット
# ---------------------------------------------------------------------------

def scene_score(a, b):
    """0.5*SAD + 0.3*ヒストグラム差 + 0.2*エッジ差"""
    a = a.astype(np.float32)
    b = b.astype(np.float32)
    sad = np.abs(a - b).mean()

    hist_a, _ = np.histogram(a, bins=32, range=(0, 256))
    hist_b, _ = np.histogram(b, bins=32, range=(0, 256))
    hist_diff = np.abs(hist_a - hist_b).sum() / a.size * 255.0

    gx_a, gy_a = np.gradient(a.mean(axis=2))
    gx_b, gy_b = np.gradient(b.mean(axis=2))
    edge_diff = np.abs(np.sqrt(gx_a**2 + gy_a**2) - np.sqrt(gx_b**2 + gy_b**2)).mean()

    return 0.5 * sad + 0.3 * hist_diff + 0.2 * edge_diff


def analyze_gops(frames, palette_for_gop=None):
    """シーンチェンジで GOP 境界を決める。palette_for_gop(samples, first_frame) を渡すと GOP ごとに呼ぶ。

    戻り値: (gop_boundaries, palettes)  gop_boundaries は [0, ..., total_frames]
    """
    boundaries = [0]
    palettes = []
    samples = []
    first_frame = None
    prev = None
    count = 0

    def close_gop():
        if palette_for_gop is not None and first_frame is not None:
            palettes.append(palette_for_gop(samples, first_frame))

    for i, frame in enumerate(frames):
        if prev is not None:
            frames_since_last = i - boundaries[-1]
            is_cut = scene_score(frame, prev) > SCENE_CUT_THRESHOLD and frames_since_last >= MIN_GOP_SIZE
            if is_cut or frames_since_last >= MAX_GOP_SIZE:
                close_gop()
                boundaries.append(i)
                samples = []
                first_frame = None
        if first_frame is None:
            first_frame = frame
        if palette_for_gop is not None:
            offset = i - boundaries[-1]
            if offset % AUTO_PALETTE_SAMPLE_STRIDE == 0 and len(samples) < AUTO_PALETTE_MAX_SAMPLES:
                samples.append(frame)
        prev = frame
        count = i + 1

    if count == 0:
        return [0], []
    close_gop()
    boundaries.append(count)
    return boundaries, palettes


def make_auto_palette_builder(gpu):
    from mvcodec.auto_palette import generate_auto_palette

    cache = {}

    def build(samples, first_frame):
        # 同じ見た目で始まる GOP はパレットを使い回す (JS 側の容量節約)
        thumb_hash = hashlib.md5(np.ascontiguousarray(first_frame[::8, ::8]).tobytes()).hexdigest()
        if thumb_hash not in cache:
            cache[thumb_hash] = generate_auto_palette(
                samples, ALL_BLOCKS, max_colors=AUTO_PALETTE_MAX_COLORS, device=gpu.torch_device if gpu else None, sample_stride=1,
                warn=lambda error: print(_tr("convert.palette_cpu_retry", error=error)),
            )
        return cache[thumb_hash]

    return build


# ---------------------------------------------------------------------------
# Pass 2: 減色
# ---------------------------------------------------------------------------

def iter_gop_ids(gop_boundaries):
    """フレーム番号順に所属 GOP 番号を返す。"""
    for gop_id in range(len(gop_boundaries) - 1):
        for _ in range(gop_boundaries[gop_id], gop_boundaries[gop_id + 1]):
            yield gop_id


def quantize_frames_gpu(frames, gop_palettes_rgb, gop_boundaries, width, height,
                        dither_method="none", apply_perceptual=True, gpu=None, dither_follow_motion=False):
    """GPU (CUDA / ROCm / DirectML) で OkLab 色空間マッチング + RDO + 時間方向ディザを行い、フレームごとに (H, W) のパレット番号を返す。

    パレットが切り替わる GOP 境界では前フレーム参照 (RDO / 時間方向ディザ) をリセットする。
    gpu が None の場合は PyTorch の CPU 実行になる (テスト用)。
    """
    import torch
    from mvcodec.color import get_edge_mask_gpu
    from mvcodec.dither_motion import DitherAligner, dither_pattern

    device = gpu.torch_device if gpu is not None else torch.device("cpu")

    def get_pal_tensors(pal_data):
        t = torch.tensor(pal_data, dtype=torch.float32, device=device)
        return t, rgb_to_oklab_torch(t)

    dither_scale = {"ordered": 32.0, "blue_noise": 64.0}.get(dither_method)
    # ディザ模様と前フレーム由来の状態を、映像の全体の動きに合わせてずらす (mvcodec/dither_motion.py)
    aligner = DitherAligner(enabled=dither_follow_motion and dither_scale is not None)
    offset = (0, 0)

    def dither_tensor_at(position):
        pattern = dither_pattern(dither_method, width, height, position) * dither_scale
        return torch.tensor(np.stack([pattern] * 3, axis=-1), dtype=torch.float32, device=device)

    def analyze_scene(diff_ratio):
        # フレーム変化率に基づいて (RDO閾値, ME閾値, 更新予算, 時間方向ディザ重み) を決定
        if diff_ratio < 0.01:
            return 40.0, 3.0, int(width * height * 0.05), 0.2
        elif diff_ratio < 0.05:
            return 25.0, 5.0, int(width * height * 0.10), 0.4
        elif diff_ratio < 0.20:
            return 15.0, 7.0, int(width * height * 0.20), 0.5
        elif diff_ratio < 0.50:
            return 8.0, 10.0, int(width * height * 0.35), 0.6
        else:
            return 0.0, 0.0, width * height, 0.8

    current_palette = None
    pal_tensor = pal_oklab = None
    temporal_dither_weight = 0.5

    for i, (img_arr, gop_id) in enumerate(zip(frames, iter_gop_ids(gop_boundaries))):
        if gop_palettes_rgb[gop_id] is not current_palette:
            current_palette = gop_palettes_rgb[gop_id]
            pal_tensor, pal_oklab = get_pal_tensors(current_palette)
            # パレット番号の意味が変わるので前フレーム由来の状態は全て破棄する
            error_buffer = torch.zeros((height, width, 3), dtype=torch.float32, device=device)
            importance_buffer = torch.zeros((height, width), dtype=torch.float32, device=device)
            prev_idx_tensor = None
            prev_orig_img_tensor = None

        new_offset = aligner.update(img_arr)
        dx, dy = new_offset[0] - offset[0], new_offset[1] - offset[1]
        offset = new_offset
        if (dx or dy) and prev_idx_tensor is not None:
            # 中身が (dx, dy) ずれたので、前フレーム由来の状態も同じだけずらす: 今[y][x] ← 前[y + dy][x + dx]
            roll = lambda t: torch.roll(t, shifts=(-dy, -dx), dims=(0, 1))  # noqa: E731
            error_buffer = roll(error_buffer)
            importance_buffer = roll(importance_buffer)
            prev_idx_tensor = roll(prev_idx_tensor)
            prev_orig_img_tensor = roll(prev_orig_img_tensor)

        orig_img_tensor = torch.tensor(img_arr, dtype=torch.float32, device=device)
        img_tensor = orig_img_tensor.clone()

        # Temporal Dithering (前フレームからの誤差を加算)
        img_tensor += error_buffer

        if dither_scale is not None:
            dither_tensor = dither_tensor_at(offset)
            bias = dither_tensor * get_edge_mask_gpu(img_tensor) if apply_perceptual else dither_tensor
            img_tensor += bias
            img_tensor = torch.clamp(img_tensor, 0, 255)

        # OkLab 知覚色空間でのカラーマッチング
        img_oklab = rgb_to_oklab_torch(img_tensor.view(-1, 3))
        idx_tensor = torch.argmin(squared_distances(img_oklab, pal_oklab), dim=1).view(height, width)

        # Rate-Distortion Optimization (RDO) & Motion Estimation (ME)
        if prev_idx_tensor is not None:
            # シーン適応型圧縮: 変化ピクセル率を RGB 差分から大まかに計算
            diff_orig = torch.abs(orig_img_tensor - prev_orig_img_tensor).mean(dim=-1)
            diff_orig_pool = torch.nn.functional.avg_pool2d(
                diff_orig.unsqueeze(0).unsqueeze(0), kernel_size=3, stride=1, padding=1
            ).squeeze(0).squeeze(0)

            diff_ratio = (diff_orig_pool > 5.0).float().mean().item()
            rdo_threshold, me_threshold, max_update_pixels, temporal_dither_weight = analyze_scene(diff_ratio)

            # 1. ME Mask (背景静止化 - RGBベース)
            me_mask = diff_orig_pool < me_threshold

            # 2. RDO (ディザリング抑制 - OkLabベース)
            prev_colors_oklab = pal_oklab[prev_idx_tensor]
            dist_to_prev_oklab = torch.norm(img_oklab.view(height, width, 3) - prev_colors_oklab, dim=-1)

            # 局所分散に応じた知覚的 RDO (平坦部ほど閾値を上げる)
            gray = orig_img_tensor.mean(dim=-1, keepdim=True).permute(2, 0, 1).unsqueeze(0)
            mean_sq = torch.nn.functional.avg_pool2d(gray, 3, stride=1, padding=1)**2
            sq_mean = torch.nn.functional.avg_pool2d(gray**2, 3, stride=1, padding=1)
            variance = torch.clamp(sq_mean - mean_sq, min=0.0).squeeze()
            norm_var = torch.clamp(variance / 500.0, 0.0, 1.0)
            perceptual_scale = 1.0 + (1.0 - norm_var) * 2.0  # Edges=1x threshold, Flat=3x threshold

            rdo_mask = dist_to_prev_oklab < (rdo_threshold / 255.0) * perceptual_scale

            # 3. Budget (予算ベース描画)
            want_to_update = ~(me_mask | rdo_mask)
            importance_buffer += torch.where(want_to_update, dist_to_prev_oklab, torch.zeros_like(dist_to_prev_oklab))

            # 更新希望数が予算を超える場合のみ、重要度上位だけを更新する
            if want_to_update.sum().item() > max_update_pixels:
                flat_importance = importance_buffer.view(-1)
                _, topk_indices = torch.topk(flat_importance, max_update_pixels)
                budget_mask = torch.zeros_like(flat_importance, dtype=torch.bool)
                budget_mask[topk_indices] = True
                reuse_mask = ~budget_mask.view(height, width)
            else:
                reuse_mask = ~want_to_update

            idx_tensor = torch.where(reuse_mask, prev_idx_tensor, idx_tensor)
            importance_buffer = torch.where(idx_tensor != prev_idx_tensor, torch.zeros_like(importance_buffer), importance_buffer)

        # Temporal Dithering のための誤差計算 (蓄積しすぎないようクランプ)
        selected_colors = pal_tensor[idx_tensor]
        error_buffer = torch.clamp((img_tensor - selected_colors) * temporal_dither_weight, -32.0, 32.0)

        yield idx_tensor.cpu().numpy().astype(np.int32)
        prev_idx_tensor = idx_tensor.clone()
        prev_orig_img_tensor = orig_img_tensor.clone()

        if i % 64 == 0:
            empty_cache(gpu)


def keep_previous_blocks(indices, previous, source_rgb, palette_rgb, threshold):
    """条件付き更新: 前のフレームのパレット番号の色が、新しく選んだ色より (RGB の距離で) threshold 以内しか
    悪くならない画素は、前の番号のままにする。誤差は今のフレームの元の色と比べるので、ずれが積み重なることはない。"""
    if previous is None or threshold <= 0 or previous.shape != indices.shape:
        return indices
    source = np.asarray(source_rgb, dtype=np.float32).reshape(*indices.shape, 3)
    palette = np.asarray(palette_rgb, dtype=np.float32)
    new_error = ((palette[indices] - source) ** 2).sum(-1)
    old_error = ((palette[previous] - source) ** 2).sum(-1)
    return np.where(old_error <= new_error + threshold * threshold, previous, indices)


def quantize_frames_cpu(frames, gop_palettes, gop_boundaries, width, height,
                        dither_method, apply_perceptual, threads, dither_follow_motion=False,
                        temporal_hysteresis=0.0):
    """CPU スレッドプールで減色し、フレームごとに (H, W) のパレット番号を返す。

    temporal_hysteresis > 0 なら、前のフレームから色がほとんど変わらない画素は前の番号のままにする
    (keep_previous_blocks)。GOP が変わる (パレットが変わる) ときは前のフレームを使わない。
    """
    # GOP 間で同じパレットオブジェクトは Pillow パレット画像も共有する
    prepared = {}
    for pal in gop_palettes:
        if id(pal) not in prepared:
            rgb = np.array([item["rgb"] for item in pal], dtype=np.float64)
            prepared[id(pal)] = (create_palette_image(pal), rgb, len(pal))

    def task(job):
        img_arr, pal, offset = job
        pal_img, pal_rgb, num_colors = prepared[id(pal)]
        img = Image.fromarray(img_arr)
        return process_single_frame_img(img, pal_img, width, height, num_colors,
                                        dither_method, apply_perceptual, pal_rgb, offset)

    state = {"previous": None, "palette": None}

    def finish(batch, results):
        """スレッドで減色した結果に、順番どおり条件付き更新をかける。"""
        for (img_arr, pal, _offset), indices in zip(batch, results):
            if pal is not state["palette"]:
                state["previous"], state["palette"] = None, pal
            if temporal_hysteresis > 0 and np.asarray(img_arr).shape[:2] == indices.shape:
                indices = keep_previous_blocks(indices, state["previous"], img_arr, prepared[id(pal)][1],
                                               temporal_hysteresis)
            state["previous"] = indices
            yield indices

    # ディザ模様のずらし量は順番に決める必要があるので、スレッドに渡す前に計算する
    aligner = DitherAligner(enabled=dither_follow_motion and dither_method in ("blue_noise", "ordered"))
    jobs = ((frame, gop_palettes[gop_id], aligner.update(frame))
            for frame, gop_id in zip(frames, iter_gop_ids(gop_boundaries)))
    with ThreadPoolExecutor(max_workers=max(1, threads)) as executor:
        batch = []
        for job in jobs:
            batch.append(job)
            if len(batch) >= CPU_BATCH_SIZE:
                yield from finish(batch, executor.map(task, batch))
                batch = []
        if batch:
            yield from finish(batch, executor.map(task, batch))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="MVCodec - Minecraft Video Codec: PNG連番や動画をBedrock用フレーム差分データへ超高速変換します。"
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input-video", default=None, help="入力動画ファイル (FFmpeg で読み込み)")
    source.add_argument("--frames-dir", default=None, help="入力画像 (PNG等の連番) フォルダ。ファイル名順に読み込む")
    source.add_argument("--input-vox", default=None,
                        help="MagicaVoxel の .vox (アニメーションの各フレームが 1 フレーム、シーンのオブジェクトはまとめて 1 つの立体。"
                             "シーングラフの無い古い形式は各モデルが 1 フレーム) または .vox が並んだフォルダ (--mode vox)")
    parser.add_argument("--mode", default=None, choices=["screen", "relief", "extrude", "inflate", "vox"],
                        help="screen: 平面スクリーン / relief: 明るさを奥行きにした立体 / extrude: 明るい部分を押し出した立体 / "
                             "inflate: 明るい部分を丸くふくらませた立体 / "
                             "vox: MagicaVoxel の立体アニメ (既定は入力に合わせて screen か vox)")
    parser.add_argument("--depth", type=int, default=16, help="立体モードの奥行き (ブロック数)")
    parser.add_argument("--depth-video", default=None,
                        help="relief で奥行きに使うグレースケール動画 (明るいほど手前)。省略時は各画素の明るさを使う")
    parser.add_argument("--extrude-threshold", type=float, default=0.5,
                        help="extrude / inflate で立体にする明るさのしきい値 (0〜1)")
    parser.add_argument("--extrude-invert", action="store_true", help="extrude / inflate で暗い部分を立体にする")
    parser.add_argument("--thickness", type=int, default=None, help="extrude の厚み (ブロック数、既定は奥行き全体)")
    parser.add_argument("--hollow", action=argparse.BooleanOptionalAction, default=False,
                        help="外から見えない内部のブロックを置かない。既定の描画 (fillBlocks で箱ごとに置く) では"
                             "詰まっている方が呼び出しもデータも少ないので無効。block 方式で 1 個ずつ置くなら有効にすると置く数が減る")
    parser.add_argument("--volume-encoding", default="auto", choices=["auto", "planes", "voxels"],
                        help="relief / extrude / inflate の送り方。planes: 色の平面と高さ (厚み) の平面の 2 枚の 2D 映像で送り、"
                             "アドオンで立体に組み立てる (データが小さい) / voxels: 立体のボクセルをそのまま送る / "
                             "auto: 送れるなら planes (--hollow のときは voxels)")
    parser.add_argument("--depth-hysteresis", type=float, default=RELIEF_HYSTERESIS,
                        help="relief の奥行きのヒステリシス (段数)。明るさが少し揺れただけでは奥行きを変えない (0 で無効)")
    parser.add_argument("--output", default=OUTPUT_FILE, help="出力パス")
    parser.add_argument("--width", type=int, default=WIDTH)
    parser.add_argument("--height", type=int, default=HEIGHT)
    parser.add_argument("--fps", type=float, default=20.0, help="出力フレームレート (Minecraft は最大 20fps)")
    parser.add_argument("--duration", type=float, default=None)
    parser.add_argument("--palette", default="full", choices=[*PALETTES.keys(), "auto"])
    parser.add_argument("--dither-method", default="none", choices=["none", "floyd", "ordered", "blue_noise", "atkinson", "burkes", "sierra"])
    parser.add_argument("--temporal-hysteresis", type=float, default=TEMPORAL_HYSTERESIS,
                        help="前のフレームと同じブロックの色が、新しく選んだ色より RGB の距離でこの値以内しか悪くならないなら"
                             "変えない (条件付き更新)。置き直すブロックとデータが減る。0 で無効。CPU で減色するときに使う "
                             "(GPU の減色には同様の仕組み (RDO) が入っている)")
    parser.add_argument("--dither-follow-motion", action=argparse.BooleanOptionalAction, default=False,
                        help="Blue Noise / Ordered のディザ模様を映像の全体の動き (パン) に合わせてずらす。"
                             "パンのデータは大きく減るが、模様が毎フレーム動くので置き直すブロックは増える (既定は無効)")
    parser.add_argument("--perceptual", action=argparse.BooleanOptionalAction, default=True,
                        help="エッジ部のみディザをかける知覚的処理 (--no-perceptual で無効)")
    parser.add_argument("--threads", type=int, default=os.cpu_count() or 4)
    parser.add_argument("--keyframe-interval", type=int, default=30,
                        help="GOP 内のキーフレーム間隔。0 なら各 GOP の先頭のみ")
    parser.add_argument("--device", default="auto", choices=DEVICE_CHOICES,
                        help="減色に使うデバイス。auto は CUDA/ROCm → DirectML → CPU の順に自動選択")
    parser.add_argument("--gpu", action="store_true", help="互換用 (GPU は --device auto で自動使用されます)")
    parser.add_argument("--adaptive-fps", action=argparse.BooleanOptionalAction, default=True,
                        help="変化の少ないフレームを省略する (--no-adaptive-fps で無効)")
    parser.add_argument("--motion", action=argparse.BooleanOptionalAction, default=True,
                        help="動き補償 (パン・スクロールで差分を小さくする)。各フレームで小さくなる場合だけ使う (--no-motion で無効)")
    parser.add_argument("--mv-range", type=int, default=MV_RANGE,
                        help="動きベクトルの探索範囲 (ブロック数, 1〜15)。大きいほど速い動きに強いが変換が遅い "
                             "(立体は 8x8x8 のブリック単位で ±2 に固定)")
    parser.add_argument("--tile-size", default="auto", choices=["auto", "0", "4", "8", "16"],
                        help="タイル辞書のタイルの大きさ (0 で無効)。繰り返し現れる模様を辞書から貼って差分を小さくする。"
                             "auto は 4 と 8 の両方で符号化して小さい方を使う")
    parser.add_argument("--scene-threshold", type=float, default=0.015,
                        help="adaptive-fps でフレームを省略する変化率のしきい値")
    parser.add_argument("--ffmpeg", default="ffmpeg", help="ffmpeg 実行ファイルのパス")
    parser.add_argument("--lang", default=None, choices=sorted(LOCALES), help="ログの表示言語 (既定は OS の言語)")
    parser.add_argument("--demo-gif", default=None, help="指定されたパスにデモ用GIFを出力する")
    args = parser.parse_args(argv)

    if args.width < 1 or args.height < 1:
        parser.error("--width / --height は 1 以上にしてください")
    if not 0 < args.fps <= 20:
        parser.error("--fps は 0 より大きく 20 以下にしてください")
    if args.keyframe_interval < 0:
        parser.error("--keyframe-interval は 0 以上にしてください")
    if not 1 <= args.mv_range <= 15:
        parser.error("--mv-range は 1〜15 にしてください")
    if args.mode is None:
        args.mode = "vox" if args.input_vox else "screen"
    if (args.mode == "vox") != bool(args.input_vox):
        parser.error("--mode vox には --input-vox を、--input-vox には --mode vox を指定してください")
    if not 2 <= args.depth <= 128:
        parser.error("--depth は 2〜128 にしてください")
    if args.depth_video and args.mode != "relief":
        parser.error("--depth-video は --mode relief でだけ使えます")
    if args.temporal_hysteresis < 0:
        parser.error("--temporal-hysteresis は 0 以上にしてください")
    if args.depth_hysteresis < 0:
        parser.error("--depth-hysteresis は 0 以上にしてください")
    if args.volume_encoding == "planes" and not planes_supported(args.mode, args.depth, args.hollow):
        parser.error("--volume-encoding planes は relief / extrude / inflate で、--hollow なし、"
                     "--depth 127 以下 (relief は 128 以下) のときだけ使えます")
    return args


def gpu_self_test(gpu, dither_method, apply_perceptual):
    """選んだ GPU で減色処理が最後まで動くかを小さな入力で確かめる (DirectML の未対応演算などを事前に検出)。"""
    rng = np.random.default_rng(0)
    frames = [rng.integers(0, 256, size=(8, 8, 3), dtype=np.uint8) for _ in range(3)]
    palette_rgb = np.array([item["rgb"] for item in PALETTES["full"]], dtype=np.float64)
    results = list(quantize_frames_gpu(frames, [palette_rgb], [0, len(frames)], 8, 8,
                                       dither_method, apply_perceptual, gpu=gpu))
    if len(results) != len(frames) or any(r.shape != (8, 8) or r.min() < 0 or r.max() >= len(palette_rgb) for r in results):
        raise RuntimeError("GPU quantization returned invalid palette indices")


def select_gpu(preference, dither_method, apply_perceptual):
    """GPU を選び、動作確認に失敗したら CPU にフォールバックする。"""
    if preference == "cpu":
        print(_tr("convert.device_cpu_forced"))
        return None
    gpu = detect_gpu(preference)
    if gpu is None:
        if preference != "auto":
            print(_tr("convert.device_missing", device=preference), file=sys.stderr)
        print(_tr("convert.device_none"))
        return None
    test_dither = dither_method if dither_method in GPU_DITHER_METHODS else "none"
    try:
        gpu_self_test(gpu, test_dither, apply_perceptual)
    except Exception as error:
        print(_tr("convert.gpu_failed", gpu=gpu.describe(), error=error), file=sys.stderr)
        return None
    if dither_method not in GPU_DITHER_METHODS:
        print(_tr("convert.device_cpu_dither", dither=dither_method, gpu=gpu.describe()))
    else:
        print(_tr("convert.device_gpu", gpu=gpu.describe()))
    return gpu


def level_block_specs(palette):
    specs = []
    for item in palette:
        spec = {"block": item["block"]}
        if "states" in item:
            spec["states"] = item["states"]
        specs.append(spec)
    return specs


def _use_utf8_for_pipes():
    """GUI などからパイプ経由で起動された時、ログの日本語を UTF-8 で書き出す (cp932 で化けるのを防ぐ)。"""
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure") and not stream.isatty():
            # 行ごとに書き出す (GUI のログに進み具合がすぐ出るように。既定ではパイプ先へはまとめて書かれる)
            stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)


class Progress:
    """変換の進み具合を、数秒ごとに 1 行ずつログへ出す (長い動画や立体は数十分かかることがあるため)。"""

    def __init__(self, total, interval=3.0):
        self.total = max(1, total)
        self.interval = interval
        self.start = time.perf_counter()
        self.last = self.start

    def update(self, done):
        now = time.perf_counter()
        if done < self.total and now - self.last < self.interval:
            return
        self.last = now
        elapsed = now - self.start
        fps = done / elapsed if elapsed > 0 else 0.0
        remaining = (self.total - done) / fps if fps > 0 else 0.0
        minutes, seconds = divmod(int(remaining + 0.5), 60)
        print(_tr("convert.progress", done=done, total=self.total, percent=int(100 * done / self.total),
                  fps=f"{fps:.1f}", eta=f"{minutes}:{seconds:02d}"))


def build_frame_data(args, width, height, gop_boundaries, palettes, adaptive, encoder, chunks, index, depth=None,
                     solid_rule=None):
    level_blocks = [level_block_specs(pal) for pal in palettes] if adaptive else level_block_specs(palettes[0])
    data = {
        "width": width,
        "height": height,
        "fps": args.fps,
        "frame_count": encoder.stats.total_frames,
        "keyframe_interval": args.keyframe_interval,
        "format": FORMAT_NAME,
        "mv_block": MV_BLOCK,
        "mv_range": encoder.mv_limit,
        "tile_size": encoder.tile_size,
        "tile_dict_max": encoder.tiles.max_entries if encoder.tiles is not None else 0,
        "gop_boundaries": gop_boundaries,
        "adaptive_palette": adaptive,
        "level_blocks": level_blocks,
        "index": index,
        "chunks": chunks,
    }
    if depth is not None and solid_rule is not None:
        # 2.5D: 符号化したのは (2 × 高さ) 行 × 幅 列の平面 (上が色、下が値)。アドオンが立体に組み立てる
        data.update({"layout": PLANES_LAYOUT, "depth": depth, "air_level": AIR_LEVEL, "solid_rule": solid_rule})
    elif depth is not None:
        # 立体: 盤面は (奥行き × 高さ) 行 × 幅 列。空気は air_level 番で表す
        data.update({"layout": "volume", "depth": depth, "air_level": AIR_LEVEL})
    else:
        data["layout"] = "screen"
    return data


def write_frame_data(args, frame_data):
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        f"export const FRAME_DATA = {json.dumps(frame_data, ensure_ascii=False)};\n", encoding="utf-8"
    )
    return out_path


def print_stats(out_path, encoder, num_gops, avg_ssim=0.0):
    stats = encoder.stats
    print(f"[MVCodec Benchmark] SSIM: {avg_ssim:.4f}")
    print(f"[MVCodec Benchmark] FileSizeKB: {out_path.stat().st_size / 1024:.2f}")
    print(f"[MVCodec Benchmark] TotalFrames: {stats.total_frames}")
    print(f"[MVCodec Benchmark] SkippedFrames: {stats.skipped_frames}")
    print(f"[MVCodec Benchmark] KeyFrames: {stats.keyframes}")
    print(f"[MVCodec Benchmark] MotionFrames: {stats.motion_frames}")
    print(f"[MVCodec Benchmark] TileFrames: {stats.tile_frames}")
    print(f"[MVCodec Benchmark] TileRefs: {stats.tile_refs}")
    print(f"[MVCodec Benchmark] GOPs: {num_gops}")


class EncoderSet:
    """タイルの大きさだけを変えた複数のエンコーダに同じフレームを流し、一番小さい結果を使う。"""

    def __init__(self, encoders):
        self.encoders = encoders

    def add(self, frame_no, gop_id, frame):
        for encoder in self.encoders:
            encoder.add(frame_no, gop_id, frame)

    def best(self):
        return min(self.encoders, key=lambda encoder: encoder.payload_size())


def make_encoder(args, width, rows, gop_boundaries, empty_value, depth=1, change_ratio_fn=None):
    tile_sizes = [4, 8] if args.tile_size == "auto" else [int(args.tile_size)]
    motion_cache = {} if len(tile_sizes) > 1 else None
    return EncoderSet([
        StreamEncoder(
            width=width, rows=rows, gop_boundaries=gop_boundaries, keyframe_interval=args.keyframe_interval,
            adaptive_fps=args.adaptive_fps, scene_threshold=args.scene_threshold, motion=args.motion,
            mv_range=args.mv_range, empty_value=empty_value, tile_size=tile_size, motion_cache=motion_cache,
            depth=depth, change_ratio_fn=change_ratio_fn,
        )
        for tile_size in tile_sizes
    ])


def convert_vox(args):
    """MagicaVoxel の立体アニメを変換する (減色はブロック色への最近傍対応だけ)。"""
    frames, vox_palettes = load_vox_frames_with_palettes(args.input_vox)
    palette = PALETTES["full" if args.palette == "auto" else args.palette]
    block_rgb = np.array([item["rgb"] for item in palette], dtype=np.float64)
    tables = {}

    def color_table(vox_palette):
        """vox の色番号 → ブロックのパレット番号 (ファイルごとにパレットが違うことがあるので、パレットごとに作る)。"""
        key = vox_palette.tobytes()
        if key not in tables:
            table = np.full(256, AIR_LEVEL, dtype=np.int16)
            table[1:] = nearest_levels(vox_palette[:255, :3], block_rgb)  # 色番号 i は palette[i - 1]
            tables[key] = table
        return tables[key]

    depth, height, width = vox_volume_shape(frames)
    total = len(frames)
    gop_boundaries = list(range(0, total, MAX_GOP_SIZE)) + [total]
    encoder = make_encoder(args, width, depth * height, gop_boundaries, AIR_LEVEL, depth)
    gop_ids = iter_gop_ids(gop_boundaries)
    progress = Progress(total)
    for i, frame in enumerate(frames):
        encoder.add(i, next(gop_ids), vox_to_volume(frame, (depth, height, width), color_table(vox_palettes[i])))
        progress.update(i + 1)
    encoder = encoder.best()
    chunks, index = encoder.finish()
    frame_data = build_frame_data(args, width, height, gop_boundaries, [palette], False, encoder, chunks, index, depth)
    out_path = write_frame_data(args, frame_data)
    print_stats(out_path, encoder, len(gop_boundaries) - 1)
    return 0


def main(argv=None):
    global _tr
    _use_utf8_for_pipes()
    args = parse_args(argv)
    _tr = Translator(args.lang or detect_system_language())
    if args.mode == "vox":
        return convert_vox(args)

    width, height = args.width, args.height
    volume = args.mode in ("relief", "extrude", "inflate")
    frame_source = make_frame_source(args)
    gpu = select_gpu(args.device, args.dither_method, args.perceptual)

    # --- Pass 1: GOP 分割 (+ 自動パレット) ---
    is_adaptive_palette = args.palette == "auto"
    palette_builder = make_auto_palette_builder(gpu) if is_adaptive_palette else None
    print(_tr("convert.analyzing"))
    gop_boundaries, adaptive_palettes = analyze_gops(frame_source(), palette_builder)
    total_frames = gop_boundaries[-1]
    if total_frames == 0:
        print(_tr("convert.no_frames"), file=sys.stderr)
        return 1
    num_gops = len(gop_boundaries) - 1

    if is_adaptive_palette:
        gop_palettes = adaptive_palettes
    else:
        gop_palettes = [PALETTES[args.palette]] * num_gops
    if max(len(p) for p in gop_palettes) > MAX_PALETTE_COLORS - (1 if volume else 0):
        raise ValueError(f"パレットは最大 {MAX_PALETTE_COLORS} 色までです")
    gop_palettes_rgb = []
    rgb_cache = {}
    for pal in gop_palettes:
        if id(pal) not in rgb_cache:
            rgb_cache[id(pal)] = np.array([item["rgb"] for item in pal], dtype=np.float64)
        gop_palettes_rgb.append(rgb_cache[id(pal)])

    # --- Pass 2: 減色 ---
    # 評価 (SSIM) 用に元フレームを間引いて保持する。減色側が先読みしても番号で取り出せるよう dict にする
    sampled_originals = {}
    # 立体の奥行き (relief) と立体にする範囲 (extrude / inflate) は、減色・ディザ前の元の明るさから決める。
    # ブロック色の明るさを使うと、ディザの粒が奥行きのノイズになり、境目もギザギザになるため
    source_heights = {}
    use_source_heights = volume and not args.depth_video

    def tee_originals(frames):
        for i, f in enumerate(frames):
            if i % SSIM_SAMPLE_INTERVAL == 0 and i // SSIM_SAMPLE_INTERVAL < SSIM_MAX_SAMPLES:
                sampled_originals[i] = f
            if use_source_heights:
                source_heights[i] = luminance01(f)
            yield f

    dither_method = args.dither_method
    pass2_frames = tee_originals(frame_source())
    if gpu is not None and dither_method in GPU_DITHER_METHODS:
        quantized = quantize_frames_gpu(pass2_frames, gop_palettes_rgb, gop_boundaries, width, height,
                                        dither_method, apply_perceptual=args.perceptual, gpu=gpu,
                                        dither_follow_motion=args.dither_follow_motion)
    else:
        quantized = quantize_frames_cpu(pass2_frames, gop_palettes, gop_boundaries, width, height,
                                        dither_method, args.perceptual, args.threads, args.dither_follow_motion,
                                        args.temporal_hysteresis)

    depth_frames = None
    if args.depth_video:
        depth_frames = ffmpeg_frame_generator(args.depth_video, width, height, args.fps, args.duration, args.ffmpeg)

    use_planes = volume and args.volume_encoding != "voxels" and planes_supported(args.mode, args.depth, args.hollow)
    solid_rule = solid_rule_for(args.mode) if use_planes else None
    lifter = VolumeLifter(args.mode, args.depth, threshold=args.extrude_threshold, invert=args.extrude_invert,
                          thickness=args.thickness, make_hollow=args.hollow,
                          hysteresis=args.depth_hysteresis) if volume else None
    if use_planes:
        # 2.5D: 色の平面と値の平面を縦に重ねた 2D 映像として、平面スクリーンと同じ符号化をする
        encoder = make_encoder(args, width, 2 * height, gop_boundaries, -1,
                               change_ratio_fn=volume_change_ratio(width, height, args.depth, solid_rule))
    elif volume:
        encoder = make_encoder(args, width, args.depth * height, gop_boundaries, AIR_LEVEL, args.depth)
    else:
        encoder = make_encoder(args, width, height, gop_boundaries, -1)
    previous_planes = None
    ssim_pairs = []
    gif_frames = []

    progress = Progress(total_frames)
    for i, (frame, gop_id) in enumerate(zip(quantized, iter_gop_ids(gop_boundaries))):
        original = sampled_originals.pop(i, None)
        screen = np.asarray(frame, dtype=np.int32).reshape(height, width)
        if original is not None:
            ssim_pairs.append((original, screen.reshape(-1).copy(), gop_id))
        if args.demo_gif:
            gif_frames.append(gop_palettes_rgb[gop_id].astype(np.uint8)[screen])

        if volume:
            heights = source_heights.pop(i, None)
            if depth_frames is not None:
                depth_frame = next(depth_frames, None)
                if depth_frame is None:
                    raise ValueError(
                        f"--depth-video のフレームが足りません: {i} フレームしかありませんが、入力は {total_frames} フレームです "
                        f"(どちらも {args.fps} fps で読み込みます)。同じ長さの深度動画を指定してください"
                    )
                heights = luminance01(depth_frame)
            encoded_frame = lifter(screen, gop_palettes_rgb[gop_id], heights)
            if use_planes:
                encoded_frame = previous_planes = volume_to_planes(encoded_frame, solid_rule, previous_planes)
        else:
            encoded_frame = screen
        encoder.add(i, gop_id, encoded_frame)
        progress.update(i + 1)

    if depth_frames is not None:
        extra = sum(1 for _ in depth_frames)
        if extra:
            print(f"warning: --depth-video has {extra} more frame(s) than the input; they were ignored", file=sys.stderr)
    encoder = encoder.best()
    chunks, index = encoder.finish()
    if encoder.stats.total_frames != total_frames:
        raise RuntimeError(
            f"1パス目と2パス目でフレーム数が一致しません ({total_frames} != {encoder.stats.total_frames})"
        )
    frame_data = build_frame_data(args, width, height, gop_boundaries, gop_palettes, is_adaptive_palette,
                                  encoder, chunks, index, args.depth if volume else None, solid_rule)
    out_path = write_frame_data(args, frame_data)

    # --- 評価 ---
    avg_ssim = 0.0
    if ssim_pairs:
        scores = [
            calculate_ssim_global(orig, gop_palettes_rgb[gop_id].astype(np.uint8)[idx].reshape(height, width, 3))
            for orig, idx, gop_id in ssim_pairs
        ]
        avg_ssim = float(np.mean(scores))

    if args.demo_gif and gif_frames:
        print("Generating demo GIF...")
        images = [Image.fromarray(f).resize((width * 4, height * 4), Image.Resampling.NEAREST) for f in gif_frames]
        images[0].save(args.demo_gif, save_all=True, append_images=images[1:], optimize=True,
                       duration=int(1000 / args.fps), loop=0)
        print(f"Demo GIF saved to {args.demo_gif}")

    print_stats(out_path, encoder, num_gops, avg_ssim)
    return 0


if __name__ == "__main__":
    sys.exit(main())
