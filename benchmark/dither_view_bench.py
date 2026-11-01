"""ディザの見え方の物差し (Phase 13.1): 変換した .js を元のフレームと比べて、見る距離ごとの色差・粒・ちらつきを出す。

定義と仮定は benchmark/metrics/dither_view.py の先頭と benchmark/README.md にある。比べる対象は 3 通り:
  - 自作の合成映像 (--clips): 変換してから測る
  - フレームのフォルダの集まり (--frames-root): 変換してから測る。結果には「ジャンル-01」のような連番だけを書き、元の名前は書かない
  - 変換済みの .js (--from-js): --frames-dir の元のフレームと比べる。変換はしない

使い方:
    python benchmark/dither_view_bench.py --out bench_out/dither_view --clips anime gradients --size 128x72 --frames 60
    python benchmark/dither_view_bench.py --frames-root D:/frames --set-label ジャンル --out D:/out/base --device auto
    python benchmark/dither_view_bench.py --out D:/out/one --from-js out.js --frames-dir D:/frames/one
    python benchmark/dither_view_bench.py --compare D:/out/before/results.json D:/out/after/results.json
結果は <out>/results.json と <out>/results.md に書く。描画時間の見積り (best_us) は node が無ければ省く。
"""
import argparse
import json
import re
import shlex
import sys
import time
import zlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from codec_bench import convert, export_tree, make_clip, play, save_frames  # noqa: E402
from metrics.deltae import calculate_deltae  # noqa: E402
from metrics.dither_view import (DISTANCES, L_BANDS, MIN_STATIC_CLIP, SCREEN_ANGLE, TAU, flicker,  # noqa: E402
                                 frame_metrics)
from mvcodec.decode import iter_decoded_frames, load_frame_data  # noqa: E402
from palette_bench import box_blur  # noqa: E402
from rdo_bench import placement_us  # noqa: E402


def read_frames(folder):
    return [np.asarray(Image.open(path).convert("RGB")) for path in sorted(Path(folder).glob("*.png"))]


def mean_or_none(values):
    values = [v for v in values if v is not None]
    return float(np.mean(values)) if values else None


def rounded(value, digits=3):
    return None if value is None else round(float(value), digits)


def evaluate_view(frames, js_path, distances=DISTANCES, screen_angle=SCREEN_ANGLE, tau=TAU, min_static=MIN_STATIC_CLIP, stride=4):
    """元のフレームと .js を比べた 1 行を作る。色差と粒は stride ごと、ちらつきは全フレーム。"""
    shown = [rgb for _index, rgb in iter_decoded_frames(load_frame_data(js_path))]
    count = min(len(frames), len(shown))
    if count == 0:
        raise ValueError("比べるフレームがない")
    if frames[0].shape != shown[0].shape:
        raise ValueError(f"元と表示の大きさが違う: {frames[0].shape} と {shown[0].shape}")
    frames, shown = frames[:count], shown[:count]
    height = frames[0].shape[0]
    picked = range(0, count, stride)
    per_frame = [frame_metrics(frames[t], shown[t], height, distances, screen_angle) for t in picked]
    row = {"frames": count}
    for k in distances:
        items = [m[k] for m in per_frame]
        row[f"view_error_k{k}"] = rounded(np.mean([m["view_error"] for m in items]))
        row[f"grain_visible_k{k}"] = rounded(np.mean([m["grain_visible"]["all"] for m in items]))
        row[f"grain_visible_L_k{k}"] = rounded(np.mean([m["grain_visible"]["L"] for m in items]))
        row[f"grain_visible_ab_k{k}"] = rounded(np.mean([m["grain_visible"]["ab"] for m in items]))
        row[f"grain_removed_k{k}"] = rounded(np.mean([m["grain_removed"] for m in items]))
        row[f"grain_band_k{k}"] = [rounded(mean_or_none([m["grain_band"]["bands"][b] for m in items]))
                                   for b in range(len(L_BANDS))]
    row["grain_band_fraction"] = [rounded(np.mean([per_frame[i][distances[0]]["grain_band"]["fractions"][b]
                                                   for i in range(len(per_frame))]))
                                  for b in range(len(L_BANDS))]
    moved = flicker(frames, shown, height, distances, screen_angle, tau, min_static)
    row["flicker_rate"] = rounded(moved["flicker_rate"], 4)
    for k in distances:
        row[f"flicker_mag_k{k}"] = rounded(moved["flicker_mag"][k])
    row["static_coverage"] = rounded(moved["static_coverage"], 4)
    row["deltaE2000"] = rounded(np.mean([calculate_deltae(frames[t], shown[t]) for t in picked]))
    # palette_bench.evaluate と同じ式 (sRGB の 3×3 を uint8 へ切り捨てる)。過去の値と並べるためだけに残す
    row["deltaE2000_blur"] = rounded(np.mean([calculate_deltae(box_blur(frames[t]).astype(np.uint8),
                                                               box_blur(shown[t]).astype(np.uint8)) for t in picked]))
    row["zip_bytes"] = len(zlib.compress(Path(js_path).read_bytes(), 6))
    return row


def playback_cost(tree, js_path, work):
    return placement_us(play(tree, js_path, work, "render=rect"), play(tree, js_path, work, "render=list"))


def conversion_args(frames, device, extra):
    """変換の既定は実写の比較と同じ (auto パレット、blue_noise、128 幅は 10 fps、256 幅は 5 fps)。後の引数が勝つ。"""
    width = frames[0].shape[1]
    return ["--palette", "auto", "--dither-method", "blue_noise", "--device", device,
            "--fps", "10" if width <= 128 else "5", *extra]


def run_job(job, args, out_dir, distances):
    frames = read_frames(job["frames_dir"])
    if not frames:
        raise ValueError(f"PNG がない: {job['clip']}")
    h, w = frames[0].shape[:2]
    convert_s = None
    js = job.get("js")
    if js is None:
        js = out_dir / "js" / f"{job['clip']}__{job['slot']}.js"
        js.parent.mkdir(parents=True, exist_ok=True)
        started = time.perf_counter()
        convert(job["tree"], job["frames_dir"], js, w, h, conversion_args(frames, args.device, job["extra"]))
        convert_s = time.perf_counter() - started
    row = {"clip": job["clip"], "variant": job["variant"], "size": f"{w}x{h}",
           **evaluate_view(frames, js, distances, args.screen_angle, args.tau, args.min_static, args.stride)}
    if convert_s is not None:
        row["convert_s"] = round(convert_s, 2)
    work = out_dir / "play" / f"{job['clip']}__{job['slot']}"
    cost = playback_cost(job["tree"], js, work)
    if "best_us" in cost:
        row["best_us"] = cost["best_us"]
    return row


def parse_variants(items):
    variants = []
    for item in items:
        name, separator, rest = item.partition("=")
        if not separator or not name:
            raise SystemExit(f"--variant は 名前=convert.py の引数 で渡す: {item}")
        variants.append((name, shlex.split(rest)))
    return variants or [("base", [])]


def build_jobs(args, out_dir):
    """(クリップ, 変換の組) の一覧を作る。--frames-root のときは、元の名前を連番に置き換える。"""
    if args.from_js:
        if not args.frames_dir:
            raise SystemExit("--from-js には --frames-dir が要る")
        jobs = []
        for i, item in enumerate(args.from_js, 1):
            name, separator, path = item.partition("=")
            variant, js = (name, Path(path)) if separator else (f"js-{i}", Path(item))
            jobs.append({"clip": args.set_label or "clip", "variant": variant, "slot": i, "frames_dir": Path(args.frames_dir),
                         "js": js.resolve(), "tree": ROOT})
        return jobs
    if args.frames_root:
        folders = sorted(p for p in Path(args.frames_root).iterdir() if p.is_dir() and any(p.glob("*.png")))
        clips = [(f"{args.set_label or 'clip'}-{i:02d}", folder) for i, folder in enumerate(folders, 1)]
    else:
        w, h = map(int, args.size.split("x"))
        clips = []
        for clip in args.clips:
            # 比べる元の映像は、変換に使ったファイルから読む (ffmpeg の gradients は実行ごとに模様が変わるため)
            folder = out_dir / "clips" / f"{clip}_{w}x{h}"
            if not folder.exists():
                save_frames(make_clip(clip, w, h, args.frames), folder)
            clips.append((clip, folder))
    if args.limit:
        clips = clips[:args.limit]
    trees = [(name, extra, ROOT) for name, extra in parse_variants(args.variant)]
    if args.baseline:
        safe = re.sub(r"[^0-9A-Za-z._-]", "_", args.baseline)
        trees.insert(0, (args.baseline, [], export_tree(args.baseline, out_dir / "trees" / safe)))
    return [{"clip": clip, "variant": name, "slot": i, "frames_dir": folder, "extra": extra, "tree": tree}
            for clip, folder in clips for i, (name, extra, tree) in enumerate(trees)]


def summarize(rows):
    """変換の組ごとの平均。None (n/a) は平均から外し、ちらつきが n/a の本数を明記する。"""
    summary = []
    for variant in dict.fromkeys(r["variant"] for r in rows):
        group = [r for r in rows if r["variant"] == variant]
        item = {"variant": variant, "clips": len(group), "flicker_na": sum(r["flicker_rate"] is None for r in group)}
        for key in dict.fromkeys(k for r in group for k in r):
            values = [r.get(key) for r in group]
            if key in ("clip", "variant", "size"):
                continue
            if any(isinstance(v, list) for v in values):
                item[key] = [rounded(mean_or_none([v[b] for v in values if v is not None]))
                             for b in range(len(L_BANDS))]
            else:
                item[key] = rounded(mean_or_none(values), 4)
        summary.append(item)
    return summary


def cell(value):
    if value is None:
        return "n/a"
    if isinstance(value, list):
        return " / ".join(cell(v) for v in value)
    return str(value)


def table(rows, first):
    keys = first + [k for k in dict.fromkeys(k for r in rows for k in r) if k not in first]
    lines = ["| " + " | ".join(keys) + " |", "|" + "---|" * len(keys)]
    lines += ["| " + " | ".join(cell(r.get(k)) for k in keys) + " |" for r in rows]
    return lines


def write_results(out_dir, rows, meta):
    summary = summarize(rows)
    results = {"schema": 1, "meta": meta, "summary": summary, "rows": rows}
    (out_dir / "results.json").write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")
    lines = ["# ディザの見え方", "",
             f"θ1 = {meta['screen_angle']} 度、τ = {meta['tau']}、静止の下限 {meta['min_static']}、"
             f"色差と粒は {meta['stride']} フレームごと、ちらつきは全フレーム。n/a は静止が足りず数えていない。", "",
             "## 平均", "", *table(summary, ["variant", "clips", "flicker_na"]), "",
             "## クリップごと", "", *table(rows, ["clip", "variant", "size"]), ""]
    (out_dir / "results.md").write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Phase 13 の採用基準の判定
# ---------------------------------------------------------------------------

def pick_rows(results, variant):
    rows = results["rows"]
    names = list(dict.fromkeys(r["variant"] for r in rows))
    if variant is None:
        if len(names) != 1:
            raise SystemExit(f"変換の組が複数ある。--before-variant / --after-variant で選ぶ: {names}")
        variant = names[0]
    return {r["clip"]: r for r in rows if r["variant"] == variant}


def change_pct(pairs, key):
    """クリップごとの変化 (%)。基準値が 0 に近いクリップは比率が暴れるので外す。"""
    changes = []
    for before, after in pairs:
        if before.get(key) is not None and after.get(key) is not None and before[key] > 1e-9:
            changes.append(100 * (after[key] - before[key]) / before[key])
    return changes


def limit_check(pairs, key, mean_limit, worst_limit=None):
    changes = change_pct(pairs, key)
    if not changes:
        return {"key": key, "status": "skip"}
    mean, worst = float(np.mean(changes)), float(np.max(changes))
    ok = mean <= mean_limit and (worst_limit is None or worst <= worst_limit)
    return {"key": key, "mean_pct": round(mean, 2), "worst_pct": round(worst, 2), "mean_limit": mean_limit,
            "worst_limit": worst_limit, "status": "ok" if ok else "ng"}


def flicker_check(pairs, key, floor):
    """基準値が小さいと比率が暴れるので、ちらつきは平均どうしの比で見る。floor 未満なら判定から外して絶対値だけ出す。"""
    usable = [(b[key], a[key]) for b, a in pairs if b.get(key) is not None and a.get(key) is not None]
    if not usable:
        return {"key": key, "status": "skip", "clips": 0}
    before, after = float(np.mean([u[0] for u in usable])), float(np.mean([u[1] for u in usable]))
    out = {"key": key, "before": round(before, 4), "after": round(after, 4), "clips": len(usable)}
    if before < floor:
        return {**out, "status": "skip"}
    change = 100 * (after - before) / before
    return {**out, "mean_pct": round(change, 2), "status": "ok" if change <= -10 else "ng"}


def judge(before_rows, after_rows, main_distance=2, rate_floor=0.005):
    """変更前後の行 ({クリップ: 行}) を Phase 13 の採用基準で判定する。CPU・GPU・ブラウザの一致と再生側を変えないことは数字で測れないので手で確かめる。"""
    clips = [c for c in before_rows if c in after_rows]
    if not clips:
        raise SystemExit("前後で共通のクリップがない")
    pairs = [(before_rows[c], after_rows[c]) for c in clips]
    distances = sorted(int(m.group(1)) for m in (re.fullmatch(r"view_error_k(\d+)", k) for k in pairs[0][0]) if m)
    grain_main = limit_check(pairs, f"grain_visible_k{main_distance}", -10)
    grain_others = [limit_check(pairs, f"grain_visible_k{k}", 0) for k in distances if k != main_distance]
    flickers = [flicker_check(pairs, "flicker_rate", rate_floor), flicker_check(pairs, f"flicker_mag_k{main_distance}", 1e-9)]
    grain_ok = grain_main["status"] == "ok" and all(c["status"] != "ng" for c in grain_others)
    flicker_ok = any(c["status"] == "ok" for c in flickers)
    limits = [limit_check(pairs, f"view_error_k{k}", 0, 5) for k in distances]
    limits += [limit_check(pairs, "zip_bytes", 5, 15), limit_check(pairs, "best_us", 5, 15), limit_check(pairs, "convert_s", 15)]
    return {"clips": len(clips), "grain": [grain_main, *grain_others], "flicker": flickers, "limits": limits,
            "improved": grain_ok or flicker_ok, "passed": (grain_ok or flicker_ok) and all(c["status"] != "ng" for c in limits),
            "manual": ["CPU・GPU・ブラウザ版で同じ方式か", "データ形式と main.js を変えていないか"]}


def run_compare(args):
    paths = [Path(p) for p in args.compare]
    before, after = (json.loads(p.read_text(encoding="utf-8")) for p in paths)
    verdict = judge(pick_rows(before, args.before_variant), pick_rows(after, args.after_variant))
    print(json.dumps(verdict, indent=1, ensure_ascii=False))
    if args.out:
        out_dir = Path(args.out).resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "compare.json").write_text(json.dumps(verdict, indent=1, ensure_ascii=False), encoding="utf-8")
    return 0 if verdict["passed"] else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out")
    parser.add_argument("--clips", nargs="+", default=["anime", "gradients", "mandelbrot", "testsrc2", "pan"])
    parser.add_argument("--size", default="128x72")
    parser.add_argument("--frames", type=int, default=60)
    parser.add_argument("--frames-root", help="フレームのフォルダの親。結果には連番だけを書く")
    parser.add_argument("--set-label", default=None, help="連番の頭に付ける名前 (例: ジャンル)")
    parser.add_argument("--limit", type=int, default=0, help="先頭の本数だけ測る (確認用)")
    parser.add_argument("--variant", action="append", default=[], metavar="名前=引数",
                        help="convert.py に足す引数の組 (複数可)。無ければ既定の設定 base だけ")
    parser.add_argument("--baseline", default=None, help="一緒に比べる git の版 (その版の既定の設定で変換して再生する)")
    parser.add_argument("--from-js", nargs="+", default=[], metavar="[名前=]パス", help="変換済みの .js。--frames-dir と比べる")
    parser.add_argument("--frames-dir", help="--from-js の元のフレーム")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--distances", nargs="+", type=int, default=list(DISTANCES), help="スクリーンの高さの何倍から見るか")
    parser.add_argument("--screen-angle", type=float, default=SCREEN_ANGLE, help="1 倍の距離でスクリーンの高さが占める視角 (度)")
    parser.add_argument("--tau", type=float, default=TAU, help="静止とみなす 5 フレームの Lab の幅")
    parser.add_argument("--min-static", type=float, default=MIN_STATIC_CLIP, help="平均の静止がこれ未満のクリップはちらつきを n/a にする")
    parser.add_argument("--stride", type=int, default=4, help="色差と粒の間引き。ちらつきは全フレーム")
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--compare", nargs=2, metavar=("前", "後"), help="2 つの results.json を Phase 13 の採用基準で判定する。通れば終了コード 0")
    parser.add_argument("--before-variant")
    parser.add_argument("--after-variant")
    args = parser.parse_args(argv)
    if args.compare:
        return run_compare(args)
    if not args.out:
        parser.error("--out が要る")
    out_dir = Path(args.out).resolve()
    if args.frames_root and out_dir.is_relative_to(ROOT):
        parser.error("--frames-root の --out はリポジトリの外にする (外部の映像から作った物を入れないため)")
    out_dir.mkdir(parents=True, exist_ok=True)
    jobs = build_jobs(args, out_dir)
    distances = tuple(args.distances)
    with ThreadPoolExecutor(args.workers) as pool:
        rows = []
        for row in pool.map(lambda job: run_job(job, args, out_dir, distances), jobs):
            rows.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
    # 元のフォルダ名やパスは meta に入れない
    write_results(out_dir, rows, {"distances": list(distances), "screen_angle": args.screen_angle, "tau": args.tau,
                                  "min_static": args.min_static, "stride": args.stride, "device": args.device})
    return 0


if __name__ == "__main__":
    sys.exit(main())
