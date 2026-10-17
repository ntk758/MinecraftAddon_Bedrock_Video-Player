"""ブロックモードのレート歪み最適化 (--block-rdo、Phase 8.5) の効果: 強さごとに、画質・データ・ゲーム内の費用を比べる。

合成映像 (benchmark/codec_bench.py と同じもの) を --block-rdo の強さを変えて変換し、次を出す:
  - 画質: ΔE2000 (画素ごと / 3×3 でぼかして)、MS-SSIM (benchmark/palette_bench.py と同じ)
  - データ: 出力 .js の deflate 後のバイト数
  - ゲーム内: モックで rect / list 方式で再生したときの 1 フレームあたりの呼び出し数・置いたブロック数と、
    アドオンの費用のモデル (Windows 版の実測) で見積もった描画時間 (auto はフレームごとに速い方を選ぶので、2 つの小さい方が上限)

使い方: python benchmark/rdo_bench.py --out bench_out
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from codec_bench import convert, make_clip, play, save_frames  # noqa: E402
from palette_bench import evaluate  # noqa: E402

# main.js の DEFAULT_COST_MODEL (µs)
COST = {"setBlock": 2.3, "fillCall": 15, "fillBlock": 0.55, "listCall": 15, "listBlock": 1.42}


def placement_us(rect, listed):
    """rect / list で再生した結果から、1 フレームあたりの描画時間 (µs) を見積もる。"""
    out = {}
    if rect and not rect.get("failed"):
        fill_blocks = rect["blocks_per_frame"] - rect["set_calls_per_frame"]
        out["rect_us"] = (COST["fillCall"] * rect["fill_calls_per_frame"] + COST["fillBlock"] * fill_blocks
                          + COST["setBlock"] * rect["set_calls_per_frame"])
        out["rect_calls"] = rect["calls_per_frame"]
    if listed and not listed.get("failed"):
        out["list_us"] = COST["listCall"] * listed["calls_per_frame"] + COST["listBlock"] * listed["blocks_per_frame"]
        out["list_calls"] = listed["calls_per_frame"]
        out["blocks"] = listed["blocks_per_frame"]
    if "rect_us" in out and "list_us" in out:
        out["best_us"] = min(out["rect_us"], out["list_us"])
    return {k: round(v, 1) for k, v in out.items()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default="bench_out")
    parser.add_argument("--clips", nargs="+", default=["anime", "pan", "testsrc2", "mandelbrot", "silhouette"])
    parser.add_argument("--size", default="128x72")
    parser.add_argument("--frames", type=int, default=60)
    parser.add_argument("--dithers", nargs="+", default=["none", "blue_noise"])
    parser.add_argument("--strengths", nargs="+", type=float, default=[0, 0.5, 1, 2, 4])
    parser.add_argument("--palette", nargs="+", default=["auto"], help="変換に渡す --palette (と続く引数)")
    parser.add_argument("--extra", nargs=argparse.REMAINDER, default=[], help="convert.py に追加で渡す引数")
    args = parser.parse_args(argv)
    w, h = map(int, args.size.split("x"))
    out_dir = Path(args.out).resolve()
    rows = []
    for clip in args.clips:
        frames_dir = out_dir / "clips" / f"{clip}_{w}x{h}"
        if not frames_dir.exists():
            save_frames(make_clip(clip, w, h, args.frames), frames_dir)
        frames = [np.asarray(Image.open(path).convert("RGB")) for path in sorted(frames_dir.glob("*.png"))]
        for dither in args.dithers:
            for strength in args.strengths:
                js = out_dir / "rdo" / f"{clip}_{dither}_{strength:g}.js"
                js.parent.mkdir(parents=True, exist_ok=True)
                convert(ROOT, frames_dir, js, w, h,
                        ["--palette", *args.palette, "--dither-method", dither, "--block-rdo", str(strength), *args.extra])
                row = {"clip": clip, "dither": dither, "strength": strength, **evaluate(frames, js)}
                work = out_dir / "rdo_play"
                rect = play(ROOT, js, work, "render=rect")
                listed = play(ROOT, js, work, "render=list")
                row.update(placement_us(rect, listed))
                rows.append(row)
                print(json.dumps(row), flush=True)
    (out_dir / "results_rdo.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    keys = list(dict.fromkeys(k for r in rows for k in r))
    lines = ["| " + " | ".join(keys) + " |", "|" + "---|" * len(keys)]
    lines += ["| " + " | ".join(str(r.get(k, "")) for k in keys) + " |" for r in rows]
    (out_dir / "results_rdo.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
