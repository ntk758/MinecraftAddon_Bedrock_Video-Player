"""描画方式 (auto / rect / list / block)・tickingarea の分割・ゲーム内ベンチマークのテスト (Node.js が必要)。"""
import json
import re
import shutil
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import convert  # noqa: E402
from mvcodec.decode import load_frame_data  # noqa: E402
from test_codec_v4 import make_scene_frames, run_convert  # noqa: E402
from test_main_js import lang_keys, run_addon  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node が必要")


@pytest.fixture(scope="module")
def frames_js(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("render")
    make_scene_frames(tmp / "frames")
    run_convert(tmp / "frames", tmp / "frames_demo.js", "--keyframe-interval", "10")
    return tmp / "frames_demo.js"


@pytest.fixture(scope="module")
def runs(frames_js, tmp_path_factory):
    tmp = tmp_path_factory.mktemp("render_runs")
    return {mode: run_addon(tmp / mode, frames_js, f"render={mode}") for mode in ("auto", "rect", "list", "block")}


def test_all_render_modes_draw_the_same_screen(runs):
    assert runs["rect"]["finalBoard"] == runs["block"]["finalBoard"] == runs["list"]["finalBoard"] == runs["auto"]["finalBoard"]
    assert all(r["finished"] for r in runs.values())
    assert runs["auto"]["stats"]["blocksPlaced"] == runs["rect"]["stats"]["blocksPlaced"]


def test_render_modes_call_counts(runs):
    block = runs["block"]["stats"]
    rect = runs["rect"]["stats"]
    listed = runs["list"]["stats"]
    assert block["fillCalls"] == 0 and block["setBlockCalls"] > 0
    rect_calls = rect["fillCalls"] + rect["setBlockCalls"]
    list_calls = listed["fillCalls"] + listed["setBlockCalls"]
    assert rect_calls < block["setBlockCalls"]
    assert list_calls < rect_calls  # 色ごとにまとめるので一番少ない
    assert listed["setBlockCalls"] == 0


def test_large_screen_splits_ticking_areas(tmp_path):
    """256x112 (17x8 = 136 チャンク) は 1 区画 (100 チャンクまで) に収まらないので分割して登録する。"""
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()
    for t in range(3):
        frame = np.zeros((112, 256, 3), np.uint8)
        frame[:, t * 10:t * 10 + 40] = 255
        Image.fromarray(frame).save(frames_dir / f"f{t}.png")
    out = tmp_path / "frames_demo.js"
    assert convert.main(["--frames-dir", str(frames_dir), "--output", str(out), "--width", "256", "--height", "112",
                         "--fps", "10", "--device", "cpu", "--no-adaptive-fps"]) == 0
    result = run_addon(tmp_path / "addon", out)
    adds = [c for c in result["commands"] if c.startswith("tickingarea add")]
    assert len(adds) == 2
    assert result["finished"]
    assert not any("tickingarea_partial" in json.dumps(m) or "tickingarea_failed" in json.dumps(m)
                   for m in result["messages"])


def test_benchmark_command_reports_every_method(frames_js, tmp_path):
    result = run_addon(tmp_path / "addon", frames_js, "bench")
    text = json.dumps(result["benchMessages"])
    assert "bvp.bench_start" in text and "bvp.bench_done" in text
    reported = re.findall(r'"translate": "bvp.bench_result", "with": \{"rawtext": \[\{"text": "([^"]+)"', text)
    assert reported == ["setBlockPermutation", "fillBlocks(row)", "fillBlocks(area)", "fillBlocks(list)",
                        "fillBlocks(list rows)", "structure.place", "runCommand(fill)"]
    keys = set(re.findall(r'"translate": "(bvp\.[a-z_.]+)"', text))
    assert keys <= lang_keys()
    # モックはほとんど時間がかからないので、短すぎる計測は保存しない
    assert "bvp.bench_unreliable" in text and result["costModel"] is None


# 実機をまねた費用 (マイクロ秒)。1 回の呼び出しが重く、1 ブロックは軽い fillBlocks と、軽い setBlockPermutation
EMULATED = {"setBlock": 20, "fillCall": 120, "fillBlock": 4, "listCall": 100, "listBlock": 12}


def test_benchmark_estimates_and_saves_the_cost_model(frames_js, tmp_path):
    """bench は 1 回の費用と 1 ブロックの費用を分けて求め、ワールドに保存する (auto 方式が使う)。"""
    result = run_addon(tmp_path / "addon", frames_js, "bench", "costs=" + json.dumps(EMULATED))
    assert "bvp.bench_model" in json.dumps(result["benchMessages"])
    model = json.loads(result["costModel"])
    for key, expected in EMULATED.items():
        # 待ち時間は下限なので少し大きめに出る。Date.now の誤差も含めて 0.7〜1.8 倍に収まれば良い
        assert 0.7 * expected <= model[key] <= 1.8 * expected, (key, model[key], expected)


def test_auto_mode_picks_lists_for_scattered_changes_and_boxes_for_solids(runs, tmp_path):
    """auto は、散らばった細かい変化では座標リストを、まとまった変化では箱を選ぶ (既定の費用のモデル)。"""
    from test_volume3d import convert_vox, write_cube_vox

    # 散らばった点が毎フレーム入れ替わる映像: 1 個ずつのマスばかりなので、色ごとの座標リストが一番速い
    frames_dir = tmp_path / "dots"
    frames_dir.mkdir()
    rng = np.random.default_rng(5)
    colors = np.array([[255, 255, 255], [20, 20, 20], [200, 30, 30], [30, 30, 200]], np.uint8)
    for t in range(8):
        frame = np.zeros((48, 64, 3), np.uint8) + 128
        ys, xs = rng.integers(0, 48, 300), rng.integers(0, 64, 300)
        frame[ys, xs] = colors[rng.integers(0, 4, 300)]
        Image.fromarray(frame).save(frames_dir / f"f{t}.png")
    dots_js = tmp_path / "dots.js"
    assert convert.main(["--frames-dir", str(frames_dir), "--output", str(dots_js), "--width", "64", "--height", "48",
                         "--fps", "10", "--device", "cpu", "--no-adaptive-fps", "--no-motion", "--tile-size", "0"]) == 0
    dots = run_addon(tmp_path / "dots_addon", dots_js, "render=auto")["stats"]
    assert dots["listFillCalls"] > 0 and dots["setBlockCalls"] == 0

    # 動く小さな四角 (変化は縦 4 マスの列 2 本) では、箱 2 個の方が座標リストより速い
    scene = runs["auto"]["stats"]
    assert scene["fillCalls"] > scene["listFillCalls"]

    # 詰まった立体は 1 個の箱 (fillBlocks 1 回) で置く
    write_cube_vox(tmp_path / "cube.vox", size=8)
    convert_vox(tmp_path / "cube.vox", tmp_path / "cube.js")
    cube = run_addon(tmp_path / "cube", tmp_path / "cube.js", "render=auto")["stats"]
    assert cube["fillCalls"] == 1 and cube["listFillCalls"] == 0 and cube["blocksPlaced"] == 8 ** 3


def test_stats_command_reports_playback_measurements(tmp_path):
    """/scriptevent <接頭辞>:stats で、表示したフレーム数・遅れ・スクリプト時間を表示する。"""
    from mvcodec.decode import load_frame_data

    # 20 fps (1 tick に 1 フレーム) で、1 ブロック 100 µs の重い環境をまねると、全マスを置く最初のフレームが
    # 1 tick に収まらず、後のフレームが予定より遅れて始まる
    make_scene_frames(tmp_path / "frames")
    frames_js = tmp_path / "fast.js"
    run_convert(tmp_path / "frames", frames_js, "--width", "64", "--height", "48", "--fps", "20")
    result = run_addon(tmp_path / "addon", frames_js, "stats", "render=block", 'costs={"setBlock": 100}')
    text = json.dumps(result["statsMessages"])
    assert "bvp.stats_play" in text and "bvp.stats_load" in text
    keys = set(re.findall(r'"translate": "(bvp\.[a-z_.]+)"', text))
    assert keys <= lang_keys()
    play_args = re.search(r'"bvp.stats_play", "with": \{"rawtext": (\[[^\]]*\])', text)
    load_args = re.search(r'"bvp.stats_load", "with": \{"rawtext": (\[[^\]]*\])', text)
    frames, _seconds, _fps, target, _tps, _drift = [item["text"] for item in json.loads(play_args.group(1))]
    late, max_late, avg_ms, max_ms = [float(item["text"]) for item in json.loads(load_args.group(1))]
    assert int(frames) == load_frame_data(frames_js)["frame_count"]
    assert float(target) == load_frame_data(frames_js)["fps"]
    assert late > 0 and max_late >= 1 and max_ms >= avg_ms > 0


def test_stats_command_before_playback_explains_how_to_use(tmp_path):
    from test_playback_control import drive

    frames = tmp_path / "frames"
    make_scene_frames(frames)
    run_convert(frames, tmp_path / "scene.js")
    out = drive(tmp_path / "addon", {"demo": tmp_path / "scene.js"}, [{"event": "badapple:stats"}])
    assert "bvp.stats_none" in json.dumps(out)


def test_stats_clock_stops_when_playback_ends(tmp_path):
    """再生が終わった後で時間をおいて stats を見ても、再生時間・fps は変わらない。"""
    from test_playback_control import drive

    frames = tmp_path / "frames"
    make_scene_frames(frames)
    run_convert(frames, tmp_path / "scene.js")
    out = drive(tmp_path / "addon", {"demo": tmp_path / "scene.js"},
                [{"event": "badapple:setup"}, {"event": "badapple:start"}, {"until": "bvp.playback_finished"},
                 {"event": "badapple:stats"}, {"sleepMs": 1500}, {"tick": 40}, {"event": "badapple:stats"}])
    messages = [json.loads(m) if isinstance(m, str) else m for m in out["messages"]]
    reports = [[arg["text"] for arg in part["with"]["rawtext"]]
               for m in messages for part in m["rawtext"] if part.get("translate") == "bvp.stats_play"]
    assert len(reports) == 2
    assert reports[0] == reports[1]


def test_frames_with_many_colors_are_not_held_back_by_yields(tmp_path):
    """色の多いフレーム (自動パレット + Blue Noise) を座標リストで置いても、色の数だけ区切って何 tick もかからない。
    実機の録画 (128×128・10 fps) で映像が約 3.4 倍遅れ、音声が途切れていた不具合の再発防止。"""
    make_scene_frames(tmp_path / "frames")
    out = tmp_path / "colors.js"
    run_convert(tmp_path / "frames", out, "--width", "64", "--height", "48", "--fps", "20", "--palette", "auto",
                "--dither-method", "blue_noise", "--keyframe-interval", "0")
    costs = '{"setBlock": 2.3, "fillCall": 15, "fillBlock": 0.55, "listCall": 15, "listBlock": 1.42}'
    result = run_addon(tmp_path / "addon", out, "stats", "render=list", f"costs={costs}")
    load = next(part for m in result["statsMessages"] for part in m["rawtext"] if part.get("translate") == "bvp.stats_load")
    late_frames = int(load["with"]["rawtext"][0]["text"])
    # 最初のフレーム (全マスを置く) 以外は 1 tick に収まる
    assert late_frames <= 2, late_frames
