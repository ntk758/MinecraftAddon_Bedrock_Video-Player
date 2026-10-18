"""変換した動画データ (frames_*.js) を、ゲーム内と同じ規則で復元してアニメーション GIF のプレビューにする。

立体 (relief / extrude / inflate / vox) は斜め上から見た立体の絵、平面スクリーンはそのまま拡大した絵になる。
Minecraft を起動しなくても、立体の形・奥行き・色を確かめられる。

使い方:
    python scripts/preview_volume.py frames_demo.js --output preview.gif [--unit 4] [--max-frames 120]
    python scripts/preview_volume.py frames_demo.js --output preview.gif --source-frames frames_dir  # 元の映像を左に並べる
"""
import argparse
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mvcodec.decode import iter_decoded_frames, load_frame_data  # noqa: E402
from mvcodec.volume import iso_view  # noqa: E402


def render_frames(data, unit=4, max_frames=None, step=1):
    """復元した各フレームの絵 (numpy の RGB 画像) を順に返す。"""
    volume = data.get("layout") in ("volume", "heightfield")
    # 立体の正面・側面はブロックの側面の色で塗る (原木などは上面と違う)
    sides = iter_decoded_frames(data, face="side") if volume else None
    for i, (_index, rgb) in enumerate(iter_decoded_frames(data)):
        side_rgb = next(sides)[1] if volume else None
        if max_frames is not None and i >= max_frames:
            break
        if i % step:
            continue
        if volume:
            yield iso_view(_index, rgb=rgb, unit=unit, side_rgb=side_rgb)
        else:
            yield np.kron(rgb, np.ones((unit, unit, 1), np.uint8))


def with_source(frame, source_path, height):
    """元の映像のフレームを、プレビューと同じ高さに拡大して左に並べる。"""
    source = Image.open(source_path).convert("RGB")
    scale = max(1, height // (2 * source.height))
    source = source.resize((source.width * scale, source.height * scale), Image.Resampling.NEAREST)
    canvas = Image.new("RGB", (source.width + 8 + frame.shape[1], frame.shape[0]), tuple(int(v) for v in frame[0, 0]))
    canvas.paste(source, (4, (frame.shape[0] - source.height) // 2))
    canvas.paste(Image.fromarray(frame), (source.width + 8, 0))
    return canvas


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("frames_js", help="convert.py の出力 (export const FRAME_DATA = {...})")
    parser.add_argument("--output", default="preview.gif")
    parser.add_argument("--unit", type=int, default=4, help="1 ブロックの大きさ (画素)")
    parser.add_argument("--max-frames", type=int, default=None)
    parser.add_argument("--step", type=int, default=1, help="何フレームごとに描くか")
    parser.add_argument("--source-frames", default=None, help="元の映像の画像連番フォルダ (並べて表示する)")
    args = parser.parse_args(argv)

    data = load_frame_data(args.frames_js)
    sources = sorted(Path(args.source_frames).iterdir()) if args.source_frames else None
    images = []
    for n, frame in enumerate(render_frames(data, args.unit, args.max_frames, args.step)):
        if sources:
            images.append(with_source(frame, sources[min(n * args.step, len(sources) - 1)], frame.shape[0]))
        else:
            images.append(Image.fromarray(frame))
    if not images:
        print("フレームがありません", file=sys.stderr)
        return 1
    duration = int(1000 * args.step / data.get("fps", 10))
    images[0].save(args.output, save_all=True, append_images=images[1:], duration=duration, loop=0, optimize=True)
    print(f"{args.output}: {len(images)} フレーム")
    return 0


if __name__ == "__main__":
    sys.exit(main())
