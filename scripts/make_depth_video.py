"""動画から、relief の奥行きに使う深度動画 (明るいほど手前のグレースケール) を AI の単眼深度推定で作る (Phase 8.7)。

既定のモデルは Depth Anything V2 Small (Hugging Face の depth-anything/Depth-Anything-V2-Small-hf、Apache-2.0、約 100 MB)。
初回の実行でモデルをダウンロードする。作った動画は convert.py の --depth-video に渡す:

    pip install transformers torch
    python scripts/make_depth_video.py input.mp4 depth.mp4 --width 128 --height 72 --fps 10
    python convert.py --input-video input.mp4 --depth-video depth.mp4 --mode relief --width 128 --height 72 --fps 10 ...

--width / --height / --fps は convert.py と同じにする (フレームの数と位置をそろえるため。違っても convert.py が
同じ大きさ・fps で読み直すが、余計なぼけが入る)。

深度推定はフレームごとの推定なので、そのままでは奥行きがちらつく。ここでは次の 2 つで扱う:
- 明るさの範囲: 各フレームの 2〜98 パーセンタイルを 0〜255 にする。範囲をゆっくり追いかける (--range-smoothing を
  1 より小さく) と、場面の切り替わりで範囲が合わず、かえって置き直すブロックが増えた (Sintel の予告編・128×72・
  10 fps・奥行き 16 で、1 フレームに変わるマス 4240 → 5166 (0.1))。既定はフレームごと (1)
- 画素の値: 前のフレームとの指数移動平均 (--temporal-smoothing、既定 0.3、0 で無効)。同じ条件で 4240 → 3734 (−12%)。
  大きくすると動きの速い映像で残像になる (0.6 では 3942 とかえって増えた)
convert.py の --depth-hysteresis (奥行きの段が少し揺れただけでは変えない) も合わせて効く。
時間方向に一貫した推定をする Video Depth Anything (https://github.com/DepthAnything/Video-Depth-Anything) で
作った深度動画も、明るいほど手前のグレースケールならそのまま使える (遠いほど明るい形式なら --invert)。
"""
import argparse
import subprocess
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DEFAULT_MODEL = "depth-anything/Depth-Anything-V2-Small-hf"


class DepthNormalizer:
    """推定した深度 (大きいほど手前) を、ちらつきを抑えて 0〜255 の明るさにする。"""

    def __init__(self, range_smoothing=1.0, temporal_smoothing=0.3, invert=False):
        self.range_smoothing = range_smoothing
        self.temporal_smoothing = temporal_smoothing
        self.invert = invert
        self.low = self.high = None
        self.previous = None

    def __call__(self, depth):
        depth = np.asarray(depth, dtype=np.float32)
        if self.invert:
            depth = -depth
        low, high = np.percentile(depth, (2, 98))
        if self.low is None:
            self.low, self.high = float(low), float(high)
        else:
            k = self.range_smoothing
            self.low += k * (float(low) - self.low)
            self.high += k * (float(high) - self.high)
        scaled = np.clip((depth - self.low) / max(self.high - self.low, 1e-6), 0.0, 1.0)
        if self.previous is not None and self.previous.shape == scaled.shape and self.temporal_smoothing > 0:
            scaled = self.temporal_smoothing * self.previous + (1 - self.temporal_smoothing) * scaled
        self.previous = scaled
        return np.round(scaled * 255).astype(np.uint8)


def load_estimator(model, device):
    """frame (H, W, 3) uint8 -> 深度 (H, W) float (大きいほど手前) の関数。"""
    from PIL import Image
    from transformers import pipeline

    estimator = pipeline("depth-estimation", model=model, device=device)

    def estimate(frame):
        result = estimator(Image.fromarray(frame))
        depth = result["predicted_depth"]
        depth = depth.squeeze().detach().cpu().numpy() if hasattr(depth, "detach") else np.asarray(depth)
        if depth.shape != frame.shape[:2]:
            depth = np.asarray(Image.fromarray(depth.astype(np.float32)).resize(frame.shape[1::-1], Image.BILINEAR))
        return depth

    return estimate


def write_gray_video(frames, path, width, height, fps, ffmpeg="ffmpeg"):
    """グレースケールのフレーム列を、劣化の少ない H.264 (yuv444p, crf 10) の動画に書く。"""
    cmd = [ffmpeg, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{width}x{height}", "-r", str(fps),
           "-i", "-", "-c:v", "libx264", "-crf", "10", "-pix_fmt", "yuv444p", str(path)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    count = 0
    try:
        for frame in frames:
            proc.stdin.write(np.ascontiguousarray(frame, dtype=np.uint8).tobytes())
            count += 1
    finally:
        proc.stdin.close()
        if proc.wait() != 0:
            raise RuntimeError("ffmpeg could not write the depth video")
    return count


def make_depth_video(source, output, width, height, fps, estimate, duration=None, normalizer=None, ffmpeg="ffmpeg",
                     progress=None):
    from convert import ffmpeg_frame_generator

    normalizer = normalizer or DepthNormalizer()

    def frames():
        for i, frame in enumerate(ffmpeg_frame_generator(source, width, height, fps, duration, ffmpeg)):
            yield normalizer(estimate(frame))
            if progress:
                progress(i + 1)

    return write_gray_video(frames(), output, width, height, fps, ffmpeg)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("input", help="元の動画")
    parser.add_argument("output", help="書き出す深度動画 (.mp4)")
    parser.add_argument("--width", type=int, default=128)
    parser.add_argument("--height", type=int, default=72)
    parser.add_argument("--fps", type=float, default=10.0)
    parser.add_argument("--duration", type=float, default=None)
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Hugging Face の深度推定モデル")
    parser.add_argument("--device", default="cpu", help="cpu / cuda など (transformers の pipeline に渡す)")
    parser.add_argument("--range-smoothing", type=float, default=1.0,
                        help="明るさの範囲を追いかける速さ (0〜1、1 でフレームごとに伸ばす)")
    parser.add_argument("--temporal-smoothing", type=float, default=0.3,
                        help="前のフレームの値を混ぜる割合 (0〜1、0 で無効)")
    parser.add_argument("--invert", action="store_true", help="モデルの出力が「大きいほど遠い」とき")
    parser.add_argument("--ffmpeg", default="ffmpeg")
    args = parser.parse_args(argv)
    if not 0 < args.range_smoothing <= 1 or not 0 <= args.temporal_smoothing < 1:
        parser.error("--range-smoothing は 0 より大きく 1 以下、--temporal-smoothing は 0 以上 1 未満にしてください")
    estimate = load_estimator(args.model, args.device)
    normalizer = DepthNormalizer(args.range_smoothing, args.temporal_smoothing, args.invert)
    count = make_depth_video(args.input, args.output, args.width, args.height, args.fps, estimate, args.duration,
                             normalizer, args.ffmpeg, progress=lambda n: print(f"\r{n} frames", end="", flush=True))
    print(f"\n{count} frames -> {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
