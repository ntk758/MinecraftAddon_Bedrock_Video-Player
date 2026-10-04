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
from mvcodec.encode import (
    MAX_PALETTE_COLORS,
    bytearray_to_utf16_str,
    encode_varint,
    extract_rle_chunks_fast,
    pack_run,
)
from mvcodec.evaluate import calculate_ssim_global

WIDTH = 64
HEIGHT = 64
OUTPUT_DIR = "BP/scripts"
OUTPUT_FILE = f"{OUTPUT_DIR}/frames_data.js"
FORMAT_NAME = "varint_rle_v4"
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

try:
    import torch
    HAS_TORCH_CUDA = torch.cuda.is_available()
except ImportError:
    HAS_TORCH_CUDA = False


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


def make_auto_palette_builder(use_gpu):
    from mvcodec.auto_palette import generate_auto_palette

    cache = {}

    def build(samples, first_frame):
        # 同じ見た目で始まる GOP はパレットを使い回す (JS 側の容量節約)
        thumb_hash = hashlib.md5(np.ascontiguousarray(first_frame[::8, ::8]).tobytes()).hexdigest()
        if thumb_hash not in cache:
            cache[thumb_hash] = generate_auto_palette(
                samples, ALL_BLOCKS, max_colors=AUTO_PALETTE_MAX_COLORS, use_gpu=use_gpu, sample_stride=1
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
                        dither_method="none", apply_perceptual=True):
    """GPU (CUDA) で OkLab 色空間マッチング + RDO + 時間方向ディザを行い、フレームごとに (H, W) のパレット番号を返す。

    パレットが切り替わる GOP 境界では前フレーム参照 (RDO / 時間方向ディザ) をリセットする。
    """
    import torch
    from mvcodec.color import generate_blue_noise_approx_numpy, get_edge_mask_gpu

    def get_pal_tensors(pal_data):
        t = torch.tensor(pal_data, dtype=torch.float32, device="cuda")
        return t, rgb_to_oklab_torch(t)

    dither_tensor = None
    if dither_method == "ordered":
        bayer = np.array([
            [0, 8, 2, 10],
            [12, 4, 14, 6],
            [3, 11, 1, 9],
            [15, 7, 13, 5]
        ], dtype=np.float32)
        bayer = ((bayer / 16.0) - 0.5) * 32.0
        bayer_tiled = np.tile(bayer, (height // 4 + 1, width // 4 + 1))[:height, :width]
        dither_tensor = torch.tensor(np.stack([bayer_tiled] * 3, axis=-1), dtype=torch.float32, device="cuda")
    elif dither_method == "blue_noise":
        bn = (generate_blue_noise_approx_numpy(width, height) - 0.5) * 64.0
        dither_tensor = torch.tensor(np.stack([bn] * 3, axis=-1), dtype=torch.float32, device="cuda")

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
            error_buffer = torch.zeros((height, width, 3), dtype=torch.float32, device="cuda")
            importance_buffer = torch.zeros((height, width), dtype=torch.float32, device="cuda")
            prev_idx_tensor = None
            prev_orig_img_tensor = None

        orig_img_tensor = torch.tensor(img_arr, dtype=torch.float32, device="cuda")
        img_tensor = orig_img_tensor.clone()

        # Temporal Dithering (前フレームからの誤差を加算)
        img_tensor += error_buffer

        if dither_tensor is not None:
            bias = dither_tensor * get_edge_mask_gpu(img_tensor) if apply_perceptual else dither_tensor
            img_tensor += bias
            img_tensor = torch.clamp(img_tensor, 0, 255)

        # OkLab 知覚色空間でのカラーマッチング
        img_oklab = rgb_to_oklab_torch(img_tensor.view(-1, 3))
        idx_tensor = torch.argmin(torch.cdist(img_oklab, pal_oklab), dim=1).view(height, width)

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
            torch.cuda.empty_cache()


def quantize_frames_cpu(frames, gop_palettes, gop_boundaries, width, height,
                        dither_method, apply_perceptual, threads):
    """CPU スレッドプールで減色し、フレームごとに (H, W) のパレット番号を返す。"""
    # GOP 間で同じパレットオブジェクトは Pillow パレット画像も共有する
    prepared = {}
    for pal in gop_palettes:
        if id(pal) not in prepared:
            rgb = np.array([item["rgb"] for item in pal], dtype=np.float64)
            prepared[id(pal)] = (create_palette_image(pal), rgb, len(pal))

    def task(job):
        img_arr, pal = job
        pal_img, pal_rgb, num_colors = prepared[id(pal)]
        img = Image.fromarray(img_arr)
        return process_single_frame_img(img, pal_img, width, height, num_colors,
                                        dither_method, apply_perceptual, pal_rgb)

    jobs = ((frame, gop_palettes[gop_id]) for frame, gop_id in zip(frames, iter_gop_ids(gop_boundaries)))
    with ThreadPoolExecutor(max_workers=max(1, threads)) as executor:
        batch = []
        for job in jobs:
            batch.append(job)
            if len(batch) >= CPU_BATCH_SIZE:
                yield from executor.map(task, batch)
                batch = []
        if batch:
            yield from executor.map(task, batch)


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
    parser.add_argument("--output", default=OUTPUT_FILE, help="出力パス")
    parser.add_argument("--width", type=int, default=WIDTH)
    parser.add_argument("--height", type=int, default=HEIGHT)
    parser.add_argument("--fps", type=float, default=20.0, help="出力フレームレート (Minecraft は最大 20fps)")
    parser.add_argument("--duration", type=float, default=None)
    parser.add_argument("--palette", default="full", choices=[*PALETTES.keys(), "auto"])
    parser.add_argument("--dither-method", default="none", choices=["none", "floyd", "ordered", "blue_noise", "atkinson", "burkes", "sierra"])
    parser.add_argument("--perceptual", action=argparse.BooleanOptionalAction, default=True,
                        help="エッジ部のみディザをかける知覚的処理 (--no-perceptual で無効)")
    parser.add_argument("--threads", type=int, default=os.cpu_count() or 4)
    parser.add_argument("--keyframe-interval", type=int, default=30,
                        help="GOP 内のキーフレーム間隔。0 なら各 GOP の先頭のみ")
    parser.add_argument("--gpu", action="store_true", help="CUDA が使えれば GPU を使う (CUDA 検出時は既定で使用)")
    parser.add_argument("--adaptive-fps", action=argparse.BooleanOptionalAction, default=True,
                        help="変化の少ないフレームを省略する (--no-adaptive-fps で無効)")
    parser.add_argument("--scene-threshold", type=float, default=0.015,
                        help="adaptive-fps でフレームを省略する変化率のしきい値")
    parser.add_argument("--ffmpeg", default="ffmpeg", help="ffmpeg 実行ファイルのパス")
    parser.add_argument("--demo-gif", default=None, help="指定されたパスにデモ用GIFを出力する")
    args = parser.parse_args(argv)

    if args.width < 1 or args.height < 1:
        parser.error("--width / --height は 1 以上にしてください")
    if not 0 < args.fps <= 20:
        parser.error("--fps は 0 より大きく 20 以下にしてください")
    if args.keyframe_interval < 0:
        parser.error("--keyframe-interval は 0 以上にしてください")
    return args


def level_block_specs(palette):
    specs = []
    for item in palette:
        spec = {"block": item["block"]}
        if "states" in item:
            spec["states"] = item["states"]
        specs.append(spec)
    return specs


def main(argv=None):
    args = parse_args(argv)
    width, height = args.width, args.height
    frame_source = make_frame_source(args)
    # CUDA が使える環境では常に GPU を使う (--gpu は互換のため残している)
    use_gpu = HAS_TORCH_CUDA

    # --- Pass 1: GOP 分割 (+ 自動パレット) ---
    is_adaptive_palette = args.palette == "auto"
    palette_builder = make_auto_palette_builder(use_gpu) if is_adaptive_palette else None
    gop_boundaries, adaptive_palettes = analyze_gops(frame_source(), palette_builder)
    total_frames = gop_boundaries[-1]
    if total_frames == 0:
        print("入力フレームがありません。", file=sys.stderr)
        return 1
    num_gops = len(gop_boundaries) - 1

    if is_adaptive_palette:
        gop_palettes = adaptive_palettes
    else:
        gop_palettes = [PALETTES[args.palette]] * num_gops
    if max(len(p) for p in gop_palettes) > MAX_PALETTE_COLORS:
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

    def tee_originals(frames):
        for i, f in enumerate(frames):
            if i % SSIM_SAMPLE_INTERVAL == 0 and i // SSIM_SAMPLE_INTERVAL < SSIM_MAX_SAMPLES:
                sampled_originals[i] = f
            yield f

    dither_method = args.dither_method
    pass2_frames = tee_originals(frame_source())
    if use_gpu and dither_method in ("none", "ordered", "blue_noise"):
        quantized = quantize_frames_gpu(pass2_frames, gop_palettes_rgb, gop_boundaries, width, height,
                                        dither_method, apply_perceptual=args.perceptual)
    else:
        quantized = quantize_frames_cpu(pass2_frames, gop_palettes, gop_boundaries, width, height,
                                        dither_method, args.perceptual, args.threads)

    gop_starts = set(gop_boundaries[:-1])
    keyframe_interval = args.keyframe_interval

    gop_utf16_strings = []
    gop_binary = bytearray()
    frame_index_entries = []  # (gop_id, byte_offset_in_gop, byte_length, is_keyframe)
    prev_frame = None
    skipped_frames = 0
    keyframes_count = 0
    ssim_pairs = []
    gif_frames = []
    current_gop = 0
    gop_start = 0

    for i, (frame, gop_id) in enumerate(zip(quantized, iter_gop_ids(gop_boundaries))):
        original = sampled_originals.pop(i, None)
        flat_frame = np.asarray(frame, dtype=np.int32).reshape(-1)

        if gop_id != current_gop:
            gop_utf16_strings.append(bytearray_to_utf16_str(gop_binary))
            gop_binary = bytearray()
            current_gop = gop_id
        if i in gop_starts:
            gop_start = i

        # GOP 先頭は必ずキーフレーム: パレットが変わってもシーク・遅延デコードが GOP 単体で完結する
        is_keyframe = i in gop_starts or (keyframe_interval > 0 and (i - gop_start) % keyframe_interval == 0)

        if original is not None:
            ssim_pairs.append((original, flat_frame.copy(), gop_id))
        if args.demo_gif:
            gif_frames.append(gop_palettes_rgb[gop_id].astype(np.uint8)[flat_frame].reshape(height, width, 3))

        offset_in_gop = len(gop_binary)
        if not is_keyframe and args.adaptive_fps and np.mean(flat_frame != prev_frame) < args.scene_threshold:
            skipped_frames += 1
            frame_index_entries.append((gop_id, offset_in_gop, 0, False))
            continue

        if is_keyframe:
            keyframes_count += 1
            chunks = extract_rle_chunks_fast(flat_frame, np.full_like(flat_frame, -1), width, height)
        else:
            chunks = extract_rle_chunks_fast(flat_frame, prev_frame, width, height)

        encoded = bytearray()
        encode_varint((i << 1) | (1 if is_keyframe else 0), encoded)
        encode_varint(len(chunks), encoded)
        for start_x, y, length, color in chunks:
            encode_varint(pack_run(start_x, y, length, color, width), encoded)

        gop_binary.extend(encoded)
        frame_index_entries.append((gop_id, offset_in_gop, len(encoded), is_keyframe))
        prev_frame = flat_frame

    gop_utf16_strings.append(bytearray_to_utf16_str(gop_binary))
    if len(frame_index_entries) != total_frames:
        raise RuntimeError(
            f"1パス目と2パス目でフレーム数が一致しません ({total_frames} != {len(frame_index_entries)})"
        )

    index_data = bytearray()
    for gop_id, offset_val, length_val, is_kf in frame_index_entries:
        encode_varint(gop_id, index_data)
        encode_varint((offset_val << 1) | (1 if is_kf else 0), index_data)
        encode_varint(length_val, index_data)

    if is_adaptive_palette:
        level_blocks = [level_block_specs(pal) for pal in gop_palettes]
    else:
        level_blocks = level_block_specs(gop_palettes[0])

    frame_data = {
        "width": width,
        "height": height,
        "fps": args.fps,
        "frame_count": total_frames,
        "keyframe_interval": keyframe_interval,
        "format": FORMAT_NAME,
        "gop_boundaries": gop_boundaries,
        "adaptive_palette": is_adaptive_palette,
        "level_blocks": level_blocks,
        "index": bytearray_to_utf16_str(index_data),
        "chunks": gop_utf16_strings,
    }

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        f"export const FRAME_DATA = {json.dumps(frame_data, ensure_ascii=False)};\n", encoding="utf-8"
    )

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

    print(f"[MVCodec Benchmark] SSIM: {avg_ssim:.4f}")
    print(f"[MVCodec Benchmark] FileSizeKB: {out_path.stat().st_size / 1024:.2f}")
    print(f"[MVCodec Benchmark] TotalFrames: {total_frames}")
    print(f"[MVCodec Benchmark] SkippedFrames: {skipped_frames}")
    print(f"[MVCodec Benchmark] KeyFrames: {keyframes_count}")
    print(f"[MVCodec Benchmark] GOPs: {num_gops}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
