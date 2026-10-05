"""描画方式 (rect / list / block)・tickingarea の分割・ゲーム内ベンチマークのテスト (Node.js が必要)。"""
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
    return {mode: run_addon(tmp / mode, frames_js, f"render={mode}") for mode in ("rect", "list", "block")}


def test_all_render_modes_draw_the_same_screen(runs):
    assert runs["rect"]["finalBoard"] == runs["block"]["finalBoard"] == runs["list"]["finalBoard"]
    assert all(r["finished"] for r in runs.values())


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
                        "structure.place", "runCommand(fill)"]
    keys = set(re.findall(r'"translate": "(bvp\.[a-z_.]+)"', text))
    assert keys <= lang_keys()
