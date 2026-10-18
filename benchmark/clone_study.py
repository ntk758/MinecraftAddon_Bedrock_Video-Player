"""動きベクトルのブロックを cloneBlocks (Script API 2.10.0 以降) でコピーしたときの得の見積もり (Phase 8.6 の調査)。

平面スクリーンの convert.py の出力を復元し、動きベクトルを持つ 8×8 ごとに
  - コピーしない: 前の盤面から変わるマスを置く
  - コピーする: 前の盤面のずらした範囲を cloneBlocks で 1 回コピーし、残りの違いだけを置く
を比べる。置く費用は座標リスト (1 ブロック 1.42 µs、実機の値)、コピーは 1 回 15 µs + 1 ブロック c µs (c は未計測なので
いくつかの値で試す)。得になるブロックだけコピーしたときに減る描画時間を、全体の描画時間に対する割合で出す。
コピー元が画面の外に出るブロック (端の画素を伸ばす予測) は、ワールドからはコピーできないので数えない。

使い方: python benchmark/clone_study.py --out bench_out
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from codec_bench import convert, make_clip, save_frames  # noqa: E402
from mvcodec.decode import gop_bytes_of, iter_decoded_frames, load_frame_data, parse_index, read_varint  # noqa: E402
from mvcodec.motion import unpack_vector  # noqa: E402

LIST_BLOCK_US = 1.42
CLONE_CALL_US = 15.0
CLONE_BLOCK_US = (0.1, 0.2, 0.3, 0.5, 0.8, 1.2)


def frame_vectors(data):
    """フレームごとの動きベクトル {ブロック番号: (dx, dy)} (キーフレーム・省略フレームは空)。"""
    gops = {}
    for gop_id, offset, length, is_keyframe in parse_index(data):
        if length == 0 or is_keyframe:
            yield {}
            continue
        if gop_id not in gops:
            gops[gop_id] = gop_bytes_of(data, gop_id)
        buf = gops[gop_id]
        header, pos = read_varint(buf, offset)
        vectors = {}
        if (header >> 1) & 1:
            count, pos = read_varint(buf, pos)
            mb = -1
            for _ in range(count):
                gap, pos = read_varint(buf, pos)
                packed, pos = read_varint(buf, pos)
                mb += gap + 1
                vectors[mb] = unpack_vector(packed, data["mv_range"])
        yield vectors


def study(js_path):
    data = load_frame_data(js_path)
    w, h, block = data["width"], data["height"], data["mv_block"]
    cols = -(-w // block)
    blocks = []  # (コピーで減るマスの数, コピーするマスの数)
    place_us = 0.0
    previous = None
    for (board, _rgb), vectors in zip(iter_decoded_frames(data), frame_vectors(data)):
        board = board.astype(np.int64)
        if previous is not None:
            place_us += int((board != previous).sum()) * LIST_BLOCK_US
            for mb, (dx, dy) in vectors.items():
                by, bx = divmod(mb, cols)
                y0, x0 = by * block, bx * block
                y1, x1 = min(y0 + block, h), min(x0 + block, w)
                if not (0 <= y0 + dy and y1 + dy <= h and 0 <= x0 + dx and x1 + dx <= w):
                    continue
                target, old = board[y0:y1, x0:x1], previous[y0:y1, x0:x1]
                source = previous[y0 + dy:y1 + dy, x0 + dx:x1 + dx]
                blocks.append((int((target != old).sum() - (target != source).sum()), (y1 - y0) * (x1 - x0)))
        previous = board
    blocks = np.array(blocks, dtype=np.float64).reshape(-1, 2)
    row = {"motion_blocks": len(blocks)}
    for cost in CLONE_BLOCK_US:
        gain = blocks[:, 0] * LIST_BLOCK_US - (CLONE_CALL_US + blocks[:, 1] * cost)
        row[f"saving_at_{cost}us"] = f"{100 * gain[gain > 0].sum() / max(place_us, 1e-9):.1f}%"
    return row


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default="bench_out")
    parser.add_argument("--clips", nargs="+", default=["anime", "pan", "testsrc2", "mandelbrot", "silhouette"])
    parser.add_argument("--size", default="128x72")
    parser.add_argument("--frames", type=int, default=60)
    parser.add_argument("--dithers", nargs="+", default=["none", "blue_noise"])
    args = parser.parse_args(argv)
    w, h = map(int, args.size.split("x"))
    out_dir = Path(args.out).resolve()
    rows = []
    for clip in args.clips:
        frames_dir = out_dir / "clips" / f"{clip}_{w}x{h}"
        if not frames_dir.exists():
            save_frames(make_clip(clip, w, h, args.frames), frames_dir)
        for dither in args.dithers:
            js = out_dir / "clone" / f"{clip}_{dither}.js"
            js.parent.mkdir(parents=True, exist_ok=True)
            convert(ROOT, frames_dir, js, w, h, ["--palette", "auto", "--dither-method", dither])
            row = {"clip": clip, "dither": dither, **study(js)}
            rows.append(row)
            print(json.dumps(row), flush=True)
    (out_dir / "results_clone.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
