import os
import sys
import glob
import json
import argparse
import base64
import subprocess
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import numpy as np
from PIL import Image

# Import MVCodec modules
from mvcodec.color import (
    PALETTES,
    create_palette_image,
    process_single_frame_img,
    rgb_to_oklab_torch
)
from mvcodec.encode import (
    encode_varint,
    bytearray_to_utf16_str,
    extract_rle_chunks
)
from mvcodec.evaluate import calculate_ssim_global

WIDTH = 64
HEIGHT = 64
FRAMES_DIR = "frames"
OUTPUT_DIR = "BP/scripts"
OUTPUT_FILE = f"{OUTPUT_DIR}/frames_data.js"

try:
    import torch
    HAS_TORCH_CUDA = torch.cuda.is_available()
except ImportError:
    HAS_TORCH_CUDA = False

def process_frames_gpu(frames_iter, palette_rgb, width, height, dither_method="none", apply_perceptual=True):
    import torch
    from mvcodec.color import generate_blue_noise_approx_numpy, get_edge_mask_gpu
    
    num_colors = len(palette_rgb)
    pal_tensor = torch.tensor(palette_rgb, dtype=torch.float32, device="cuda")
    processed_frames = []

    dither_tensor = None
    if dither_method == "ordered":
        bayer = np.array([
            [0, 8, 2, 10],
            [12, 4, 14, 6],
            [3, 11, 1, 9],
            [15, 7, 13, 5]
        ], dtype=np.float32)
        bayer = (bayer / 16.0) - 0.5
        bayer *= 32.0
        bayer_tiled = np.tile(bayer, (height // 4 + 1, width // 4 + 1))[:height, :width]
        bayer_tiled = np.stack([bayer_tiled]*3, axis=-1)
        dither_tensor = torch.tensor(bayer_tiled, dtype=torch.float32, device="cuda")
    elif dither_method == "blue_noise":
        bn = generate_blue_noise_approx_numpy(width, height)
        bn = (bn - 0.5) * 64.0 # Spread
        bn_tiled = np.stack([bn]*3, axis=-1)
        dither_tensor = torch.tensor(bn_tiled, dtype=torch.float32, device="cuda")

    error_buffer = torch.zeros((height, width, 3), dtype=torch.float32, device="cuda")
    prev_idx_tensor = None
    prev_orig_img_tensor = None
    rdo_threshold = 20.0
    me_threshold = 5.0 # 背景と見なす元画像の最大色差
    temporal_dither_weight = 0.5

    for i, img_arr in enumerate(frames_iter):
        orig_img_tensor = torch.tensor(img_arr, dtype=torch.float32, device="cuda")
        img_tensor = orig_img_tensor.clone()
        
        # Temporal Dithering (前フレームからの誤差を加算)
        img_tensor += error_buffer
        
        if dither_tensor is not None:
            if apply_perceptual:
                mask = get_edge_mask_gpu(img_tensor)
                bias = dither_tensor * mask
            else:
                bias = dither_tensor
            img_tensor += bias
            img_tensor = torch.clamp(img_tensor, 0, 255)

        dists = torch.cdist(img_tensor.view(-1, 3), pal_tensor)
        idx_tensor = torch.argmin(dists, dim=1).view(height, width)
        
        # Rate-Distortion Optimization (RDO) & Motion Estimation (ME)
        if prev_idx_tensor is not None and prev_orig_img_tensor is not None:
            # 1. ME Mask (背景静止化)
            # オリジナル画像同士の絶対誤差(SAD)を計算し、小さなノイズレベルの変化なら「静止」とみなす
            diff_orig = torch.abs(orig_img_tensor - prev_orig_img_tensor).mean(dim=-1) # (H, W)
            # PyTorchのAvgPool2dを使って3x3領域で平均化し、孤立したノイズを除去（周辺も静止しているか確認）
            diff_orig_pool = torch.nn.functional.avg_pool2d(
                diff_orig.unsqueeze(0).unsqueeze(0), kernel_size=3, stride=1, padding=1
            ).squeeze(0).squeeze(0)
            
            me_mask = diff_orig_pool < me_threshold
            
            # 2. RDO (ディザリング抑制)
            prev_colors = pal_tensor[prev_idx_tensor] # (H, W, 3)
            dist_to_prev = torch.norm(img_tensor - prev_colors, dim=-1) # (H, W)
            rdo_mask = dist_to_prev < rdo_threshold
            
            # MEで完全に静止しているか、RDOで許容範囲なら再利用
            reuse_mask = me_mask | rdo_mask
            idx_tensor = torch.where(reuse_mask, prev_idx_tensor, idx_tensor)
            
        # Temporal Dithering のための誤差計算
        selected_colors = pal_tensor[idx_tensor]
        error_buffer = (img_tensor - selected_colors) * temporal_dither_weight
        # 誤差が蓄積しすぎないように減衰・クランプ
        error_buffer = torch.clamp(error_buffer, -32.0, 32.0)
        
        processed_frames.append(idx_tensor.cpu().numpy().astype(np.int32) % num_colors)
        prev_idx_tensor = idx_tensor.clone()
        prev_orig_img_tensor = orig_img_tensor.clone()

        del orig_img_tensor, img_tensor, dists, idx_tensor, selected_colors
        if i % 64 == 63:
            torch.cuda.empty_cache()

    return processed_frames

def parse_args():
    parser = argparse.ArgumentParser(
        description="MVCodec - Minecraft Video Codec: PNG連番や動画をBedrock用フレーム差分データへ超高速変換します。"
    )
    parser.add_argument("--output", default=OUTPUT_FILE, help="出力パス")
    parser.add_argument("--width", type=int, default=WIDTH)
    parser.add_argument("--height", type=int, default=HEIGHT)
    parser.add_argument("--input-video", default=None, help="入力動画ファイル")
    parser.add_argument("--fps", type=float, default=20.0)
    parser.add_argument("--duration", type=float, default=None)
    parser.add_argument("--palette", default="full", choices=["concrete", "expanded", "full", "all_55", "ultra_110", "auto"])
    parser.add_argument("--dither-method", default="none", choices=["none", "floyd", "ordered", "blue_noise", "atkinson", "burkes", "sierra"])
    parser.add_argument("--perceptual", action="store_true", default=True)
    parser.add_argument("--no-perceptual", dest="perceptual", action="store_false")
    parser.add_argument("--video-id", default=None)
    parser.add_argument("--threads", type=int, default=os.cpu_count() or 4)
    parser.add_argument("--keyframe-interval", type=int, default=30)
    parser.add_argument("--gpu", action="store_true")
    parser.add_argument("--adaptive-fps", action="store_true", default=True)
    parser.add_argument("--scene-threshold", type=float, default=0.015)
    parser.add_argument("--demo-gif", default=None, help="指定されたパスにデモ用GIFを出力する")
    return parser.parse_args()

def ffmpeg_frame_generator(video_path, width, height, fps, duration=None):
    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-vf", f"fps={fps},scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-ih)/2:(oh-ih)/2:black",
        "-f", "rawvideo", "-pix_fmt", "rgb24",
    ]
    if duration is not None:
        cmd.extend(["-t", str(duration)])
    cmd.append("-")
    
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    frame_size = width * height * 3
    while True:
        raw = proc.stdout.read(frame_size)
        if not raw or len(raw) < frame_size:
            break
        yield np.frombuffer(raw, dtype=np.uint8).reshape((height, width, 3))
    proc.stdout.close()
    proc.wait()

def ffmpeg_scene_detection(video_path, fps, duration=None):
    import re
    # FFmpegの select フィルタを使ってシーンチェンジフレームを検出する
    cmd = [
        "ffmpeg", "-i", video_path,
        "-vf", f"fps={fps},select='gt(scene,0.1)'",
        "-f", "null", "-"
    ]
    if duration is not None:
        cmd = [
        "ffmpeg", "-t", str(duration), "-i", video_path,
        "-vf", f"fps={fps},select='gt(scene,0.1)'",
        "-f", "null", "-"
        ]
        
    proc = subprocess.Popen(cmd, stderr=subprocess.PIPE, text=True, encoding='utf-8')
    scene_frames = []
    
    for line in proc.stderr:
        # parsed_scene=0.1500 などは出るが、実際には frame=15 のようなログから取得する方が確実かも。
        # 今回は簡易実装。
        pass
    proc.wait()
    return scene_frames

def main():
    args = parse_args()
    WIDTH = args.width
    HEIGHT = args.height
    
    if args.palette == "auto":
        from mvcodec.color import ALL_BLOCKS
        from mvcodec.auto_palette import generate_auto_palette
        
        # サンプリング用に一度ジェネレータを回すのは重いので、本来ならここで動画パスから専用関数を呼ぶ。
        # 今回は簡易的にジェネレータを作る
        temp_iter = ffmpeg_frame_generator(args.input_video, WIDTH, HEIGHT, args.fps, args.duration) if args.input_video else []
        palette = generate_auto_palette(temp_iter, ALL_BLOCKS, max_colors=64, use_gpu=(args.gpu or HAS_TORCH_CUDA))
    else:
        palette = PALETTES[args.palette]
        
    num_colors = len(palette)
    pal_img = create_palette_image(palette)
    
    if args.input_video:
        # SSIMサンプリングのために元のフレームを少し保持する
        sample_interval = 10
        sampled_originals = []
        
        def iter_wrapper():
            for i, frame in enumerate(ffmpeg_frame_generator(args.input_video, WIDTH, HEIGHT, args.fps, args.duration)):
                if i % sample_interval == 0 and len(sampled_originals) < 50:
                    sampled_originals.append(frame)
                yield frame
        frames_iter = iter_wrapper()
    else:
        frames_iter = []
        sampled_originals = []

    use_gpu = args.gpu or HAS_TORCH_CUDA
    palette_rgb_arr = np.array([item['rgb'] for item in palette], dtype=np.float64)
    dither_method = args.dither_method

    if use_gpu and HAS_TORCH_CUDA and dither_method in ('none', 'ordered', 'blue_noise'):
        processed_frames = process_frames_gpu(frames_iter, palette_rgb_arr, WIDTH, HEIGHT, dither_method, apply_perceptual=args.perceptual)
    else:
        palette_rgb_for_dither = palette_rgb_arr if dither_method in ('ordered', 'atkinson', 'burkes', 'sierra') else None
        frames_list = list(frames_iter)
        
        def task(img_arr):
            img = Image.fromarray(img_arr)
            return process_single_frame_img(img, pal_img, WIDTH, HEIGHT, num_colors, dither_method, args.perceptual, palette_rgb_for_dither)

        max_workers = min(args.threads, len(frames_list) if frames_list else 1)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            processed_frames = list(executor.map(task, frames_list))

    total_frames = len(processed_frames)
    if total_frames == 0:
        return

    keyframe_interval = args.keyframe_interval
    adaptive_fps = args.adaptive_fps
    scene_threshold = args.scene_threshold

    encoded_data = bytearray()
    
    prev_frame = np.full(WIDTH * HEIGHT, -1, dtype=np.int32)
    
    frame_indices = []
    skipped_frames = 0
    keyframes_count = 0
    
    for i, frame in enumerate(processed_frames):
        flat_frame = frame.flatten()
        is_keyframe = (keyframe_interval > 0 and i % keyframe_interval == 0)

        if not is_keyframe and adaptive_fps and i > 0:
            diff_ratio = np.mean(flat_frame != prev_frame)
            if diff_ratio < scene_threshold:
                skipped_frames += 1
                continue
        
        if is_keyframe:
            keyframes_count += 1
            chunks = extract_rle_chunks(flat_frame, np.full_like(flat_frame, -1), WIDTH, HEIGHT, num_colors)
        else:
            chunks = extract_rle_chunks(flat_frame, prev_frame, WIDTH, HEIGHT, num_colors)
            
        frame_indices.append(i)
        
        chunk_count = len(chunks)
        encode_varint((i << 1) | (1 if is_keyframe else 0), encoded_data)
        encode_varint(chunk_count, encoded_data)
        
        for start_x, y, length, color in chunks:
            delta = y * WIDTH + start_x
            data_val = (delta << 13) | ((length - 1) << 7) | (color & 0x7f)
            encode_varint(data_val, encoded_data)
            
        prev_frame = flat_frame.copy()

    base64_str = base64.b64encode(encoded_data).decode('ascii')
    utf16_str = bytearray_to_utf16_str(encoded_data)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    
    export_var = "FRAME_DATA"
    if args.video_id:
        # export_var = f"FRAME_DATA_{args.video_id}"  # keep generic for now
        pass
        
    js_content = f"""export const {export_var} = {{
  width: {WIDTH},
  height: {HEIGHT},
  frames: {total_frames},
  binary: "{utf16_str}",
  format: "varint_rle_v2"
}};
"""
    out_path.write_text(js_content, encoding='utf-8')
    
    # Benchmarking
    import time
    file_size_kb = out_path.stat().st_size / 1024
    
    avg_ssim = 0.0
    if sampled_originals and len(processed_frames) >= len(sampled_originals):
        ssim_sum = 0.0
        count = 0
        for idx, orig_img in enumerate(sampled_originals):
            proc_idx = idx * 10
            if proc_idx < len(processed_frames):
                # 変換後のインデックスをRGBに戻す
                p_frame_flat = np.array(processed_frames[proc_idx]).flatten()
                p_rgb = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)
                for y in range(HEIGHT):
                    for x in range(WIDTH):
                        color_id = p_frame_flat[y * WIDTH + x]
                        if color_id < num_colors:
                            p_rgb[y, x] = palette[color_id]['rgb']
                
                ssim_val = calculate_ssim_global(orig_img, p_rgb)
                ssim_sum += ssim_val
                count += 1
        if count > 0:
            avg_ssim = ssim_sum / count
            
    # --- Demo GIF Generation ---
    if args.demo_gif and len(processed_frames) > 0:
        print("Generating demo GIF...")
        gif_frames = []
        for p_frame in processed_frames:
            p_frame_flat = np.array(p_frame).flatten()
            p_rgb = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)
            for y in range(HEIGHT):
                for x in range(WIDTH):
                    color_id = p_frame_flat[y * WIDTH + x]
                    if color_id < num_colors:
                        p_rgb[y, x] = palette[color_id]['rgb']
            
            img = Image.fromarray(p_rgb)
            img = img.resize((WIDTH * 4, HEIGHT * 4), Image.Resampling.NEAREST)
            gif_frames.append(img)
            
        gif_frames[0].save(
            args.demo_gif,
            save_all=True,
            append_images=gif_frames[1:],
            optimize=True,
            duration=int(1000 / args.fps),
            loop=0
        )
        print(f"Demo GIF saved to {args.demo_gif}")
            
    print(f"[MVCodec Benchmark] SSIM: {avg_ssim:.4f}")
    print(f"[MVCodec Benchmark] FileSizeKB: {file_size_kb:.2f}")
    print(f"[MVCodec Benchmark] TotalFrames: {total_frames}")
    print(f"[MVCodec Benchmark] SkippedFrames: {skipped_frames}")
    print(f"[MVCodec Benchmark] KeyFrames: {keyframes_count}")

if __name__ == "__main__":
    main()
