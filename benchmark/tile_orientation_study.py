"""タイル辞書に回転・反転 (8 通り) の参照を足すと、どれだけ得になるかの見積もり (Phase 8.5 の調査)。

convert.py の出力を復元した盤面の列で、エンコーダと同じ規則の辞書 (mvcodec/tiles.py) を作り直し、
前の盤面から MIN_MISMATCH 以上変わったタイルについて、
  - 今の辞書でそのまま見つかるか (exact)
  - 回転・反転した形でだけ見つかるか (oriented)
を数える。oriented のタイルを参照にできたときに減るデータを「そのタイルの差分ラン数 × RUN_BYTES − 参照 2 バイト」で見積もり、
v5 のフレームデータ全体 (エントロピー符号化の前) に対する割合を出す。動き補償の予測は使わない (前の盤面と比べる) ので、
動き補償と併用したときの効果はこれより小さい。

使い方: python benchmark/tile_orientation_study.py --out bench_out
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
from mvcodec.decode import gop_bytes_of, iter_decoded_frames, load_frame_data, parse_index  # noqa: E402
from mvcodec.tiles import MIN_MISMATCH, TileDictionary  # noqa: E402

RUN_BYTES = 1.5
REF_BYTES = 2


def orientations(tile):
    """回転・反転の 7 通り (そのままを除く)。"""
    out = []
    for k in range(4):
        rotated = np.rot90(tile, k)
        if k:
            out.append(rotated)
        out.append(np.fliplr(rotated))
    return out


def run_count(tile_new, tile_old):
    changed = tile_new != tile_old
    starts = changed.copy()
    starts[:, 1:] &= ~(changed[:, :-1] & (tile_new[:, 1:] == tile_new[:, :-1]))
    return int(starts.sum())


def study(js_path, tile):
    data = load_frame_data(js_path)
    w = data["width"]
    boards = [board.reshape(-1).astype(np.int32) for board, _ in iter_decoded_frames(data)]
    h = len(boards[0]) // w
    keyframes = {i for i, entry in enumerate(parse_index(data)) if entry[3]}
    total_bytes = sum(len(gop_bytes_of(data, g)) for g in range(len(data["chunks"])))
    dictionary = TileDictionary(w, h, tile)
    exact = oriented = changed_tiles = 0
    saved = 0.0
    previous = None
    for i, board in enumerate(boards):
        if i in keyframes or previous is None:
            dictionary.update(previous, board, True)
            previous = board
            continue
        b2, p2 = board.reshape(h, w), previous.reshape(h, w)
        new_tiles, old_tiles = dictionary.tiles_of(b2), dictionary.tiles_of(p2)
        mismatch = (new_tiles != old_tiles).sum(1)
        for t in np.flatnonzero(mismatch >= MIN_MISMATCH):
            changed_tiles += 1
            content = new_tiles[t].reshape(tile, tile)
            if dictionary.recency_of(content) is not None:
                exact += 1
                continue
            if any(dictionary.recency_of(np.ascontiguousarray(o)) is not None for o in orientations(content)):
                oriented += 1
                saved += max(0.0, run_count(content, old_tiles[t].reshape(tile, tile)) * RUN_BYTES - REF_BYTES)
        dictionary.update(previous, board, False)
        previous = board
    return {"changed_tiles": changed_tiles, "exact_hits": exact, "oriented_only_hits": oriented,
            "v5_bytes": total_bytes, "est_saving": f"{100 * saved / max(1, total_bytes):.1f}%"}


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
            js = out_dir / "orientation" / f"{clip}_{dither}.js"
            js.parent.mkdir(parents=True, exist_ok=True)
            convert(ROOT, frames_dir, js, w, h, ["--palette", "auto", "--dither-method", dither, "--no-entropy"])
            for tile in (4, 8):
                row = {"clip": clip, "dither": dither, "tile": tile, **study(js, tile)}
                rows.append(row)
                print(json.dumps(row), flush=True)
    (out_dir / "results_orientation.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
