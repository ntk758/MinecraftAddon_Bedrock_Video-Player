"""動画を変換し、ゲーム内と同じ規則で復元した盤面からデモ GIF を作る。

使い方:
    python scripts/generate_demo_gif.py <入力動画> [--output demo.gif] [--width 128] [--height 128] [--fps 10] [--duration 5]
"""
import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mvcodec.decode import iter_decoded_frames, load_frame_data  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("video", help="入力動画ファイル")
    parser.add_argument("--output", default=str(ROOT / "demo.gif"))
    parser.add_argument("--width", type=int, default=128)
    parser.add_argument("--height", type=int, default=128)
    parser.add_argument("--fps", type=float, default=10.0)
    parser.add_argument("--duration", type=float, default=5.0, help="変換する秒数")
    parser.add_argument("--palette", default="auto")
    parser.add_argument("--dither-method", default="floyd", help="既定は CPU でも動く floyd")
    parser.add_argument("--scale", type=int, default=4, help="GIF の拡大倍率")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as temp_dir:
        output_js = Path(temp_dir) / "demo_output.js"
        cmd = [
            sys.executable, str(ROOT / "convert.py"),
            "--input-video", args.video,
            "--output", str(output_js),
            "--width", str(args.width),
            "--height", str(args.height),
            "--palette", args.palette,
            "--dither-method", args.dither_method,
            "--duration", str(args.duration),
            "--fps", str(args.fps),
        ]
        print("Running convert.py...")
        subprocess.run(cmd, check=True)
        data = load_frame_data(output_js)

    size = (data["width"] * args.scale, data["height"] * args.scale)
    frames = [Image.fromarray(rgb).resize(size, Image.Resampling.NEAREST) for _idx, rgb in iter_decoded_frames(data)]
    if not frames:
        print("Error: フレームがありません。", file=sys.stderr)
        return 1
    frames[0].save(args.output, save_all=True, append_images=frames[1:], optimize=True,
                   duration=int(1000 / args.fps), loop=0)
    print(f"Demo GIF saved to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
