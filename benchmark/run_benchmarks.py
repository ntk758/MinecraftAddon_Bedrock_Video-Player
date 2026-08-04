import argparse
import os
import time
import subprocess
import json
import re
import numpy as np
import cv2
import math

try:
    from skimage.metrics import structural_similarity as ssim
except ImportError:
    ssim = None
    print("Warning: scikit-image not installed. SSIM will be skipped.")

try:
    import torch
    import lpips
    loss_fn_alex = lpips.LPIPS(net='alex')
except ImportError:
    loss_fn_alex = None
    print("Warning: lpips or torch not installed. LPIPS will be skipped.")

def parse_args():
    parser = argparse.ArgumentParser(description="Benchmark Minecraft Bedrock Video Player Conversion")
    parser.add_argument('--video', type=str, required=True, help="Path to original video")
    parser.add_argument('--output', type=str, required=True, help="Path to output frames.js")
    parser.add_argument('--fps', type=int, default=10, help="Target FPS")
    parser.add_argument('--convert_script', type=str, default='convert.py', help="Path to convert.py script")
    return parser.parse_args()

def run_conversion(video_path, output_path, fps, convert_script):
    start_time = time.time()
    cmd = ["python", convert_script, "--video", video_path, "--output", output_path, "--fps", str(fps)]
    print(f"Running conversion: {' '.join(cmd)}")
    subprocess.run(cmd, check=True)
    encode_time = time.time() - start_time
    return encode_time

def parse_js_output(js_path):
    with open(js_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Extract format
    fmt_match = re.search(r'(?:const|let|var)\s+FORMAT\s*=\s*(["\'])(.*?)\1', content)
    fmt = fmt_match.group(2) if fmt_match else "unknown"

    # Extract resolution
    res_match = re.search(r'(?:const|let|var)\s+RESOLUTION\s*=\s*\[(\d+),\s*(\d+)\]', content)
    if res_match:
        resolution = (int(res_match.group(1)), int(res_match.group(2)))
    else:
        resolution = (64, 36) # default fallback

    # Extract frames/level_blocks
    # This assumes frames are stored in an array assigned to a variable like FRAMES or level_blocks
    frames_match = re.search(r'(?:const|let|var)\s+(?:FRAMES|LEVEL_BLOCKS|level_blocks)\s*=\s*(\[.*\])\s*;', content, re.DOTALL)
    frames = []
    if frames_match:
        frames_str = frames_match.group(1)
        try:
            # Simple json parsing, might fail if js is not strict json
            frames = json.loads(frames_str)
        except json.JSONDecodeError:
            print("Warning: Could not parse frames as strict JSON. Decoding may be incomplete.")
    
    return fmt, resolution, frames

# Map basic color IDs to RGB (fallback)
# In practice, use mvcodec.color.ALL_BLOCKS if available
BASIC_COLOR_MAP = {
    0: (0, 0, 0),       # Black
    1: (255, 255, 255), # White
    2: (255, 0, 0),     # Red
    3: (0, 255, 0),     # Green
    4: (0, 0, 255),     # Blue
    5: (255, 255, 0),   # Yellow
    6: (0, 255, 255),   # Cyan
    7: (255, 0, 255),   # Magenta
}

def decode_frames_to_rgb(frames, resolution, fmt):
    width, height = resolution
    decoded = []
    
    try:
        from mvcodec.color import ALL_BLOCKS
        color_map = {i: block.rgb for i, block in enumerate(ALL_BLOCKS)}
    except ImportError:
        color_map = BASIC_COLOR_MAP

    for frame_data in frames:
        img = np.zeros((height, width, 3), dtype=np.uint8)
        
        # Determine if it's a flat list or list of lists (adaptive)
        # Handle accordingly. This is a simplified reconstruction.
        if isinstance(frame_data, list):
            idx = 0
            for y in range(height):
                for x in range(width):
                    if idx < len(frame_data):
                        val = frame_data[idx]
                        if isinstance(val, list):
                            # Maybe [color_id, count] for RLE
                            pass
                        elif isinstance(val, int):
                            rgb = color_map.get(val, (0,0,0))
                            img[y, x] = rgb
                        idx += 1
        decoded.append(img)
    return decoded

def extract_video_frames(video_path, fps, resolution):
    cap = cv2.VideoCapture(video_path)
    frames = []
    
    orig_fps = cap.get(cv2.CAP_PROP_FPS)
    if orig_fps == 0:
        orig_fps = fps
        
    frame_interval = int(round(orig_fps / fps))
    if frame_interval < 1:
        frame_interval = 1
        
    width, height = resolution
    count = 0
    
    while True:
        ret, frame = cap.read()
        if not ret:
            break
            
        if count % frame_interval == 0:
            frame = cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            frames.append(frame)
            
        count += 1
        
    cap.release()
    return frames

def calculate_psnr(img1, img2):
    mse = np.mean((img1 - img2) ** 2)
    if mse == 0:
        return 100
    PIXEL_MAX = 255.0
    return 20 * math.log10(PIXEL_MAX / math.sqrt(mse))

def main():
    args = parse_args()

    encode_time = 0
    if not os.path.exists(args.output):
        print(f"{args.output} not found. Running conversion script...")
        encode_time = run_conversion(args.video, args.output, args.fps, args.convert_script)
    else:
        print(f"{args.output} found. Skipping conversion.")
        
    size_mb = os.path.getsize(args.output) / (1024 * 1024)
        
    fmt, resolution, js_frames = parse_js_output(args.output)
    print(f"Parsed JS: Format={fmt}, Resolution={resolution}, Frames={len(js_frames)}")
    
    decoded_frames = decode_frames_to_rgb(js_frames, resolution, fmt)
    orig_frames = extract_video_frames(args.video, args.fps, resolution)
    
    # Match lengths
    min_len = min(len(decoded_frames), len(orig_frames))
    if min_len == 0:
        print("Error: No frames to compare.")
        return
        
    decoded_frames = decoded_frames[:min_len]
    orig_frames = orig_frames[:min_len]
    
    psnr_vals = []
    ssim_vals = []
    lpips_vals = []
    
    for dec, orig in zip(decoded_frames, orig_frames):
        psnr_vals.append(calculate_psnr(orig, dec))
        
        if ssim is not None:
            val = ssim(orig, dec, channel_axis=-1, data_range=255)
            ssim_vals.append(val)
            
        if loss_fn_alex is not None:
            # Convert to tensor, normalize to [-1, 1]
            t_orig = torch.from_numpy(orig).permute(2, 0, 1).unsqueeze(0).float() / 127.5 - 1.0
            t_dec = torch.from_numpy(dec).permute(2, 0, 1).unsqueeze(0).float() / 127.5 - 1.0
            with torch.no_grad():
                val = loss_fn_alex(t_orig, t_dec).item()
            lpips_vals.append(val)

    avg_psnr = np.mean(psnr_vals)
    avg_ssim = np.mean(ssim_vals) if ssim_vals else float('nan')
    avg_lpips = np.mean(lpips_vals) if lpips_vals else float('nan')
    
    print("\n### Benchmark Results\n")
    print("| Metric | Value |")
    print("|---|---|")
    print(f"| **Encode Time (s)** | {encode_time:.2f} |")
    print(f"| **Output Size (MB)** | {size_mb:.2f} |")
    print(f"| **Average PSNR** | {avg_psnr:.2f} |")
    print(f"| **Average SSIM** | {avg_ssim:.4f} |")
    print(f"| **Average LPIPS** | {avg_lpips:.4f} |")
    print(f"| **FPS Evaluated** | {args.fps} |")

if __name__ == "__main__":
    main()
