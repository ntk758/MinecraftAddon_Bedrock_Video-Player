"""自動パレット (--palette auto) の色空間の比較 (Issue #3): RGB と OkLab で、色差・構造・ちらつき・データ量を比べる。

合成映像 (benchmark/codec_bench.py と同じもの) を作り、--palette-space rgb / oklab で変換して、
ゲーム内と同じ規則で復元した画面を元の映像と比べる:
  - ΔE2000: 画素ごとの色差の平均 (小さいほど良い)。ディザの粒の影響を除くため、3×3 でぼかした色差も出す
  - MS-SSIM: 明るさの構造の近さ (大きいほど良い)
  - ちらつき: 1 フレームで変わる画素の割合 (小さいほど置くブロックが少ない)
  - データ: 出力 .js の deflate 後のバイト数

使い方: python benchmark/palette_bench.py --out bench_out
"""
import argparse
import json
import sys
import zlib
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from codec_bench import convert, make_clip, save_frames  # noqa: E402
from metrics.deltae import calculate_deltae  # noqa: E402
from metrics.ms_ssim import calculate_ms_ssim  # noqa: E402
from mvcodec.decode import iter_decoded_frames, load_frame_data  # noqa: E402


def box_blur(img):
    padded = np.pad(img.astype(np.float64), ((1, 1), (1, 1), (0, 0)), mode="edge")
    h, w = img.shape[:2]
    return sum(padded[dy:dy + h, dx:dx + w] for dy in range(3) for dx in range(3)) / 9.0


def evaluate(frames, js_path):
    data = load_frame_data(js_path)
    decoded = [rgb for _index, rgb in iter_decoded_frames(data)]
    de, de_blur, ms, changes = [], [], [], []
    previous = None
    for source, shown in zip(frames, decoded):
        de.append(calculate_deltae(source, shown))
        de_blur.append(calculate_deltae(box_blur(source).astype(np.uint8), box_blur(shown).astype(np.uint8)))
        ms.append(calculate_ms_ssim(source, shown))
        if previous is not None:
            changes.append(float(np.any(shown != previous, axis=-1).mean()))
        previous = shown
    raw = Path(js_path).read_bytes()
    return {
        "deltaE2000": round(float(np.mean(de)), 2),
        "deltaE2000_blur": round(float(np.mean(de_blur)), 2),
        "ms_ssim": round(float(np.nanmean(ms)), 4),
        "changed_per_frame": round(float(np.mean(changes)), 4) if changes else 0.0,
        "zip_bytes": len(zlib.compress(raw, 6)),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default="bench_out")
    parser.add_argument("--clips", nargs="+", default=["gradients", "mandelbrot", "testsrc2", "anime", "pan"])
    parser.add_argument("--size", default="128x72")
    parser.add_argument("--frames", type=int, default=60)
    parser.add_argument("--dithers", nargs="+", default=["none", "blue_noise"])
    parser.add_argument("--colors", nargs="+", type=int, default=[16, 24],
                        help="自動パレットの色数 (候補の 39 色より少ないときだけ色空間で選ぶブロックが変わる)")
    args = parser.parse_args(argv)
    w, h = map(int, args.size.split("x"))
    out_dir = Path(args.out).resolve()
    rows = []
    for clip in args.clips:
        frames_dir = out_dir / "clips" / f"{clip}_{w}x{h}"
        if not frames_dir.exists():
            save_frames(make_clip(clip, w, h, args.frames), frames_dir)
        # 比べる元の映像は、変換に使ったファイルから読む (ffmpeg の gradients は実行ごとに模様が変わるため)
        frames = [np.asarray(Image.open(path).convert("RGB")) for path in sorted(frames_dir.glob("*.png"))]
        for dither in args.dithers:
            variants = [("full (39)", ["--palette", "full"])]
            variants += [(f"auto {n} {space}", ["--palette", "auto", "--palette-colors", str(n), "--palette-space", space])
                         for n in args.colors for space in ("rgb", "oklab")]
            for name, extra in variants:
                js = out_dir / "palette" / f"{clip}_{dither}_{name.replace(' ', '_').replace('(', '').replace(')', '')}.js"
                js.parent.mkdir(parents=True, exist_ok=True)
                convert(ROOT, frames_dir, js, w, h, [*extra, "--dither-method", dither])
                row = {"clip": clip, "dither": dither, "palette": name, **evaluate(frames, js)}
                rows.append(row)
                print(json.dumps(row), flush=True)
    (out_dir / "results_palette.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    keys = list(rows[0])
    lines = ["| " + " | ".join(keys) + " |", "|" + "---|" * len(keys)]
    lines += ["| " + " | ".join(str(r[k]) for k in keys) + " |" for r in rows]
    (out_dir / "results_palette.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
