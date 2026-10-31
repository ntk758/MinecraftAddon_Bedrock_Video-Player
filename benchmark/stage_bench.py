"""だまし絵 (--view anamorphic) の前後比較ベンチマーク: 投影・描き直し・展開 (micro) と、モックの上での通し再生 (still / walk)。

git の版 (既定: main) の main.js・codec.js と、作業中の木のそれらを、同じ入力で比べる。入力は乱数の種と式で毎回同じものを
作り (out/fixtures に残す)、ローカルにある第三者のアドオン (--addon) も同じように測れる。

    python benchmark/stage_bench.py --out D:/bvp_work/bench/wp --baseline main
    python benchmark/stage_bench.py --out D:/bvp_work/bench/wp --baseline main --addon D:/x/my_mmd80.mcaddon
    python benchmark/stage_bench.py --out D:/bvp_work/bench/wp --quick --baseline worktree   # smoke (mmd40 を 20 フレームだけ)

素材 (--fixtures、既定は all):
  mmd40・mmd80  tests/mmd_fixture.py の箱人形を踊らせ (mvcodec.mmd.mmd_to_vox、高さ 40・80)、GUI の MMD のだまし絵と同じ引数で変換する。
                表面だけの殻なので、実在の MMD と同じく中が空洞になる (見える面の割合は実在の MMD と違う)
  solid56       コードで作る、中身の詰まった人型 (高さ 56、腕と脚が動く) の .vox
  person80      ffmpeg で作った白い人型の動画を人物の範囲にした inflate (80x45、奥行き 16)
  person80_bg   person80 に testsrc2 の背景の映像 (後ろの壁の大きさ、--fit cover) を、隠し項目 <id>_bg として加えたもの
測るもの:
  micro   benchmark/js/stage_micro.mjs を node --jitless で動かし、同じプロセスで baseline と作業中の木を交互に測る
          (変化のあるフレームの投影 ms・目が動いたときの描き直し ms・全フレームの展開 ms と、盤面の食い違うマスの数)
  still   benchmark/js/stage_play.mjs でモックの上で main.js を最後まで再生する。目は舞台の正面の決まった位置
  walk    同じ再生で、目を 1 tick に 0.2 ブロックずつ舞台の中で往復させる
          (置く費用はゲーム内の実測値。1 フレームの平均・遅れたフレーム・tick の ms・最後の盤面の SHA-1)
結果は <out>/results.json と <out>/results.md に書く。第三者の素材から作ったものは --out の下にだけ置き、数値以外は残さない
(--out がこのリポジトリの中なら拒否する)。ほかの重い処理と同時に動かさないこと (時間が ±40〜60% ぶれる)。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
JS_DIR = HERE / "js"
MOCK_DIR = ROOT / "tests" / "js" / "minecraft_mock"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

FPS = 10
# 素材の作り方 (このファイルの build_fixture) を変えたら上げる (out/fixtures の作り置きを使わず、作り直させる)。
# 変換コード (convert.py・mvcodec・pack_builder.py) の内容は stamp に sha1 で入るので、上げなくても作り直される
# 2: .vox のだまし絵の変換が閉じた空洞を埋めるようになった
FIXTURE_VERSION = 2
FIXTURES = ("mmd40", "mmd80", "solid56", "person80", "person80_bg")
# ゲーム内の実測 (tests/js/minecraft_mock の costs と同じ値、マイクロ秒) と、モックとゲーム内の時間の比の見積り
COST_US = {"setBlock": 2.3, "fillCall": 15, "fillBlock": 0.55, "listCall": 15, "listBlock": 1.42}
# 比の根拠: 同じアドオンのゲーム内の実測 ÷ WP1 のモックの通し再生 (still) の 1 フレームの平均 =
# 44.3 ÷ 10.1 = 4.39 (ローカルの MMD アドオン o40)、216.8 ÷ 47.5 = 4.56 (o80)。下限 3.1 はそれ以前の実測。上限はこの 2 つを含める
GAME_RATIO = (3.1, 4.6)
EVENT_NAMESPACE_LINE = 'const EVENT_NAMESPACE = "badapple";'


# ---------------------------------------------------------------------------
# 素材
# ---------------------------------------------------------------------------

def doll_dance_keys(seconds):
    """箱人形の踊り: 腕を振り、足を交互に上げ、体が上下する。30 fps のキーを 5 フレームごとに置く (補間は直線)。"""
    keys = []
    last = int(math.ceil(seconds * 30))
    for frame in sorted({*range(0, last + 1, 5), last}):
        t = frame / 30
        wave = math.sin(2 * math.pi * 0.8 * t)
        for name, sign, phase in (("左腕", 1, 0.0), ("右腕", -1, math.pi / 2)):
            angle = sign * 0.9 * math.sin(2 * math.pi * 0.8 * t + phase)
            keys.append((name, frame, (0, 0, 0), (0, 0, math.sin(angle / 2), math.cos(angle / 2))))
        for name, lift in (("左足ＩＫ", max(0.0, wave)), ("右足ＩＫ", max(0.0, -wave))):
            keys.append((name, frame, (0, 3 * lift, -1.5 * lift), (0, 0, 0, 1)))
        keys.append(("センター", frame, (0.8 * math.sin(2 * math.pi * 0.4 * t), 0.5 * abs(wave), 0), (0, 0, 0, 1)))
    return keys


def make_mmd_vox(path, height, frames):
    """箱人形 (tests/mmd_fixture.py) を踊らせた立体のアニメ (.vox)。frames フレーム (10 fps)。"""
    from mmd_fixture import write_doll
    from mvcodec.mmd import mmd_to_vox

    with tempfile.TemporaryDirectory(prefix="bvp-doll-") as folder:
        pmx, vmd = write_doll(Path(folder), doll_dance_keys((frames - 1) / FPS))
        mmd_to_vox(pmx, vmd, path, height=height, fps=FPS, duration=(frames - 1) / FPS + 1e-6)


def make_solid_vox(path, frames):
    """中身の詰まった人型の .vox (高さ 56)。腕が肩を中心に振れ、脚が前後に動く。MagicaVoxel の座標 (z が上)。"""
    from mvcodec.volume import write_vox

    size = (44, 24, 56)
    xs, ys, zs = np.meshgrid(np.arange(size[0]) + 0.5, np.arange(size[1]) + 0.5, np.arange(size[2]) + 0.5, indexing="ij")
    cx, cy = 22.0, 12.0
    models = []
    for k in range(frames):
        phase = 2 * math.pi * 0.8 * (k / FPS)
        index = np.zeros(size, np.uint8)
        # 胴
        index[(abs(xs - cx) <= 7) & (abs(ys - cy) <= 4) & (zs >= 24) & (zs <= 43)] = 2
        # 頭
        index[((xs - cx) ** 2 + (ys - cy) ** 2 + (zs - 49.5) ** 2) <= 5.5 ** 2] = 1
        # 脚 (前後に振れる)
        for side, sign in ((-1, 1), (1, -1)):
            swing = 4 * math.sin(phase) * sign
            leg = (abs(xs - (cx + side * 3.5)) <= 2.5) & (abs(ys - (cy + swing)) <= 3) & (zs <= 24)
            index[leg] = 3
            index[leg & (zs <= 3)] = 4
        # 腕 (肩 z = 41 を中心に、体の横で前後に開く線分)
        for side, sign in ((-1, 1), (1, -1)):
            angle = 0.6 * math.sin(phase + (0 if sign > 0 else math.pi))
            shoulder = np.array([cx + side * 9.0, cy, 41.0])
            tip = shoulder + np.array([side * 3.0 * math.sin(abs(angle)), 16.0 * math.sin(angle), -16.0 * math.cos(angle)])
            point = np.stack([xs, ys, zs], axis=-1) - shoulder
            direction = tip - shoulder
            along = np.clip((point @ direction) / (direction @ direction), 0, 1)
            distance = np.linalg.norm(point - along[..., None] * direction, axis=-1)
            arm = distance <= 2.3
            index[arm & (index == 0)] = 2
            index[arm & (zs < 30)] = 1
        cells = np.argwhere(index > 0)
        voxels = np.concatenate([cells, index[tuple(cells.T)][:, None]], axis=1).astype(np.uint8)
        models.append((size, voxels))
    palette = np.zeros((256, 4), np.uint8)
    palette[:4] = [[240, 200, 170, 255], [200, 40, 50, 255], [40, 60, 160, 255], [30, 30, 30, 255]]
    Path(path).write_bytes(write_vox(models, palette))


def ffmpeg_path():
    return shutil.which("ffmpeg") or "ffmpeg"


def make_person_video(path, seconds):
    """暗い背景の上で、白い人型 (楕円の胴と頭、振る腕と脚の四角) が動く動画 (ffmpeg の lavfi の geq)。人物の範囲の動画にも使う。
    drawbox の式の t は線の太さで時刻ではないので、動く四角も geq の T (秒) で描く。"""
    ellipse = lambda cx, cy, rx, ry: f"lte(pow((X-{cx})/{rx},2)+pow((Y-{cy})/{ry},2),1)"  # noqa: E731
    box = lambda x, y, w, h: f"between(X,{x},({x})+{w})*between(Y,{y},{y}+{h})"  # noqa: E731
    shape = "+".join([
        ellipse(160, 95, 24, 38), ellipse(160, 42, 15, 17),
        box("112+14*sin(T*5)", 62, 14, 48), box("194-14*sin(T*5)", 62, 14, 48),
        box("138+10*sin(T*5)", 128, 16, 46), box("166-10*sin(T*5)", 128, 16, 46),
    ])
    chain = f"format=yuv444p,geq=lum='if({shape},235,lum(X,Y))':cb='cb(X,Y)':cr='cr(X,Y)'"
    command = [ffmpeg_path(), "-v", "error", "-y", "-f", "lavfi", "-i",
               f"color=c=0x182030:size=320x180:rate={FPS}:duration={seconds}", "-vf", chain,
               "-c:v", "libx264", "-pix_fmt", "yuv444p", "-crf", "0", str(path)]
    subprocess.run(command, check=True, capture_output=True)


def make_testsrc_video(path, seconds):
    command = [ffmpeg_path(), "-v", "error", "-y", "-f", "lavfi", "-i",
               f"testsrc2=size=320x180:rate={FPS}:duration={seconds}", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(command, check=True, capture_output=True)


def converter_args(source, output, width, height, mode="screen", depth=16, anamorphic=False):
    """convert.py に渡す引数 (起動コマンドの部分を除く)。GUI と同じ build_converter_args で組み立てる
    (設定は GUI の既定: 自動パレット・ディザなし・キーフレーム 30・CPU)。"""
    from video_player_gui import build_converter_args

    return build_converter_args(Path(source), Path(output), ffmpeg_path(), width, height, float(FPS), "auto", "none", 30, "cpu",
                                True, None, "en", mode, depth, anamorphic)


def run_converter(argv):
    result = subprocess.run([sys.executable, str(ROOT / "convert.py"), *argv], cwd=ROOT, capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise RuntimeError(f"convert.py failed: {(result.stderr or result.stdout)[-1500:]}")


def video_entry(video_id, frames_js, hidden=False):
    from pack_builder import frame_data_dims

    dims = frame_data_dims(frames_js)
    entry = {"id": video_id, "title": video_id, "frame_count": dims.get("frame_count", 0), "width": dims.get("width", 0),
             "height": dims.get("height", 0)}
    if "depth" in dims:
        entry["depth"] = dims["depth"]
    if hidden:
        entry["hidden"] = True
    return entry


def build_fixture(name, frames, folder):
    """素材 name を folder に作る (frames_<id>.js と videos.js)。"""
    folder.mkdir(parents=True, exist_ok=True)
    seconds = frames / FPS
    with tempfile.TemporaryDirectory(prefix="bvp-fixture-") as temp:
        temp = Path(temp)
        entries = []
        if name in ("mmd40", "mmd80", "solid56"):
            vox = temp / f"{name}.vox"
            if name == "solid56":
                make_solid_vox(vox, frames)
            else:
                make_mmd_vox(vox, int(name[3:]), frames)
            output = folder / f"frames_{name}.js"
            run_converter(converter_args(vox, output, 64, 64, anamorphic=True))
            entries.append(video_entry(name, output.read_text(encoding="utf-8")))
        elif name in ("person80", "person80_bg"):
            from mvcodec.stage import stage_geometry

            person = temp / "person.mp4"
            make_person_video(person, seconds)
            output = folder / f"frames_{name}.js"
            argv = converter_args(person, output, 80, 45, mode="inflate", depth=16, anamorphic=True)
            argv += ["--mask-video", str(person)]
            if name == "person80_bg":
                argv += ["--stage-background", f"{name}_bg"]
            run_converter(argv)
            entries.append(video_entry(name, output.read_text(encoding="utf-8")))
            if name == "person80_bg":
                geom = stage_geometry(80, 45, 16)
                source = temp / "background.mp4"
                make_testsrc_video(source, seconds)
                background = folder / f"frames_{name}_bg.js"
                run_converter([*converter_args(source, background, geom["stageWidth"], geom["wallHeight"]), "--fit", "cover"])
                entries.append(video_entry(f"{name}_bg", background.read_text(encoding="utf-8"), hidden=True))
        else:
            raise SystemExit(f"unknown fixture: {name}")
        from pack_builder import videos_js

        (folder / "videos.js").write_text(videos_js(entries), encoding="utf-8")
        return entries


def converter_source_hash(root=ROOT):
    """変換に関わるファイル (convert.py・mvcodec/*.py・pack_builder.py) の内容の sha1。素材の作り置きを使ってよいかの判定に使う。"""
    root = Path(root)
    files = [root / "convert.py", root / "pack_builder.py", *sorted((root / "mvcodec").glob("*.py"))]
    digest = hashlib.sha1()
    for path in files:
        if path.is_file():
            digest.update(path.name.encode("utf-8") + b"\0")
            # 改行コードの違い (CRLF の作業コピー) で変わらないようにする
            digest.update(path.read_bytes().replace(b"\r\n", b"\n") + b"\0")
    return digest.hexdigest()


def ensure_fixture(name, frames, out_dir, source_root=ROOT):
    """素材を作る (同じ作り方・同じフレーム数・同じ変換コードの作り置きがあれば使う)。戻り値はフォルダ。"""
    folder = out_dir / "fixtures" / name
    stamp = {"version": FIXTURE_VERSION, "frames": frames, "fps": FPS, "source": converter_source_hash(source_root)}
    meta = folder / "fixture.json"
    if meta.is_file() and json.loads(meta.read_text(encoding="utf-8")) == stamp and (folder / "videos.js").is_file():
        return folder
    if folder.exists():
        shutil.rmtree(folder)
    print(f"[fixture] building {name} ({frames} frames)", flush=True)
    started = time.perf_counter()
    build_fixture(name, frames, folder)
    meta.write_text(json.dumps(stamp), encoding="utf-8")
    print(f"[fixture] {name} done in {time.perf_counter() - started:.1f} s", flush=True)
    return folder


def extract_addon(path, out_dir):
    """第三者の .mcaddon を out/addons/<名前>/ に展開する (数値だけ残すので、展開先は --out の下だけ)。"""
    path = Path(path)
    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", path.stem).strip("_") or "addon"
    folder = out_dir / "addons" / name
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    with zipfile.ZipFile(path) as archive:
        archive.extractall(folder / "pack")
    scripts = next((p.parent for p in (folder / "pack").rglob("main.js")), None)
    if scripts is None:
        raise SystemExit(f"{path}: BP/scripts/main.js not found")
    return name, scripts


# ---------------------------------------------------------------------------
# 版 (baseline と作業中の木)
# ---------------------------------------------------------------------------

def git(*args):
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, encoding="utf-8", check=True).stdout.strip()


def resolve_tree(ref, out_dir):
    """比べる版を { name, dir, label } にする。ref は git の版、"worktree" (作業中の木)、または main.js のあるフォルダ
    (git worktree など)。git の版は main.js と codec.js だけを git archive で取り出す (コミットしていない変更は含まれない)。"""
    if ref == "worktree":
        sha = git("rev-parse", "--short", "HEAD")
        dirty = bool(git("status", "--porcelain", "--", "main.js", "codec.js"))
        return {"name": "worktree", "dir": ROOT, "label": f"worktree ({sha}{' + uncommitted' if dirty else ''})"}
    folder = Path(ref)
    if folder.is_dir() and (folder / "main.js").is_file() and (folder / "codec.js").is_file():
        return {"name": ref, "dir": folder.resolve(), "label": f"folder {folder.resolve()}"}
    sha = git("rev-parse", "--verify", f"{ref}^{{commit}}")
    dest = out_dir / "trees" / sha[:12]
    if not (dest / "codec.js").is_file():
        import io
        import tarfile

        archive = subprocess.run(["git", "-C", str(ROOT), "archive", "--format=tar", sha, "--", "main.js", "codec.js"],
                                 capture_output=True, check=True).stdout
        dest.mkdir(parents=True, exist_ok=True)
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            tar.extractall(dest, **({"filter": "data"} if hasattr(tarfile, "data_filter") else {}))
    return {"name": ref, "dir": dest, "label": f"{ref} ({sha[:7]})"}


# ---------------------------------------------------------------------------
# 実行
# ---------------------------------------------------------------------------

def node_command():
    node = shutil.which("node")
    if node is None:
        raise SystemExit("node が必要です")
    return [node, "--jitless"]


def run_micro(frames_js, tree_a, tree_b, work_root, reps):
    command = [*node_command(), str(JS_DIR / "stage_micro.mjs"), str(frames_js), str(tree_a["dir"] / "codec.js"),
               str(tree_b["dir"] / "codec.js"), "--reps", str(reps)]
    result = subprocess.run(command, cwd=work_root, capture_output=True, text=True, encoding="utf-8", timeout=3600)
    if result.returncode != 0:
        raise RuntimeError(f"stage_micro failed: {result.stderr[-1500:]}")
    return json.loads(result.stdout)


def prepare_play_dir(work, tree, data_dir, namespace):
    """通し再生の作業フォルダ: 版の main.js・codec.js、素材のデータ、モック、再生のスクリプト。"""
    work.mkdir(parents=True)
    main_text = (tree["dir"] / "main.js").read_text(encoding="utf-8")
    if namespace != "badapple":
        main_text = main_text.replace(EVENT_NAMESPACE_LINE, EVENT_NAMESPACE_LINE.replace("badapple", namespace))
    (work / "main.js").write_text(main_text, encoding="utf-8", newline="\n")
    shutil.copy(tree["dir"] / "codec.js", work / "codec.js")
    for source in data_dir.iterdir():
        if source.name == "videos.js" or source.name.startswith("frames_"):
            shutil.copy(source, work / source.name)
    for module, source in (("server", "server.js"), ("server-ui", "server-ui.js")):
        target = work / "node_modules" / "@minecraft" / module
        target.mkdir(parents=True)
        (target / "package.json").write_text(json.dumps({"name": f"@minecraft/{module}", "type": "module", "main": "index.js"}))
        shutil.copy(MOCK_DIR / source, target / "index.js")
    (work / "package.json").write_text(json.dumps({"type": "module"}))
    shutil.copy(JS_DIR / "stage_play.mjs", work / "stage_play.mjs")


def run_play(tree, data_dir, namespace, path_kind, frames, work_root):
    """通し再生を、毎回新しい一時フォルダで 1 回動かす。"""
    work_root.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="play_", dir=work_root)) / "run"
    try:
        prepare_play_dir(work, tree, data_dir, namespace)
        argument = json.dumps({"path": path_kind, "frames": frames, "ns": namespace})
        result = subprocess.run([*node_command(), "stage_play.mjs", argument], cwd=work, capture_output=True, text=True,
                                encoding="utf-8", timeout=7200)
        if result.returncode != 0:
            raise RuntimeError(f"stage_play failed ({tree['label']}, {path_kind}): {result.stderr[-1500:]}")
        return json.loads(result.stdout)
    finally:
        shutil.rmtree(work.parent, ignore_errors=True)


# ---------------------------------------------------------------------------
# 集計
# ---------------------------------------------------------------------------

def spread(values):
    return {"median": statistics.median(values), "min": min(values), "max": max(values), "n": len(values)}


def delta_pct(base, new):
    return None if not base else (new - base) / base * 100.0


def compare(base_values, new_values):
    base, new = spread(base_values), spread(new_values)
    return {"baseline": base, "worktree": new, "delta_pct": delta_pct(base["median"], new["median"])}


MICRO_METRICS = (
    ("projection_ms", "投影 (人物が変わったとき) ms/回", lambda r: (r["projection"]["a"]["mean"], r["projection"]["b"]["mean"]), True),
    ("redraw_ms", "描き直し (目が 0.3 動いたとき) ms/回", lambda r: (r["redraw"]["a"]["mean"], r["redraw"]["b"]["mean"]), True),
    ("decode_ms", "展開 (decodeFrameInto) ms/フレーム", lambda r: (r["decode"]["a"]["mean"], r["decode"]["b"]["mean"]), True),
)
PLAY_METRICS = (
    ("avg_frame_ms", "1 フレームの平均 ms (stats_load)", lambda r: r["load"]["avgFrameMs"], True),
    ("max_frame_ms", "1 フレームの最大 ms (stats_load)", lambda r: r["load"]["maxFrameMs"], True),
    ("late_frames", "遅れたフレーム", lambda r: r["load"]["lateFrames"], False),
    ("max_late_ticks", "最大の遅れ (tick)", lambda r: r["load"]["maxLateTicks"], False),
    ("tick_mean_ms", "tick の平均 ms", lambda r: r["tickMs"]["mean"], True),
    ("tick_p95_ms", "tick の p95 ms", lambda r: r["tickMs"]["p95"], True),
    ("tick_max_ms", "tick の最大 ms", lambda r: r["tickMs"]["max"], True),
    ("js_ms_per_frame", "JS の時間 ms/フレーム (置く費用を除く)", lambda r: r["jsMs"] / max(1, r["stats"]["frames"]), True),
    # "game": ゲーム内の実測 (COST_US) から作った値なので、モック→ゲーム内の比を掛けない (見込みの列は空)
    ("api_ms_per_frame", "置く費用の見積り ms/フレーム", lambda r: r["modeledApiMs"] / max(1, r["stats"]["frames"]), "game"),
    ("ticks", "tick 数", lambda r: r["ticks"], False),
)


def summarize_micro(runs):
    summary = {}
    for key, _label, getter, _ms in MICRO_METRICS:
        pairs = [getter(run) for run in runs]
        summary[key] = compare([a for a, _ in pairs], [b for _, b in pairs])
    summary["diff_cells"] = {
        "projection": sum(run["projection"]["diffCells"] for run in runs),
        "redraw": sum(run["redraw"]["diffCells"] for run in runs),
        "decode": sum(run["decode"]["diffCells"] for run in runs),
        "drawn_mismatch": sum(run["projection"]["drawnMismatch"] for run in runs),
    }
    return summary


def summarize_play(base_runs, new_runs):
    summary = {key: compare([getter(r) for r in base_runs], [getter(r) for r in new_runs]) for key, _l, getter, _m in PLAY_METRICS}
    base_sha = sorted({r["board"]["sha1"] for r in base_runs})
    new_sha = sorted({r["board"]["sha1"] for r in new_runs})
    summary["sha1_equal"] = base_sha == new_sha and len(base_sha) == 1
    summary["sha1"] = {"baseline": base_sha, "worktree": new_sha}
    return summary


def row_text(entry, is_ms, estimate=True):
    base, new = entry["baseline"], entry["worktree"]
    digits = 2 if is_ms else 1
    cell = lambda s: f"{s['median']:.{digits}f} ({s['min']:.{digits}f}–{s['max']:.{digits}f})"  # noqa: E731
    delta = "" if entry["delta_pct"] is None else f"{entry['delta_pct']:+.1f}%"
    game = f"{new['median'] * GAME_RATIO[0]:.0f}–{new['median'] * GAME_RATIO[1]:.0f}" if is_ms is True and estimate else ""
    return cell(base), cell(new), delta, game


def write_markdown(results, path):
    meta = results["meta"]
    lines = [
        "# だまし絵のベンチマーク",
        "",
        f"- baseline: {meta['baseline']['label']}",
        f"- worktree: {meta['worktree']['label']}",
        f"- 日時: {meta['date']} / node {meta['node']} (--jitless) / 繰り返し {meta['repeats']} 回 (中央値 (最小–最大)) / "
        f"再生 {meta['frames']} フレームまで",
        f"- 置く費用 (µs): {json.dumps(COST_US)}",
        f"- ゲーム内の見込み = worktree の中央値 × {GAME_RATIO[0]}〜{GAME_RATIO[1]} (モックの値に比を掛けた推定で、実測ではない。"
        "モックの時間の列だけ。「置く費用の見積り」はもともとゲーム内の値なので空)",
        "",
    ]
    for name, fixture in results["fixtures"].items():
        info = fixture["info"]
        lines += [f"## {name}", "",
                  f"人物 {info['width']}×{info['height']}×{info['depth']} ({info['layout']})、舞台 {info['stageWidth']}×{info['wallHeight']}"
                  f" ({info['cells']} マス)、{info['frames']} フレーム", ""]
        sections = []
        if "micro" in fixture:
            sections.append(("micro (node --jitless、同じプロセスで交互)", fixture["micro"]["summary"], MICRO_METRICS))
        for kind in ("still", "walk"):
            if kind in fixture:
                sections.append((f"{kind} (通し再生)", fixture[kind]["summary"], PLAY_METRICS))
        for title, summary, metrics in sections:
            lines += [f"### {title}", "", "| 項目 | baseline | worktree | Δ% | ゲーム内の見込み (ms) |", "|---|---|---|---|---|"]
            for key, label, _getter, is_ms in metrics:
                base, new, delta, game = row_text(summary[key], is_ms)
                lines.append(f"| {label} | {base} | {new} | {delta} | {game} |")
            if "diff_cells" in summary:
                diff = summary["diff_cells"]
                lines.append(f"| 盤面の食い違うマス (投影 / 描き直し / 展開) | | | {diff['projection']} / {diff['redraw']} / {diff['decode']} | |")
            if "sha1_equal" in summary:
                lines.append(f"| 最後の盤面の SHA-1 | {', '.join(s[:10] for s in summary['sha1']['baseline'])} | "
                             f"{', '.join(s[:10] for s in summary['sha1']['worktree'])} | {'同じ' if summary['sha1_equal'] else '違う'} | |")
            lines.append("")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", required=True, help="結果と作った素材を置くフォルダ (このリポジトリの中は拒否する)")
    parser.add_argument("--baseline", default="main", help="比べる版: git の版 (既定 main)、worktree、または main.js のあるフォルダ")
    parser.add_argument("--fixtures", nargs="+", default=["all"], help=f"素材 ({', '.join(FIXTURES)}、既定は all。none なら --addon だけ)")
    parser.add_argument("--repeats", type=int, default=5, help="繰り返す回数 (中央値を取る)")
    parser.add_argument("--frames", type=int, default=100, help="素材のフレーム数と、通し再生するフレーム数の上限")
    parser.add_argument("--micro-reps", type=int, default=1, help="micro の中で全フレームを測る回数 (プロセスごと)")
    parser.add_argument("--addon", action="append", default=[], help="ローカルにある第三者の .mcaddon (何度でも指定可)。数値だけ残す")
    parser.add_argument("--only", nargs="+", choices=["micro", "still", "walk"], default=["micro", "still", "walk"],
                        help="測るものを絞る")
    parser.add_argument("--quick", action="store_true", help="確認用: mmd40 を 1 回、20 フレームだけ")
    args = parser.parse_args(argv)
    out = Path(args.out).resolve()
    try:
        out.relative_to(ROOT)
    except ValueError:
        pass
    else:
        parser.error(f"--out はリポジトリの外にしてください: {out}")
    args.out = out
    if args.quick:
        args.fixtures, args.repeats, args.frames, args.addon = ["mmd40"], 1, 20, []
    if args.fixtures == ["all"]:
        args.fixtures = list(FIXTURES)
    elif args.fixtures == ["none"]:
        args.fixtures = []
    unknown = [name for name in args.fixtures if name not in FIXTURES]
    if unknown:
        parser.error(f"unknown fixtures: {unknown}")
    return args


def measure(label, trees, frames_js, data_dir, namespace, args, work_root):
    """1 つの素材 (または第三者のアドオン) を測る。trees は {"baseline": 版, "worktree": 版}。"""
    fixture = {}
    play_runs = {}
    for repeat in range(args.repeats):
        # 繰り返しごとに baseline と worktree の順を入れ替えて、時間の流れによる偏りを消す
        roles = ["baseline", "worktree"] if repeat % 2 == 0 else ["worktree", "baseline"]
        if "micro" in args.only:
            print(f"[{label}] micro {repeat + 1}/{args.repeats}", flush=True)
            fixture.setdefault("micro", {"runs": []})["runs"].append(
                run_micro(frames_js, trees["baseline"], trees["worktree"], work_root, args.micro_reps))
        for kind in ("still", "walk"):
            if kind not in args.only:
                continue
            for role in roles:
                print(f"[{label}] {kind} {role} {repeat + 1}/{args.repeats}", flush=True)
                play_runs.setdefault(kind, {}).setdefault(role, []).append(
                    run_play(trees[role], data_dir, namespace, kind, args.frames, work_root))
    sample = None
    if "micro" in fixture:
        fixture["micro"]["summary"] = summarize_micro(fixture["micro"]["runs"])
        sample = fixture["micro"]["runs"][0]
    for kind, runs in play_runs.items():
        fixture[kind] = {"runs": runs, "summary": summarize_play(runs["baseline"], runs["worktree"])}
        sample = sample or runs["worktree"][0]
    video, geom = sample["video"], sample["geom"]
    frames = sample["frames"]["total"] if "frames" in sample else video["frameCount"]
    fixture["info"] = {"width": video["width"], "height": video["height"], "depth": video["depth"],
                       "layout": video.get("layout", "-"), "stageWidth": geom["stageWidth"], "wallHeight": geom["wallHeight"],
                       "cells": geom["cells"], "frames": frames}
    return fixture


def main(argv=None):
    args = parse_args(argv)
    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    work_root = out / "work"
    work_root.mkdir(exist_ok=True)
    # baseline が worktree のときは同じ版どうしの比較になる (ノイズの大きさを測れる)
    trees = {"baseline": resolve_tree(args.baseline, out), "worktree": resolve_tree("worktree", out)}
    results = {
        "schema": 1,
        "meta": {
            "baseline": {"label": trees["baseline"]["label"]}, "worktree": {"label": trees["worktree"]["label"]},
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "node": subprocess.run(["node", "--version"], capture_output=True, text=True).stdout.strip(),
            "repeats": args.repeats, "frames": args.frames, "micro_reps": args.micro_reps, "cost_us": COST_US,
            "game_ratio": list(GAME_RATIO), "quick": args.quick,
        },
        "fixtures": {},
    }
    jobs = []
    for name in args.fixtures:
        folder = ensure_fixture(name, args.frames, out)
        jobs.append((name, folder / f"frames_{name}.js", folder, "badapple"))
    for path in args.addon:
        name, scripts = extract_addon(path, out)
        listing = json.loads(re.search(r"VIDEO_LIST\s*=\s*(\[.*\]);", (scripts / "videos.js").read_text(encoding="utf-8"), re.S).group(1))
        main_id = next(entry["id"] for entry in listing if not entry.get("hidden"))
        namespace = re.search(r'const EVENT_NAMESPACE = "([^"]+)";', (scripts / "main.js").read_text(encoding="utf-8")).group(1)
        jobs.append((f"addon:{name}", scripts / f"frames_{main_id}.js", scripts, namespace))
    for name, frames_js, data_dir, namespace in jobs:
        results["fixtures"][name] = measure(name, trees, frames_js, data_dir, namespace, args, work_root)
        (out / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    write_markdown(results, out / "results.md")
    shutil.rmtree(work_root, ignore_errors=True)
    print(f"wrote {out / 'results.json'} and results.md", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
