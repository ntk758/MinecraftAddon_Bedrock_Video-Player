"""AI の人物の切り抜き (mvcodec/person.py) の速さ・メモリ・マスクの安定性を測る (ローカル専用。CI では動かさない)。

動画 (--video、ローカルにある第三者の動画でもよい。引数で渡すだけで、リポジトリには何も書かない) を、構成 (版 × メモリの段階) ごとに
新しいプロセスで scripts/make_person_videos.py に通し、親のプロセスから次を測る:
  - wall 時間と ms/フレーム (モデルの読み込みを含む)
  - ピーク時のワーキングセット (peak working set) と、確保したメモリ (peak private)。psutil で 200 ms ごとに見る
  - --vram を付けると、(Windows のみ) そのプロセスの GPU の専用メモリ (Dedicated Usage) の最大。Get-Counter で見る
  - 子プロセスが自分で出す統計 (--stats。出所ごとのフレーム数・空のフレーム・場面の切り替わり・モデルごとの推論の ms・工程ごとの ms・
    自分で測った最大のメモリ) を行に入れ、下のマスクから数えた値と合っているかを stats_check に出す (統計を出さない古い版は飛ばす)
出力のマスク動画からは、ブロックの単位 (80x45 にボックス平均で縮めて 0.5 以上を人物とする) で次を求める:
  - 空のフレームの数 (ブロック単位で人物が 1 つも無い。面積 0.5% 未満も別に数える)
  - 1 フレームあたりの「変わるマス」と「1 フレームだけ点滅するマス」(場面の切り替わりをまたぐ変化は含めない)
  - 大きさの跳び: 隣り合うフレームの |Δlog 面積| の p90
場面の切り替わりは、元の動画の構図 (32x18 の明るさの相関と色のヒストグラム) で決める。

    python benchmark/person_bench.py --out D:/bvp_work/bench/person --video clip.mp4 --tree worktree --profiles gpu --duration 30
    python benchmark/person_bench.py --out ... --video a.mp4 --video b.mp4 --tree baseline --tree worktree --profiles gpu full --vram

--tree baseline は --baseline (git の版 (既定 main) か、mvcodec のあるフォルダ) の版。モデルは BVP_MODEL_DIR (環境変数) の場所に
あればそこから読む。マスクの動画と背景の動画は、数値を出した後に消す (残すなら --keep-masks。その場合も --out の下だけ)。
ほかの重い処理と同時に動かさないこと。
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
BLOCKS = (80, 45)
SAMPLE_SECONDS = 0.2
EMPTY_AREA = 0.005
MB = 1 << 20
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def git(*args):
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, encoding="utf-8", check=True).stdout.strip()


def resolve_tree(name, baseline, out_dir):
    """--tree baseline|worktree を、mvcodec と scripts/make_person_videos.py のあるフォルダにする。"""
    if name == "worktree":
        return ROOT, f"worktree ({git('rev-parse', '--short', 'HEAD')})"
    folder = Path(baseline)
    if folder.is_dir() and (folder / "mvcodec").is_dir():
        return folder.resolve(), f"folder {folder.resolve()}"
    sha = git("rev-parse", "--verify", f"{baseline}^{{commit}}")
    dest = out_dir / "trees" / sha[:12]
    if not (dest / "scripts" / "make_person_videos.py").is_file():
        archive = subprocess.run(["git", "-C", str(ROOT), "archive", "--format=tar", sha, "--", "mvcodec",
                                  "scripts/make_person_videos.py"], capture_output=True, check=True).stdout
        dest.mkdir(parents=True, exist_ok=True)
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            tar.extractall(dest, **({"filter": "data"} if hasattr(tarfile, "data_filter") else {}))
    return dest, f"{baseline} ({sha[:7]})"


class VramSampler(threading.Thread):
    """プロセスの GPU の専用メモリ (MB) を Get-Counter で 300 ms ごとに見て、最大を覚える (Windows のみ)。"""

    def __init__(self, pid):
        super().__init__(daemon=True)
        self.pid = pid
        self.peak = None
        self.stop_event = threading.Event()
        self.process = None

    def run(self):
        script = ("while ($true) { (Get-Counter '\\GPU Process Memory(pid_%d*)\\Dedicated Usage' -ErrorAction SilentlyContinue)"
                  ".CounterSamples | ForEach-Object { $_.CookedValue }; Write-Output '--'; Start-Sleep -Milliseconds 300 }") % self.pid
        try:
            self.process = subprocess.Popen(["powershell", "-NoProfile", "-Command", script], stdout=subprocess.PIPE,
                                            stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, text=True,
                                            creationflags=CREATE_NO_WINDOW)
        except OSError:
            return
        total = 0.0
        for line in self.process.stdout:
            line = line.strip()
            if line == "--":
                self.peak = max(self.peak or 0.0, total / MB)
                total = 0.0
            elif re.match(r"^[\d.]+$", line):
                total += float(line)
            if self.stop_event.is_set():
                break

    def stop(self):
        self.stop_event.set()
        if self.process is not None:
            self.process.kill()


def supports_stats(tree_dir):
    """この版の人物の切り抜きが --stats (統計の JSON) を出せるか。"""
    try:
        return '"--stats"' in (tree_dir / "mvcodec" / "person.py").read_text(encoding="utf-8")
    except OSError:
        return False


def read_stats(out_dir):
    """子プロセスが out_dir に書いた統計 (person-stats-*.json のうち一番新しいもの)。無ければ None。"""
    files = sorted(out_dir.glob("person-stats-*.json"), key=lambda path: path.stat().st_mtime)
    return json.loads(files[-1].read_text(encoding="utf-8")) if files else None


def run_pipeline(tree_dir, video, out_dir, profile, model, fps, duration, ffmpeg, want_vram):
    """make_person_videos.py を新しいプロセスで動かし、時間とメモリを測る。戻り値は (結果, マスクの動画, 背景の動画)。"""
    import psutil

    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("person-stats-*.json"):
        old.unlink()
    mask, background = out_dir / "mask.mp4", out_dir / "background.mp4"
    argv = [sys.executable, str(tree_dir / "scripts" / "make_person_videos.py"), str(video), str(mask), str(background),
            "--fps", str(fps), "--ffmpeg", ffmpeg, "--model", model, "--profile", profile]
    if duration is not None:
        argv += ["--duration", str(duration)]
    if supports_stats(tree_dir):
        argv += ["--stats", str(out_dir)]
    with open(out_dir / "log.txt", "w", encoding="utf-8", errors="replace") as log:
        started = time.perf_counter()
        process = subprocess.Popen(argv, stdout=log, stderr=subprocess.STDOUT, cwd=tree_dir, creationflags=CREATE_NO_WINDOW)
        launcher = psutil.Process(process.pid)
        worker = None
        vram = None
        peaks = {"wset": 0, "private": 0}
        try:
            while process.poll() is None:
                if worker is None:
                    # venv の python.exe は本物の python を子として起動する (Windows)。測るのは本物の方
                    worker = next((c for c in launcher.children(recursive=True) if "python" in c.name().lower()), None)
                    if worker is None and time.perf_counter() - started > 5:
                        worker = launcher
                    if worker is not None and want_vram and os.name == "nt":
                        vram = VramSampler(worker.pid)
                        vram.start()
                if worker is not None:
                    try:
                        info = worker.memory_info()
                        peaks["wset"] = max(peaks["wset"], getattr(info, "peak_wset", info.rss), info.rss)
                        peaks["private"] = max(peaks["private"], getattr(info, "peak_pagefile", 0), getattr(info, "private", 0))
                    except psutil.Error:
                        pass
                time.sleep(SAMPLE_SECONDS)
        finally:
            wall = time.perf_counter() - started
            if vram:
                vram.stop()
    result = {"exit_code": process.returncode, "wall_s": wall, "peak_working_set_mb": peaks["wset"] / MB,
              "peak_private_mb": peaks["private"] / MB, "peak_vram_mb": vram.peak if vram else None,
              "stats": read_stats(out_dir)}
    return result, mask, background


# ---------------------------------------------------------------------------
# マスクの品質
# ---------------------------------------------------------------------------

def read_rgb(path, fps, ffmpeg, long_side):
    sys.path.insert(0, str(ROOT))
    from mvcodec.person import read_frames

    return read_frames(path, fps, None, ffmpeg, long_side)


def scene_cuts(frames):
    """構図で決める場面の切り替わり (cut[t] = フレーム t - 1 から t で切り替わった)。
    32x18 の明るさの相関が低い、または相関がやや低く色のヒストグラムが大きく違う。"""
    cut = np.zeros(len(frames), bool)
    previous_luma = previous_hist = None
    for t, frame in enumerate(frames):
        luma = np.asarray(Image.fromarray(frame).convert("L").resize((32, 18), Image.Resampling.BOX), np.float32)
        luma = luma - luma.mean()
        luma /= np.linalg.norm(luma) + 1e-6
        bins = (frame[::4, ::4].reshape(-1, 3) >> 5).astype(np.int64)
        hist = np.bincount(bins[:, 0] * 64 + bins[:, 1] * 8 + bins[:, 2], minlength=512).astype(np.float32)
        hist /= hist.sum()
        if previous_luma is not None:
            corr = float((luma * previous_luma).sum())
            distance = 0.5 * float(np.abs(hist - previous_hist).sum())
            cut[t] = corr < 0.5 or (corr < 0.75 and distance > 0.5)
        previous_luma, previous_hist = luma, hist
    return cut


def block_statistics(masks, cut):
    """マスク (0〜1 の 2 次元配列の並び) と場面の切り替わり cut (cut[t] = t - 1 から t で切り替わった) から、ブロック単位の安定性を求める。"""
    count = len(masks)
    blocks = np.array([np.asarray(Image.fromarray(m.astype(np.float32)).resize(BLOCKS, Image.Resampling.BOX)) >= 0.5
                       for m in masks]).reshape(count, -1)
    same = ~cut[1:]
    same3 = ~cut[1:-1] & ~cut[2:]
    changed = (blocks[1:] != blocks[:-1]).sum(axis=1)
    blink = ((blocks[1:-1] != blocks[:-2]) & (blocks[:-2] == blocks[2:])).sum(axis=1)
    area = blocks.mean(axis=1)
    log_area = np.log(np.maximum(area, 1e-3))
    jump = np.abs(np.diff(log_area))[same & (area[1:] > 0) & (area[:-1] > 0)]
    full_area = np.array([(m >= 0.5).mean() for m in masks])
    return {
        "frames": count,
        "scene_cuts": int(cut[1:].sum()),
        "empty_frames_blocks": int((blocks.sum(axis=1) == 0).sum()),
        "empty_frames_area": int((full_area < EMPTY_AREA).sum()),
        "changed_per_frame": float(changed[same].mean()) if same.any() else None,
        "blink_per_frame": float(blink[same3].mean()) if same3.any() else None,
        "dlog_area_p90": float(np.percentile(jump, 90)) if len(jump) else None,
        "blocks_per_frame": float(blocks.sum(axis=1).mean()),
    }


def mask_statistics(mask_path, source, fps, ffmpeg):
    """出力のマスク動画 mask_path と元の動画 source から、ブロック単位の安定性を求める。"""
    masks = [frame[..., 0].astype(np.float32) / 255 for frame in read_rgb(mask_path, fps, ffmpeg, 4096)]
    frames = list(read_rgb(source, fps, ffmpeg, 640))
    count = min(len(masks), len(frames))
    return block_statistics(masks[:count], scene_cuts(frames[:count]))


def check_stats(stats, measured):
    """子プロセスの統計 stats と、マスクの動画から数えた値 measured を比べる。{項目: (統計, 数えた値, 合っているか)}。
    ブロック単位の「空」(empty_frames_blocks) は面積 0.5% 未満より厳しいので、面積のほうと比べる。"""
    pairs = {"frames": (stats.get("frames"), measured["frames"]),
             "empty_frames": (stats.get("empty_frames"), measured["empty_frames_area"]),
             "scene_cuts": (stats.get("scene_cuts"), measured["scene_cuts"])}
    return {name: (a, b, a == b) for name, (a, b) in pairs.items()}


def format_stats(stats):
    """統計の主な数を 1 行にする (出所ごとのフレーム数・予備に頼った理由・モデルごとの推論の中央値と p95)。"""
    if not stats:
        return ""
    models = " ".join(f"{name}[{m['ms']['n']}x {m['ms']['median']:.0f}/{m['ms']['p95']:.0f}ms]"
                      for name, m in (stats.get("models") or {}).items() if m.get("ms"))
    sources = " ".join(f"{k}={v}" for k, v in (stats.get("sources") or {}).items())
    return (f"stats: {sources} flagged={stats.get('flagged', '-')} reasons={(stats.get('fallback') or {}).get('reasons', '-')} "
            f"{models} mem={stats.get('memory')}")


def format_row(row):
    def number(value, digits=1):
        return "-" if value is None else f"{value:.{digits}f}"

    return (f"{row['video']:<24} {row['tree']:<9} {row['profile']:<5} exit {row['exit_code']} "
            f"{number(row['ms_per_frame'])} ms/f  wset {number(row['peak_working_set_mb'], 0)} MB  "
            f"private {number(row['peak_private_mb'], 0)} MB  vram {number(row['peak_vram_mb'], 0)} MB | "
            f"empty(blocks) {row.get('empty_frames_blocks', '-')}/{row.get('frames', '-')}  "
            f"empty(area) {row.get('empty_frames_area', '-')}  changed {number(row.get('changed_per_frame'))}  "
            f"blink {number(row.get('blink_per_frame'))}  dlogA p90 {number(row.get('dlog_area_p90'), 3)}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", required=True, help="結果を置くフォルダ (このリポジトリの中は拒否する)")
    parser.add_argument("--video", action="append", required=True, help="動画 (何度でも指定可)")
    parser.add_argument("--tree", action="append", choices=["baseline", "worktree"], help="測る版 (何度でも指定可、既定 worktree)")
    parser.add_argument("--baseline", default="main", help="--tree baseline の版: git の版 (既定 main) か mvcodec のあるフォルダ")
    parser.add_argument("--profiles", nargs="+", default=["gpu"], choices=["full", "gpu", "cpu", "photo"], help="メモリの段階")
    parser.add_argument("--model", default="anime", choices=["anime", "photo"], help="切り抜きのモデル")
    parser.add_argument("--fps", type=float, default=10)
    parser.add_argument("--duration", type=float, default=None, help="動画の先頭から何秒を使うか")
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--vram", action="store_true", help="プロセスの GPU の専用メモリの最大も測る (Windows のみ)")
    parser.add_argument("--keep-masks", action="store_true", help="マスクと背景の動画を残す (既定は数値を出した後に消す)")
    args = parser.parse_args(argv)
    out = Path(args.out).resolve()
    try:
        out.relative_to(ROOT)
    except ValueError:
        pass
    else:
        parser.error(f"--out はリポジトリの外にしてください: {out}")
    out.mkdir(parents=True, exist_ok=True)

    rows = []
    trees = {name: resolve_tree(name, args.baseline, out) for name in dict.fromkeys(args.tree or ["worktree"])}
    for video in args.video:
        stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", Path(video).stem).strip("_") or "video"
        for name, (tree_dir, label) in trees.items():
            for profile in args.profiles:
                print(f"[{stem}] {label} / {profile}", flush=True)
                run_dir = out / "runs" / f"{stem}_{name}_{profile}"
                result, mask, background = run_pipeline(tree_dir, video, run_dir, profile, args.model, args.fps, args.duration,
                                                        args.ffmpeg, args.vram)
                row = {"video": stem, "tree": name, "tree_label": label, "profile": profile, "model": args.model, **result}
                if result["exit_code"] == 0 and mask.is_file():
                    row.update(mask_statistics(mask, video, args.fps, args.ffmpeg))
                    row["ms_per_frame"] = result["wall_s"] * 1000 / max(1, row["frames"])
                    if result["stats"]:
                        row["stats_check"] = check_stats(result["stats"], row)
                else:
                    row["ms_per_frame"] = None
                if not args.keep_masks:
                    for path in (mask, background):
                        path.unlink(missing_ok=True)
                rows.append(row)
                print("  " + format_row(row), flush=True)
                if row.get("stats"):
                    print("  " + format_stats(row["stats"]), flush=True)
                    print("  stats_check (統計 / マスクから数えた値): " + ", ".join(
                        f"{name} {a}/{b} {'OK' if ok else 'NG'}" for name, (a, b, ok) in row.get("stats_check", {}).items()), flush=True)
                (out / "person_results.json").write_text(json.dumps(
                    {"date": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), "fps": args.fps, "duration": args.duration,
                     "rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    print()
    for row in rows:
        print(format_row(row))
    return 0


if __name__ == "__main__":
    sys.exit(main())
