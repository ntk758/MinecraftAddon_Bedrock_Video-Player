"""圧縮率と描画コストのベンチマーク (Phase 7.x の効果を、別の版と同じ入力で比べる)。

著作権の心配がない合成映像 (パン・アニメ風・白黒シルエット・ffmpeg の testsrc2 / mandelbrot) を作り、
指定した git の版 (既定: main) と作業中の版の convert.py でそれぞれ変換して、次を比べる:
  - データの大きさ: 出力 .js のバイト数と、.mcaddon と同じ deflate 圧縮後のバイト数
  - 描画コスト: モックの Minecraft で main.js を再生したときの、1 フレームあたりの API 呼び出し数と置いたブロック数

使い方:
    python benchmark/codec_bench.py --out bench_out                 # 平面スクリーン (main と比較)
    python benchmark/codec_bench.py --out bench_out --suite volume  # 立体 (--volume-baseline の版と比較)
結果は <out>/results_<suite>.json と <out>/results_<suite>.md に書く。node と ffmpeg が必要 (無ければ一部を省略)。
"""
import argparse
import io
import json
import shutil
import subprocess
import sys
import tarfile
import time
import zlib
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
FPS = 10


# ---------------------------------------------------------------------------
# 合成映像
# ---------------------------------------------------------------------------

def smooth_noise(rng, h, w, scale):
    """なめらかな 0〜1 のノイズ (粗い乱数を拡大して重ねる)。"""
    out = np.zeros((h, w), np.float32)
    for octave, weight in ((scale, 0.6), (scale // 2, 0.3), (max(2, scale // 4), 0.1)):
        coarse = rng.random((h // octave + 2, w // octave + 2)).astype(np.float32)
        img = Image.fromarray((coarse * 255).astype(np.uint8)).resize(((w // octave + 2) * octave, (h // octave + 2) * octave),
                                                                     Image.BICUBIC)
        out += weight * np.asarray(img, np.float32)[:h, :w] / 255.0
    return out


def clip_pan(w, h, n):
    """風景をゆっくり横へパンする (カメラの動き)。"""
    rng = np.random.default_rng(1)
    big_w, big_h = w + 2 * n, h + n // 2
    land = smooth_noise(rng, big_h, big_w, 16)
    sky = np.linspace(0, 1, big_h)[:, None]
    rgb = np.stack([60 + 150 * land, 90 + 120 * land * (1 - sky) + 40 * sky, 140 + 100 * sky * (1 - land)], -1)
    rgb = np.clip(rgb, 0, 255).astype(np.uint8)
    return [rgb[t // 4:t // 4 + h, int(1.5 * t):int(1.5 * t) + w] for t in range(n)]


def clip_anime(w, h, n):
    """平坦な色のアニメ風: 背景の丘がパンし、キャラクターがまばたきして口を動かし、字幕が出る。"""
    frames = []
    for t in range(n):
        img = Image.new("RGB", (w, h), (150, 200, 245))
        d = ImageDraw.Draw(img)
        for i in range(-1, w // 24 + 3):
            cx = i * 24 - (t % 24)
            d.ellipse([cx - 20, h * 0.55, cx + 20, h * 1.2], fill=(90, 170, 90))
        d.rectangle([0, int(h * 0.8), w, h], fill=(110, 80, 50))
        bx = int(w * 0.35 + 6 * np.sin(t / 6))
        d.ellipse([bx - 14, int(h * 0.25), bx + 14, int(h * 0.25) + 28], fill=(250, 220, 190), outline=(40, 30, 30))
        d.rectangle([bx - 12, int(h * 0.25) + 28, bx + 12, int(h * 0.25) + 52], fill=(200, 50, 60))
        ey = int(h * 0.25) + 11
        if t % 12 not in (0, 1):
            d.ellipse([bx - 7, ey, bx - 3, ey + 5], fill=(30, 30, 60))
            d.ellipse([bx + 3, ey, bx + 7, ey + 5], fill=(30, 30, 60))
        else:
            d.line([bx - 7, ey + 3, bx - 3, ey + 3], fill=(30, 30, 60))
            d.line([bx + 3, ey + 3, bx + 7, ey + 3], fill=(30, 30, 60))
        mouth = 1 + 3 * (t // 2 % 2)
        d.rectangle([bx - 3, ey + 10, bx + 3, ey + 10 + mouth], fill=(120, 20, 20))
        text = ["HELLO WORLD", "BLOCK VIDEO", "PLAYER 3D"][t // 20 % 3][: 1 + (t % 20)]
        d.text((4, h - 12), text, fill=(255, 255, 255), stroke_width=1, stroke_fill=(0, 0, 0))
        frames.append(np.asarray(img))
    return frames


def clip_silhouette(w, h, n):
    """白黒のシルエット (Bad Apple 風): 人の形が歩き、途中で白黒が反転する。"""
    frames = []
    for t in range(n):
        img = Image.new("L", (w, h), 255)
        d = ImageDraw.Draw(img)
        cx = int(w * 0.2 + (w * 0.6) * t / n)
        top = int(h * 0.15)
        d.ellipse([cx - 6, top, cx + 6, top + 12], fill=0)
        d.rectangle([cx - 5, top + 12, cx + 5, top + 34], fill=0)
        swing = int(8 * np.sin(t / 3))
        d.line([cx, top + 16, cx - 10 + swing, top + 28], fill=0, width=4)
        d.line([cx, top + 16, cx + 10 - swing, top + 28], fill=0, width=4)
        d.line([cx, top + 34, cx - 6 + swing, h - 4], fill=0, width=5)
        d.line([cx, top + 34, cx + 6 - swing, h - 4], fill=0, width=5)
        for k in range(6):
            px = int((k * 37 + t * (2 + k)) % w)
            py = int((k * 13 + t * (1 + k % 3)) % h)
            d.ellipse([px, py, px + 3, py + 3], fill=0)
        if t >= n // 2:
            img = Image.eval(img, lambda v: 255 - v)
        frames.append(np.asarray(img.convert("RGB")))
    return frames


def clip_ffmpeg(source, w, h, n):
    cmd = ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"{source}=size={w}x{h}:rate={FPS}",
           "-frames:v", str(n), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    return list(np.frombuffer(raw, np.uint8).reshape(-1, h, w, 3))


def make_clip(name, w, h, n):
    if name == "pan":
        return clip_pan(w, h, n)
    if name == "anime":
        return clip_anime(w, h, n)
    if name == "silhouette":
        return clip_silhouette(w, h, n)
    if name == "testsrc2":
        return clip_ffmpeg("testsrc2", w, h, n)
    if name == "mandelbrot":
        return clip_ffmpeg("mandelbrot", w, h, n)
    raise ValueError(name)


def save_frames(frames, folder):
    folder.mkdir(parents=True, exist_ok=True)
    for i, frame in enumerate(frames):
        Image.fromarray(frame).save(folder / f"frame_{i:04d}.png")


# ---------------------------------------------------------------------------
# 版の取り出し・変換・再生
# ---------------------------------------------------------------------------

def export_tree(ref, dest):
    """git の版 ref をフォルダへ取り出す (ref が "worktree" なら作業中のフォルダをそのまま使う)。"""
    if ref == "worktree":
        return ROOT
    dest = Path(dest)
    if (dest / "convert.py").exists():
        return dest
    archive = subprocess.run(["git", "-C", str(ROOT), "archive", "--format=tar", ref], capture_output=True, check=True).stdout
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(dest, **({"filter": "data"} if hasattr(tarfile, "data_filter") else {}))
    return dest


def convert(tree, frames_dir, out, w, h, extra):
    argv = [sys.executable, str(tree / "convert.py"), "--frames-dir", str(frames_dir), "--output", str(out),
            "--width", str(w), "--height", str(h), "--fps", str(FPS), "--palette", "full", "--device", "cpu",
            "--threads", "4", *extra]
    start = time.perf_counter()
    result = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=tree)
    if result.returncode != 0:
        raise RuntimeError(f"convert failed ({tree}): {result.stderr[-2000:]}")
    return time.perf_counter() - start


def sizes(js_path):
    raw = Path(js_path).read_bytes()
    data = json.loads(raw[raw.index(b"{"):raw.rindex(b"}") + 1])
    return {
        "js_bytes": len(raw),
        "zip_bytes": len(zlib.compress(raw, 6)),
        "frames": data["frame_count"],
    }


def play(tree, js_path, work, *node_args):
    """tree の main.js / codec.js を、作業中の版のモックで再生して API 呼び出しの回数を数える。"""
    if shutil.which("node") is None:
        return None
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    shutil.copy(tree / "main.js", work / "main.js")
    shutil.copy(tree / "codec.js", work / "codec.js")
    shutil.copy(js_path, work / "frames_demo.js")
    shutil.copy(ROOT / "tests" / "js" / "run_main.mjs", work / "run_main.mjs")
    (work / "videos.js").write_text(
        'import { FRAME_DATA as video_demo } from "./frames_demo.js";\n'
        'export const VIDEOS = { "demo": video_demo };\n'
        'export const VIDEO_LIST = [{ id: "demo", title: "Demo", frame_count: video_demo.frame_count, '
        'width: video_demo.width, height: video_demo.height }];\n', encoding="utf-8")
    for module, source in (("server", "server.js"), ("server-ui", "server-ui.js")):
        target = work / "node_modules" / "@minecraft" / module
        target.mkdir(parents=True)
        (target / "package.json").write_text(json.dumps({"name": f"@minecraft/{module}", "type": "module", "main": "index.js"}))
        shutil.copy(ROOT / "tests" / "js" / "minecraft_mock" / source, target / "index.js")
    (work / "package.json").write_text(json.dumps({"type": "module"}))
    result = subprocess.run(["node", "run_main.mjs", *node_args], cwd=work, capture_output=True, text=True,
                            encoding="utf-8", timeout=1800)
    if result.returncode != 0:
        # 例: main の版は 100 チャンクを超えるスクリーンで tickingarea の登録に失敗し、設置できない
        print(f"playback failed ({tree.name}): {result.stderr.strip().splitlines()[0][:200]}", file=sys.stderr)
        shutil.rmtree(work, ignore_errors=True)
        return {"failed": True}
    out = json.loads(result.stdout)
    stats = out["stats"]
    frames = max(1, out["frameCount"])
    shutil.rmtree(work, ignore_errors=True)
    return {
        "calls_per_frame": (stats["setBlockCalls"] + stats["fillCalls"]) / frames,
        "blocks_per_frame": stats["blocksPlaced"] / frames,
        "script_ms_per_frame": out.get("playMs", 0) / frames,
    }


# ---------------------------------------------------------------------------
# スイート
# ---------------------------------------------------------------------------

SCREEN_CONFIGS = [
    # (名前, 版, 追加の引数, 再生する描画方式)
    ("v4 (main)", "baseline", [], [None]),
    ("v5 delta only", "worktree", ["--no-motion", "--tile-size", "0"], []),
    ("v5 + motion", "worktree", ["--tile-size", "0"], []),
    ("v5 + motion + tiles", "worktree", [], ["render=rect", "render=list", "render=block"]),
]

VOLUME_CONFIGS = [
    # --volume-baseline を指定したときだけ使う (3D 動き補償の前の版)
    ("before (2D MV, hollow, slices)", "volume_baseline", ["--hollow"], ["render=rect"]),
    ("voxels (3D MV, solid, 3D boxes)", "worktree", ["--volume-encoding", "voxels"], ["render=rect", "render=list"]),
    ("voxels --hollow", "worktree", ["--hollow"], ["render=rect"]),
    ("planes (2.5D)", "worktree", ["--volume-encoding", "planes"], ["render=rect", "render=list"]),
    ("planes --no-motion", "worktree", ["--volume-encoding", "planes", "--no-motion"], []),
]


def slug(text):
    return "".join(c if c.isalnum() else "_" for c in text).strip("_")


def run_screen_suite(args, trees, out_dir):
    rows = []
    for size in args.sizes:
        w, h = map(int, size.split("x"))
        for clip in args.clips:
            frames_dir = out_dir / "clips" / f"{clip}_{w}x{h}"
            if not frames_dir.exists():
                save_frames(make_clip(clip, w, h, args.frames), frames_dir)
            for dither in args.dithers:
                for name, tree_key, extra, renders in SCREEN_CONFIGS:
                    tree = trees[tree_key]
                    js = out_dir / "js" / f"{clip}_{w}x{h}_{dither}_{slug(name)}.js"
                    js.parent.mkdir(parents=True, exist_ok=True)
                    seconds = convert(tree, frames_dir, js, w, h, ["--dither-method", dither, *extra])
                    row = {"clip": clip, "size": size, "dither": dither, "config": name, "encode_s": round(seconds, 1),
                           **sizes(js)}
                    for render in renders:
                        node_args = [render] if render else []
                        stats = play(tree, js, out_dir / "play", *node_args)
                        if stats and stats.get("failed"):
                            row[f"calls_{render or 'default'}"] = "setup failed"
                        elif stats:
                            row[f"calls_{render or 'default'}"] = round(stats["calls_per_frame"], 1)
                            row[f"blocks_{render or 'default'}"] = round(stats["blocks_per_frame"], 1)
                    rows.append(row)
                    print(json.dumps(row, ensure_ascii=False), flush=True)
    return rows


def run_volume_suite(args, trees, out_dir):
    rows = []
    w, h = map(int, args.volume_size.split("x"))
    for clip, mode in (("silhouette", "extrude"), ("silhouette", "inflate"), ("mandelbrot", "relief"),
                       ("testsrc2", "relief")):
        frames_dir = out_dir / "clips" / f"{clip}_{w}x{h}"
        if not frames_dir.exists():
            save_frames(make_clip(clip, w, h, args.frames), frames_dir)
        for name, tree_key, extra, renders in VOLUME_CONFIGS:
            if tree_key not in trees or (mode == "inflate" and tree_key == "volume_baseline"):
                continue  # 前の版を指定していない / inflate は前の版に無い
            tree = trees[tree_key]
            js = out_dir / "js" / f"vol_{clip}_{mode}_{slug(name)}.js"
            js.parent.mkdir(parents=True, exist_ok=True)
            seconds = convert(tree, frames_dir, js, w, h, ["--mode", mode, "--depth", str(args.depth), *extra])
            row = {"clip": clip, "mode": mode, "config": name, "encode_s": round(seconds, 1), **sizes(js)}
            for render in renders:
                stats = play(tree, js, out_dir / "play", render)
                if stats and stats.get("failed"):
                    row[f"calls_{render}"] = "setup failed"
                elif stats:
                    row[f"calls_{render}"] = round(stats["calls_per_frame"], 1)
                    row[f"blocks_{render}"] = round(stats["blocks_per_frame"], 1)
                    row[f"script_ms_{render}"] = round(stats["script_ms_per_frame"], 2)
            rows.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
    return rows


def write_markdown(rows, path):
    keys = []
    for row in rows:
        keys += [k for k in row if k not in keys]
    lines = ["| " + " | ".join(keys) + " |", "|" + "---|" * len(keys)]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(k, "")) for k in keys) + " |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default="bench_out")
    parser.add_argument("--suite", choices=["screen", "volume"], default="screen")
    parser.add_argument("--baseline", default="main", help="平面スクリーンで比べる git の版")
    parser.add_argument("--volume-baseline", default=None,
                        help="立体で一緒に比べる git の版 (例: 96e1f14 = 3D 動き補償の前)。省略時は作業中の版の送り方どうしを比べる")
    parser.add_argument("--clips", nargs="+", default=["pan", "anime", "silhouette", "testsrc2", "mandelbrot"])
    parser.add_argument("--sizes", nargs="+", default=["128x72"])
    parser.add_argument("--dithers", nargs="+", default=["none", "blue_noise"])
    parser.add_argument("--frames", type=int, default=60)
    parser.add_argument("--volume-size", default="64x48")
    parser.add_argument("--depth", type=int, default=16)
    args = parser.parse_args(argv)

    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    trees = {"worktree": ROOT}
    if args.suite == "screen":
        trees["baseline"] = export_tree(args.baseline, out_dir / "trees" / args.baseline)
        rows = run_screen_suite(args, trees, out_dir)
    else:
        if args.volume_baseline:
            trees["volume_baseline"] = export_tree(args.volume_baseline, out_dir / "trees" / args.volume_baseline)
        rows = run_volume_suite(args, trees, out_dir)
    (out_dir / f"results_{args.suite}.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    write_markdown(rows, out_dir / f"results_{args.suite}.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
