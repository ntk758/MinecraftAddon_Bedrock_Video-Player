"""色の境界の崩れ方を 3 種類に分けて測る: にじみ・境界の粒 (場違いな色)・8×8 の継ぎ目。

元のフレームの色の境界 (隣のマスとの色差が EDGE_DELTA 以上) のまわりの帯と、境界から離れた内側を比べる。
見え方は benchmark/metrics/dither_view.py と同じ S-CIELAB のぼかし (距離 k = 2、スクリーンの高さの 2 倍)。

  - にじみ: ぼかした絵の色差 (元と表示)。帯 / 内側。境界のコントラスト (ぼかした絵の 3×3 の明るさの幅、表示 / 元)。
  - 粒: 足された粒 (grain_visible) の帯 / 内側。場違いな色 = 3×3 のまわりのどの元の色とも ΔE76 が STRAY_DELTA を超えるマスの割合。
  - 継ぎ目: 8×8 の区切りのうち、片方が 1〜2 色だけ (8×8 の最適化で平らにされた) でもう片方が 3 色以上、かつ元の映像では
    そこに境界が無い線。線の両側 2 マスの平均の色の差が、元とどれだけ違うか (ΔE76)。

使い方:
    python benchmark/edge_bench.py --videos a.mp4 b.y4m --out D:/out/edge --variants blue_noise blue_noise_nordo floyd none
結果は <out>/results.json と <out>/results.md。
"""
import argparse
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from codec_bench import convert, save_frames  # noqa: E402
from metrics.dither_view import _grain_visible_map, cells_per_degree, view_lab  # noqa: E402
from mvcodec.decode import iter_decoded_frames, load_frame_data  # noqa: E402
from skimage.color import rgb2lab  # noqa: E402

EDGE_DELTA = 12.0  # 隣のマスとの ΔE76 がこれ以上なら境界
STRAY_DELTA = 15.0
TILE = 8
DISTANCE = 2
VARIANTS = {
    "blue_noise": ["--dither-method", "blue_noise"],
    "blue_noise_nordo": ["--dither-method", "blue_noise", "--block-rdo", "0"],
    "floyd": ["--dither-method", "floyd"],
    "floyd_nordo": ["--dither-method", "floyd", "--block-rdo", "0"],
    "none": ["--dither-method", "none"],
}


def extract(video, folder, w, h, frames, start):
    folder.mkdir(parents=True, exist_ok=True)
    if any(folder.glob("*.png")):
        return
    vf = f"scale={w}:{h}:force_original_aspect_ratio=increase:flags=area,crop={w}:{h},fps=10"
    subprocess.run(["ffmpeg", "-v", "error", "-ss", str(start), "-i", str(video), "-vf", vf, "-frames:v", str(frames),
                    str(folder / "frame_%04d.png")], check=True)


def neighbours(img):
    """3×3 のまわり (端は繰り返し) を (9, H, W, C) で返す。"""
    pad = np.pad(img, ((1, 1), (1, 1), (0, 0)), mode="edge")
    h, w = img.shape[:2]
    return np.stack([pad[dy:dy + h, dx:dx + w] for dy in range(3) for dx in range(3)])


def dilate(mask, r):
    out = mask.copy()
    for _ in range(r):
        pad = np.pad(out, 1)
        out = pad[1:-1, 1:-1] | pad[:-2, 1:-1] | pad[2:, 1:-1] | pad[1:-1, :-2] | pad[1:-1, 2:]
    return out


def edge_mask(lab):
    dx = np.zeros(lab.shape[:2], bool)
    dy = np.zeros(lab.shape[:2], bool)
    gx = np.linalg.norm(lab[:, 1:] - lab[:, :-1], axis=-1) >= EDGE_DELTA
    gy = np.linalg.norm(lab[1:] - lab[:-1], axis=-1) >= EDGE_DELTA
    dx[:, 1:] |= gx
    dx[:, :-1] |= gx
    dy[1:] |= gy
    dy[:-1] |= gy
    return dx | dy


def seams(source_lab, shown_rgb, shown_lab, edges):
    """8×8 の区切りで、平らな区画とディザのある区画の継ぎ目の誤差 (元に境界の無い線だけ)。対照としてディザ同士の線も返す。"""
    h, w = shown_rgb.shape[:2]
    codes = shown_rgb.astype(np.int32) @ np.array([65536, 256, 1])
    ty, tx = h // TILE, w // TILE
    flat = np.zeros((ty, tx), bool)
    for j in range(ty):
        for i in range(tx):
            flat[j, i] = len(np.unique(codes[j * TILE:(j + 1) * TILE, i * TILE:(i + 1) * TILE])) <= 2
    mixed, control = [], []

    def step(a_src, b_src, a_show, b_show):
        return float(np.linalg.norm((a_show.mean((0, 1)) - b_show.mean((0, 1))) - (a_src.mean((0, 1)) - b_src.mean((0, 1)))))

    for j in range(ty):
        for i in range(tx):
            y0, x0 = j * TILE, i * TILE
            if i + 1 < tx:  # 縦の線 (x = x0 + 8)
                x = x0 + TILE
                if not edges[y0:y0 + TILE, x - 2:x + 2].any():
                    e = step(source_lab[y0:y0 + TILE, x - 2:x], source_lab[y0:y0 + TILE, x:x + 2],
                             shown_lab[y0:y0 + TILE, x - 2:x], shown_lab[y0:y0 + TILE, x:x + 2])
                    if flat[j, i] != flat[j, i + 1]:
                        mixed.append(e)
                    elif not flat[j, i] and not flat[j, i + 1]:
                        control.append(e)
            if j + 1 < ty:
                y = y0 + TILE
                if not edges[y - 2:y + 2, x0:x0 + TILE].any():
                    e = step(source_lab[y - 2:y, x0:x0 + TILE], source_lab[y:y + 2, x0:x0 + TILE],
                             shown_lab[y - 2:y, x0:x0 + TILE], shown_lab[y:y + 2, x0:x0 + TILE])
                    if flat[j, i] != flat[j + 1, i]:
                        mixed.append(e)
                    elif not flat[j, i] and not flat[j + 1, i]:
                        control.append(e)
    return mixed, control, float(flat.mean())


def frame_stats(source, shown):
    h = source.shape[0]
    cpd = cells_per_degree(h, DISTANCE)
    lab_src, lab_show = rgb2lab(source), rgb2lab(shown)
    v_src, v_show = view_lab(source, cpd), view_lab(shown, cpd)
    edges = edge_mask(lab_src)
    band = dilate(edges, 1)
    inside = ~dilate(edges, 3)
    view_err = np.linalg.norm(v_src - v_show, axis=-1)
    grain = _grain_visible_map(v_src, v_show)
    near = neighbours(lab_src)  # (9, H, W, 3)
    stray = np.min(np.linalg.norm(near - lab_show[None], axis=-1), axis=0) > STRAY_DELTA

    def contrast(v):
        n = neighbours(v[..., :1])[..., 0]
        return n.max(0) - n.min(0)

    c_src, c_show = contrast(v_src), contrast(v_show)
    strong = edges & (c_src > 5)
    mixed, control, flat_share = seams(lab_src, shown, lab_show, edges)
    return {
        "band_share": float(band.mean()), "inside_share": float(inside.mean()),
        "view_band": float(view_err[band].mean()) if band.any() else None,
        "view_inside": float(view_err[inside].mean()) if inside.any() else None,
        "grain_band": float(grain[band].mean()) if band.any() else None,
        "grain_inside": float(grain[inside].mean()) if inside.any() else None,
        "stray_band": float(stray[band].mean()) if band.any() else None,
        "stray_inside": float(stray[inside].mean()) if inside.any() else None,
        "contrast_ratio": float(np.median(c_show[strong] / c_src[strong])) if strong.any() else None,
        "seam_mixed": mixed, "seam_control": control, "flat_tiles": flat_share,
    }


def mean(values):
    values = [v for v in values if v is not None]
    return float(np.mean(values)) if values else None


def evaluate(frames_dir, js):
    source = [np.asarray(Image.open(p).convert("RGB")) for p in sorted(frames_dir.glob("*.png"))]
    shown = [rgb for _i, rgb in iter_decoded_frames(load_frame_data(js))]
    n = min(len(source), len(shown))
    stats = [frame_stats(source[t], shown[t]) for t in range(0, n, 2)]
    row = {k: mean([s[k] for s in stats]) for k in stats[0] if not k.startswith("seam_")}
    mixed = [e for s in stats for e in s["seam_mixed"]]
    control = [e for s in stats for e in s["seam_control"]]
    row["seam_mixed_n"] = len(mixed)
    row["seam_mixed"] = float(np.mean(mixed)) if mixed else None
    row["seam_mixed_bad"] = float(np.mean(np.array(mixed) > 3.0)) if mixed else None
    row["seam_control"] = float(np.mean(control)) if control else None
    row["seam_control_bad"] = float(np.mean(np.array(control) > 3.0)) if control else None
    return row


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--videos", nargs="+", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--size", default="128x72")
    parser.add_argument("--frames", type=int, default=40)
    parser.add_argument("--start", type=float, default=2.0)
    parser.add_argument("--variants", nargs="+", default=["blue_noise", "blue_noise_nordo", "floyd", "none"])
    parser.add_argument("--jobs", type=int, default=3)
    args = parser.parse_args(argv)
    w, h = map(int, args.size.split("x"))
    out = Path(args.out)
    jobs = []
    for index, video in enumerate(args.videos, 1):
        video = Path(video)
        label = f"clip-{index:02d}"
        frames_dir = out / "frames" / label
        if video.is_dir():
            frames = sorted(video.glob("*.png"))[: args.frames]
            if not any(frames_dir.glob("*.png")):
                save_frames([np.asarray(Image.open(p).convert("RGB").resize((w, h), Image.BOX)) for p in frames], frames_dir)
        else:
            extract(video, frames_dir, w, h, args.frames, args.start)
        for variant in args.variants:
            jobs.append((label, video.name, frames_dir, variant))

    def run(job):
        label, name, frames_dir, variant = job
        js = out / "js" / f"{label}_{variant}.js"
        js.parent.mkdir(parents=True, exist_ok=True)
        if not js.exists():
            convert(ROOT, frames_dir, js, w, h, ["--palette", "auto", *VARIANTS[variant]])
        row = {"clip": label, "source": name, "variant": variant, **evaluate(frames_dir, js)}
        print(json.dumps({k: (round(v, 3) if isinstance(v, float) else v) for k, v in row.items()}, ensure_ascii=False),
              flush=True)
        return row

    with ThreadPoolExecutor(args.jobs) as pool:
        rows = list(pool.map(run, jobs))
    (out / "results.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    keys = ["view_band", "view_inside", "grain_band", "grain_inside", "stray_band", "stray_inside", "contrast_ratio",
            "seam_mixed", "seam_mixed_bad", "seam_control", "seam_control_bad", "flat_tiles"]
    lines = ["| variant | " + " | ".join(keys) + " |", "|---" * (len(keys) + 1) + "|"]
    for variant in args.variants:
        sub = [r for r in rows if r["variant"] == variant]
        lines.append(f"| {variant} | " + " | ".join(
            "n/a" if (m := mean([r[k] for r in sub])) is None else f"{m:.3f}" for k in keys) + " |")
    (out / "results.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
