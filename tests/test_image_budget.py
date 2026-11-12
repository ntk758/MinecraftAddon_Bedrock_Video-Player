"""画像モードの予算の計画 (image_mode/budget.py): 何を優先するかで、大きさ・fps・長さのどれを変えるか。"""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from image_mode import atlas, budget  # noqa: E402

MIB = 1024 * 1024


def test_estimate_matches_the_real_atlas():
    # 実際に並べたシートの大きさと同じ数え方 (1 シートに収まるときは小さいシート、2 枚以上は最大の格子)
    import numpy as np
    for count in (5, 128, 300):
        frames = [np.zeros((36, 64, 3), dtype=np.uint8)] * count
        sheets, layout = atlas.pack_sheets(frames, 64, 36)
        real = atlas.memory_report(layout)["bytes"]
        assert budget.video_bytes(count / 10, 64, 36, 10) == real


def test_nothing_changes_when_it_already_fits():
    result = budget.plan([10], 256, 144, 30, "quality")
    assert result["changed"] == [] and (result["width"], result["fps"]) == (256, 30) and result["fits"]


@pytest.mark.parametrize("priority, kept", [("quality", "size"), ("smooth", "fps"), ("length", "length")])
def test_each_priority_keeps_its_own_thing(priority, kept):
    result = budget.plan([60], 512, 288, 30, priority, target_tier=2)
    assert result["fits"] and result["bytes"] <= 225 * MIB
    assert kept not in result["changed"]
    if priority == "quality":
        assert (result["width"], result["height"]) == (512, 288) and result["fps"] == 10
    if priority == "smooth":
        assert result["fps"] == 30 and result["width"] < 512
    if priority == "length":
        assert result["seconds"] == [60.0]


def test_length_is_cut_only_as_a_last_resort():
    # 合わせて 90 分は 64 幅・10 fps でも Tier 1 に入らない → 後ろを切る (動画ごとに同じ割合)
    result = budget.plan([3600, 1800], 256, 144, 30, "length", target_tier=1)
    assert "length" in result["changed"] and result["fits"]
    first, second = result["seconds"]
    assert first < 3600 and abs(first / 3600 - second / 1800) < 0.01


def test_pc_tier_allows_more():
    phone = budget.plan([60], 512, 288, 30, "smooth", target_tier=2)
    pc = budget.plan([60], 512, 288, 30, "smooth", target_tier=5)
    assert pc["width"] > phone["width"]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg が必要")
def test_cli_priority_lowers_fps_to_fit(tmp_path):
    import convert_image_mode
    video = tmp_path / "clip.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30:duration=4",
                    "-pix_fmt", "yuv420p", str(video)], check=True)
    assert atlas.probe_duration(video) == pytest.approx(4, abs=0.1)
    output = tmp_path / "out.mcaddon"
    # Tier 1 (150 MB) に 4 秒の 1024x576 は 30 fps では入らない (約 270 MiB) → 画質優先で fps を下げる
    code = convert_image_mode.main([str(video), "-o", str(output), "--width", "1024", "--height", "576", "--fps", "30",
                                    "--priority", "quality", "--target-tier", "1", "--max-sheet", "4096"])
    assert code == 0
    import json
    report = json.loads(output.with_suffix(".report.json").read_text(encoding="utf-8"))
    assert report["plan"]["changed"] == ["fps"] and report["plan"]["fps"] < 30
    assert report["memory"]["bytes"] <= 150 * MIB
