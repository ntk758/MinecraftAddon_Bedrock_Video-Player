"""実映像での評価 (Phase 8.8): 権利上使える実映像で、データ量・画質・ちらつき・ゲーム内の描画の費用を測る。

合成映像 (codec_bench.py など) は、辞書の再利用や動きの予測を多く (または少なく) 見積もるおそれがあるので、
benchmark/datasets/real_videos.json の実映像 (Blender Foundation のオープンムービー、CC-BY 3.0) でも測る。
映像はリポジトリに入れず、公式の配布元から取得して --cache に置く (予告編は SHA-256 を確かめる。
長い映画は ffmpeg で使う区間だけを読み出す)。

設定ごとに convert.py で変換し、次を出す:
  - データ: 出力 .js の deflate 後のバイト数 (.mcaddon の大きさの目安) と、1 分あたりの KB
  - 画質: ΔE2000 (画素ごと / 3×3 でぼかして)、MS-SSIM (benchmark/palette_bench.py と同じ)
  - ちらつき: 1 フレームで変わる画素の割合
  - ゲーム内: モックの Minecraft で rect / list 方式で再生したときの 1 フレームの呼び出し数・置くブロック数と、
    アドオンの費用のモデル (Windows 版の実測) で見積もった 1 フレームの描画時間 (auto は 2 つの小さい方以下)
  - 変換の時間
--baseline に git の版を渡すと、その版でも同じように変換・再生して並べる。

使い方: python benchmark/real_video_bench.py --out bench_out [--baseline v6.7.0]
結果は <out>/results_real.json と <out>/results_real.md。ffmpeg と node (無ければゲーム内の費用を省略) が必要。
"""
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from codec_bench import FPS, convert, export_tree, play  # noqa: E402
from palette_bench import evaluate  # noqa: E402
from rdo_bench import placement_us  # noqa: E402

DATASET = Path(__file__).resolve().parent / "datasets" / "real_videos.json"
CONFIGS = {
    # 名前: convert.py の引数 (GUI の既定は自動パレット + Blue Noise)
    "default": ["--palette", "auto", "--dither-method", "blue_noise"],
    "no-dither": ["--palette", "auto", "--dither-method", "none"],
}


def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch(entry, cache):
    """映像をキャッシュへ取得してそのパスを返す。segment は使う区間だけを読み出した動画 (先頭が区間の始まり)。"""
    cache.mkdir(parents=True, exist_ok=True)
    if entry.get("segment"):
        path = cache / f"{entry['id']}_{entry['start']}_{entry['duration']}.mp4"
        if not path.exists():
            tmp = path.with_suffix(".part.mp4")
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(entry["start"]), "-i", entry["url"],
                            "-t", str(entry["duration"]), "-an", "-c:v", "libx264", "-crf", "14", "-preset", "fast",
                            str(tmp)], check=True)
            tmp.replace(path)
        return path
    path = cache / Path(entry["url"]).name
    if not path.exists() or sha256_of(path) != entry["sha256"]:
        tmp = path.with_name(path.name + ".part")
        with urllib.request.urlopen(entry["url"], timeout=120) as response, open(tmp, "wb") as f:
            shutil.copyfileobj(response, f)
        if sha256_of(tmp) != entry["sha256"]:
            tmp.unlink()
            raise RuntimeError(f"{entry['id']}: SHA-256 が一致しません (配布元のファイルが変わった可能性)")
        tmp.replace(path)
    return path


def extract_frames(entry, video, folder, width, height):
    """convert.py と同じ縮小・余白で、使う区間を PNG の連番にする。"""
    from convert import build_video_filter

    if folder.exists() and any(folder.glob("*.png")):
        return
    folder.mkdir(parents=True, exist_ok=True)
    start = 0 if entry.get("segment") else entry["start"]
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(start), "-i", str(video), "-t", str(entry["duration"]),
                    "-vf", build_video_filter(width, height, FPS), str(folder / "frame_%04d.png")], check=True)


def measure(tree, frames, frames_dir, js, width, height, extra, work):
    seconds = convert(tree, frames_dir, js, width, height, extra)
    row = evaluate(frames, js)
    row["kb_per_minute"] = round(row["zip_bytes"] / 1024 * 60 * FPS / max(1, len(frames)), 1)
    row["convert_s"] = round(seconds, 1)
    if shutil.which("node"):
        row.update(placement_us(play(tree, js, work, "render=rect"), play(tree, js, work, "render=list")))
    return row


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default="bench_out")
    parser.add_argument("--cache", default=None, help="取得した映像を置くフォルダ (既定は <out>/videos)")
    parser.add_argument("--videos", nargs="+", default=None, help="使う映像の id (既定はすべて)")
    parser.add_argument("--configs", nargs="+", default=list(CONFIGS), choices=list(CONFIGS))
    parser.add_argument("--size", default="128x72")
    parser.add_argument("--baseline", default=None, help="一緒に比べる git の版 (タグなど)")
    args = parser.parse_args(argv)
    width, height = map(int, args.size.split("x"))
    out_dir = Path(args.out).resolve()
    cache = Path(args.cache).resolve() if args.cache else out_dir / "videos"
    entries = json.loads(DATASET.read_text(encoding="utf-8"))["videos"]
    if args.videos:
        entries = [e for e in entries if e["id"] in args.videos]
    trees = [("worktree", ROOT)]
    if args.baseline:
        trees.insert(0, (args.baseline, export_tree(args.baseline, out_dir / "trees" / args.baseline)))

    rows = []
    for entry in entries:
        video = fetch(entry, cache)
        frames_dir = out_dir / "frames" / f"{entry['id']}_{width}x{height}"
        extract_frames(entry, video, frames_dir, width, height)
        frames = [np.asarray(Image.open(p).convert("RGB")) for p in sorted(frames_dir.glob("*.png"))]
        for config in args.configs:
            for label, tree in trees:
                js = out_dir / "real" / f"{entry['id']}_{config}_{label.replace('/', '_')}.js"
                js.parent.mkdir(parents=True, exist_ok=True)
                row = {"video": entry["id"], "config": config, "version": label,
                       **measure(tree, frames, frames_dir, js, width, height, CONFIGS[config], out_dir / "real_play")}
                rows.append(row)
                print(json.dumps(row, ensure_ascii=False), flush=True)

    (out_dir / "results_real.json").write_text(json.dumps(rows, indent=1, ensure_ascii=False), encoding="utf-8")
    keys = list(dict.fromkeys(k for r in rows for k in r))
    lines = [f"実映像 {width}×{height}・{FPS} fps (benchmark/datasets/real_videos.json、CC-BY 3.0 Blender Foundation)", "",
             "| " + " | ".join(keys) + " |", "|" + "---|" * len(keys)]
    lines += ["| " + " | ".join(str(r.get(k, "")) for k in keys) + " |" for r in rows]
    (out_dir / "results_real.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
