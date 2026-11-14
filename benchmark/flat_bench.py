"""色が一定の面の塗り方 (--flat-areas off / smooth / balance) の比較: 色ムラ・遠目の色差・データ・ちらつき・変換の速さ。

合成映像 (benchmark/codec_bench.py と同じもの) と、--videos で渡した動画を変換し、ゲーム内と同じ規則で復元した画面を元の映像と比べる:
  - ムラ: 元の映像で色が一定の面の、ブロックの画面のまだらさ (benchmark/metrics/mura.py。OkLab × 100、小さいほど良い)
  - 遠目の色差: 4 × 4 ブロックでぼかして比べた色差 (OkLab × 100)。全体と、色が一定の面だけ
  - ちらつき: 1 フレームで変わる画素の割合
  - データ: 出力 .js の deflate 後のバイト数
  - 変換の時間: convert.py 全体の秒数と、減色 1 フレームの時間 (process_single_frame_img だけ、1 スレッド)

使い方:
    python benchmark/flat_bench.py --out bench_out
    python benchmark/flat_bench.py --out bench_out --clips anime --videos path/to/video.mp4 --dithers blue_noise
結果は <out>/results_flat.json と <out>/results_flat.md に書く。
"""
import argparse
import json
import re
import subprocess
import sys
import time
import zlib
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from codec_bench import FPS, convert, make_clip, save_frames  # noqa: E402
from metrics.mura import blurred_color_error, flat_region, mura  # noqa: E402
from mvcodec.color import (BLOCK_TEX, FLAT_AREA_CHOICES, PALETTES, create_palette_image, palette_tex,  # noqa: E402
                           process_single_frame_img)
from mvcodec.decode import iter_decoded_frames, load_frame_data, parse_index  # noqa: E402


def video_frames(video, folder, w, h, n):
    """動画の先頭 n フレームを、変換ツールと同じ縮小 (build_video_filter) で w × h の PNG にする。"""
    from convert import build_video_filter

    if folder.exists():
        return
    folder.mkdir(parents=True)
    subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-vf", build_video_filter(w, h, FPS), "-frames:v", str(n),
                    str(folder / "frame_%04d.png")], check=True)


def decoded_boards(js_path):
    """復元した画面を (ブロックの色 (H, W, 3), ブロックのテクスチャのばらつき (H, W)) の列で返す。
    ばらつきの盤面は、デコーダと同じく「番号が変わった画素 (キーフレームは全部)」だけ、そのフレームの GOP のパレットで塗り直す。"""
    data = load_frame_data(js_path)
    blocks = data["level_blocks"] if data.get("adaptive_palette") else [data["level_blocks"]] * len(data["chunks"])
    tex_tables = [np.array([BLOCK_TEX.get(spec["block"], 0.0) for spec in table], np.float32) for table in blocks]
    entries = parse_index(data)
    tex_board, previous = None, None
    for (gop_id, _offset, length, keyframe), (index, rgb) in zip(entries, iter_decoded_frames(data)):
        if tex_board is None:
            tex_board = np.zeros(index.shape, np.float32)
        if length > 0:
            changed = np.ones(index.shape, bool) if keyframe or previous is None else index != previous
            changed &= index >= 0
            tex_board[changed] = tex_tables[gop_id][index[changed]]
        previous = index
        yield rgb, tex_board.copy()


def evaluate(frames, js_path):
    mura_values, grain, texture, far, far_flat, changes = [], [], [], [], [], []
    shown_prev = None
    for source, (shown, tex) in zip(frames, decoded_boards(js_path)):
        flat = flat_region(source)
        m = mura(source, shown, tex, flat)
        if flat.any():
            mura_values.append(m["mura"])
            grain.append(m["mura_grain"])
            texture.append(m["mura_texture"])
            far_flat.append(blurred_color_error(source, shown, mask=flat))
        far.append(blurred_color_error(source, shown))
        if shown_prev is not None:
            changes.append(float(np.any(shown != shown_prev, axis=-1).mean()))
        shown_prev = shown

    def mean(values):
        return round(float(np.mean(values)), 2) if values else None

    return {"mura": mean(mura_values), "mura_grain": mean(grain), "mura_texture": mean(texture),
            "far_error": mean(far), "far_error_flat": mean(far_flat),
            "changed_per_frame": round(float(np.mean(changes)), 4) if changes else 0.0,
            "zip_bytes": len(zlib.compress(Path(js_path).read_bytes(), 6))}


def quantize_ms(frames, mode, dither, palette_name="all", repeat=3):
    """減色 1 フレームの時間 (ms、1 スレッド)。固定のパレットで process_single_frame_img だけを測る (一番速い回)。"""
    palette = PALETTES[palette_name]
    pal_img, pal_rgb = create_palette_image(palette), np.array([p["rgb"] for p in palette], np.float64)
    tex = palette_tex(palette)
    h, w = frames[0].shape[:2]
    images = [Image.fromarray(f) for f in frames]
    best = None
    for _ in range(repeat):
        start = time.perf_counter()
        for img in images:
            process_single_frame_img(img, pal_img, w, h, len(palette), dither, True, pal_rgb, pal_tex=tex, flat_areas=mode)
        took = (time.perf_counter() - start) / len(images) * 1000
        best = took if best is None else min(best, took)
    return round(best, 2)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default="bench_out")
    parser.add_argument("--clips", nargs="*", default=["anime", "gradients", "testsrc2", "pan"])
    parser.add_argument("--videos", nargs="*", default=[], help="一緒に比べる動画 (先頭 --frames フレーム)")
    parser.add_argument("--size", default="128x72")
    parser.add_argument("--frames", type=int, default=60)
    parser.add_argument("--dithers", nargs="+", default=["blue_noise", "none"])
    parser.add_argument("--palettes", nargs="+", default=["auto"])
    parser.add_argument("--modes", nargs="+", default=list(FLAT_AREA_CHOICES), choices=FLAT_AREA_CHOICES)
    args = parser.parse_args(argv)
    w, h = map(int, args.size.split("x"))
    out_dir = Path(args.out).resolve()
    inputs = []
    for clip in args.clips:
        folder = out_dir / "clips" / f"{clip}_{w}x{h}"
        if not folder.exists():
            save_frames(make_clip(clip, w, h, args.frames), folder)
        inputs.append((clip, folder))
    for video in args.videos:
        name = re.sub(r"[^A-Za-z0-9]+", "_", Path(video).stem)
        folder = out_dir / "clips" / f"{name}_{w}x{h}_{args.frames}"
        video_frames(video, folder, w, h, args.frames)
        inputs.append((name, folder))
    rows = []
    for name, folder in inputs:
        # 比べる元の映像は、変換に使ったファイルから読む (ffmpeg の gradients は実行ごとに模様が変わるため)
        frames = [np.asarray(Image.open(p).convert("RGB")) for p in sorted(folder.glob("*.png"))]
        for dither in args.dithers:
            for palette in args.palettes:
                for mode in args.modes:
                    js = out_dir / "flat" / f"{name}_{dither}_{palette}_{mode}.js"
                    js.parent.mkdir(parents=True, exist_ok=True)
                    seconds = convert(ROOT, folder, js, w, h, ["--palette", palette, "--dither-method", dither,
                                                               "--flat-areas", mode])
                    row = {"clip": name, "dither": dither, "palette": palette, "flat_areas": mode,
                           **evaluate(frames, js), "convert_s": round(seconds, 2),
                           "quantize_ms": quantize_ms(frames, mode, dither)}
                    rows.append(row)
                    print(json.dumps(row), flush=True)
    (out_dir / "results_flat.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    keys = list(rows[0])
    lines = ["| " + " | ".join(keys) + " |", "|" + "---|" * len(keys)]
    lines += ["| " + " | ".join(str(r[k]) for k in keys) + " |" for r in rows]
    (out_dir / "results_flat.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
