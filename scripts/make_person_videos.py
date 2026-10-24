"""動画から人物を切り抜き、人物の範囲の動画と、人物を消した背景の動画を作る (Phase 12.3)。

初回に AI の切り抜きモデル (torchvision の DeepLabV3 MobileNetV3-Large、BSD-3、約 42 MB) をダウンロードする。
作った動画で、人物をふくらませた立体を、背景の映像の前に立つだまし絵にできる:

    python scripts/make_person_videos.py in.mp4 mask.mp4 background.mp4 --width 160 --height 90 --fps 10
    python convert.py --input-video background.mp4 --output bg.js --mode screen --fit cover \
        --width <後ろの壁の幅> --height <後ろの壁の高さ> --fps 10
    python convert.py --input-video in.mp4 --mask-video mask.mp4 --mode inflate --depth 16 --view anamorphic \
        --stage-background bg --output person.js --width 160 --height 90 --fps 10

Windows 版の GUI の表示モード「人物を切り抜いて立体に (AI)」が同じことをする。
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mvcodec.person import make_person_videos  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("input")
    parser.add_argument("mask_output", help="人物の範囲の動画 (白 = 人物)")
    parser.add_argument("background_output", help="人物を消した背景の動画 (元の音声付き)")
    parser.add_argument("--width", type=int, required=True)
    parser.add_argument("--height", type=int, required=True)
    parser.add_argument("--fps", type=float, default=10)
    parser.add_argument("--duration", type=float, default=None)
    parser.add_argument("--ffmpeg", default="ffmpeg")
    args = parser.parse_args(argv)
    count = make_person_videos(args.input, args.mask_output, args.background_output, args.width, args.height,
                               args.fps, args.duration, args.ffmpeg,
                               progress=lambda i: print(f"\r{i} frames", end="", flush=True) if i % 10 == 0 else None)
    print(f"\n{count} frames -> {args.mask_output}, {args.background_output}")


if __name__ == "__main__":
    main()
