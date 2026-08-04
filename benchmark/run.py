import argparse
import os
import numpy as np

from compare import parse_js_output, decode_frames_to_rgb, extract_video_frames
from metrics.timing import measure_conversion_time
from metrics.psnr import calculate_psnr
from metrics.ssim import calculate_ssim
from metrics.ms_ssim import calculate_ms_ssim
from metrics.deltae import calculate_deltae
from report import generate_report

def main():
    parser = argparse.ArgumentParser(description="Modular Benchmark CLI")
    parser.add_argument('--video', type=str, required=True, help="Path to original video")
    parser.add_argument('--output', type=str, required=True, help="Path to output frames.js")
    parser.add_argument('--fps', type=int, default=10, help="Target FPS")
    parser.add_argument('--convert_script', type=str, default='convert.py', help="Path to convert script")
    parser.add_argument('--deep', action='store_true', help="Run deeper metrics like LPIPS")
    args = parser.parse_args()

    encode_time = 0
    if not os.path.exists(args.output):
        print(f"{args.output} not found. Running conversion...")
        encode_time = measure_conversion_time(args.video, args.output, args.fps, args.convert_script)
    else:
        print(f"{args.output} found. Skipping conversion.")

    size_mb = os.path.getsize(args.output) / (1024 * 1024)
    fmt, resolution, js_frames = parse_js_output(args.output)
    print(f"Parsed JS: Format={fmt}, Resolution={resolution}, Frames={len(js_frames)}")

    decoded_frames = decode_frames_to_rgb(js_frames, resolution, fmt)
    orig_frames = extract_video_frames(args.video, args.fps, resolution)

    min_len = min(len(decoded_frames), len(orig_frames))
    if min_len == 0:
        print("Error: No frames to compare.")
        return

    decoded_frames = decoded_frames[:min_len]
    orig_frames = orig_frames[:min_len]

    psnr_vals = []
    ssim_vals = []
    ms_ssim_vals = []
    deltae_vals = []
    lpips_vals = []

    if args.deep:
        from metrics.lpips_metric import calculate_lpips

    for dec, orig in zip(decoded_frames, orig_frames):
        psnr_vals.append(calculate_psnr(orig, dec))
        ssim_vals.append(calculate_ssim(orig, dec))
        ms_ssim_vals.append(calculate_ms_ssim(orig, dec))
        deltae_vals.append(calculate_deltae(orig, dec))
        if args.deep:
            lpips_vals.append(calculate_lpips(orig, dec))

    results = {
        "encode_time": encode_time,
        "size_mb": size_mb,
        "fps": args.fps,
        "psnr": np.nanmean(psnr_vals) if psnr_vals else float('nan'),
        "ssim": np.nanmean(ssim_vals) if ssim_vals else float('nan'),
        "ms_ssim": np.nanmean(ms_ssim_vals) if ms_ssim_vals else float('nan'),
        "deltae": np.nanmean(deltae_vals) if deltae_vals else float('nan')
    }
    if args.deep:
        results["lpips"] = np.nanmean(lpips_vals) if lpips_vals else float('nan')

    generate_report(results)

if __name__ == "__main__":
    main()
