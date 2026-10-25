"""はじめてガイド (gui_wizard.py) の、画面を持たない計算: 環境の確認・アドオンの大きさの見込み・勧める設定・案内。"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import gui_wizard as wizard  # noqa: E402


def test_size_estimate_matches_the_bundled_sample():
    """同梱のサンプル (15 秒、128 × 72、10 fps、音声付き) は 501 KB。見込みの「ふつう」が近く、範囲に入る。"""
    low, typical, high = wizard.estimate_addon_size(15, 128, 72, 10)
    assert low < 501_000 < high
    assert abs(typical - 501_000) / 501_000 < 0.2
    # 立体 (2.5D) は色と高さの 2 枚分、音声なしなら音声の分だけ小さい
    assert wizard.estimate_addon_size(15, 128, 72, 10, volume=True)[1] > typical
    assert wizard.estimate_addon_size(15, 128, 72, 10, audio=False)[1] < typical - 300_000


def test_recommended_settings_follow_the_aspect_ratio_and_length():
    import video_player_gui as gui

    assert wizard.recommend_settings(1920, 1080, 60, gui.suggest_size) == {"width": 128, "height": 72, "fps": 10}
    # 縦の動画も長い辺 128
    assert wizard.recommend_settings(1080, 1920, 60, gui.suggest_size) == {"width": 72, "height": 128, "fps": 10}
    # 5 分を超える動画は 5 fps (データを小さく)
    assert wizard.recommend_settings(1920, 1080, 600, gui.suggest_size)["fps"] == 5


def test_play_commands_use_the_namespace_and_video_ids():
    commands = wizard.play_commands("mytv", ["intro", "song"])
    assert commands == ["/scriptevent mytv:setup", "/scriptevent mytv:play intro", "/scriptevent mytv:play song",
                        "/scriptevent mytv:stop"]


def test_format_size():
    assert wizard.format_size(500) == "1 KB"
    assert wizard.format_size(501_000) == "489 KB"
    assert wizard.format_size(12 * (1 << 20)) == "12.0 MB"
    assert wizard.format_size(3 * (1 << 30)) == "3.0 GB"


def test_environment_checks_report_every_item(tmp_path):
    checks = wizard.environment_checks(None, tmp_path / "out" / "a.mcaddon")
    keys = [check["key"] for check in checks]
    assert keys == ["ffmpeg", "gpu", "memory", "ai_photo", "ai_anime", "disk", "minecraft"]
    assert all(check["status"] in ("ok", "warn", "error") for check in checks)
    # FFmpeg が無いのはエラー (動画を読めない)
    assert checks[0]["status"] == "error"
    # 保存先がまだ無いフォルダでも、ある所まで戻って空きを調べる
    disk = checks[keys.index("disk")]
    assert "GB free" in disk["detail"]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg が必要")
def test_environment_checks_find_ffmpeg_and_probe_duration(tmp_path):
    ffmpeg = shutil.which("ffmpeg")
    checks = wizard.environment_checks(ffmpeg, tmp_path / "a.mcaddon")
    assert checks[0]["status"] == "ok" and "ffmpeg" in checks[0]["detail"].lower()
    video = tmp_path / "clip.mp4"
    subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=red:size=64x36:rate=10:duration=2.5",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video)], check=True)
    assert wizard.probe_video_duration(video, ffmpeg) == pytest.approx(2.5, abs=0.15)
    assert wizard.probe_video_duration(tmp_path / "missing.mp4", ffmpeg) is None


def test_every_wizard_text_exists_in_every_language():
    keys = ["wizard.open", "wizard.recommended", "after.guide", "after.import", "after.open_folder",
            "build.memory_check", "profile.full", "profile.photo"]
    keys += [f"wizard.check.{key}" for key in ("ffmpeg", "gpu", "memory", "ai_photo", "ai_anime", "disk", "minecraft")]
    keys += [f"wizard.fix.{key}" for key in ("ffmpeg", "gpu", "memory", "ai_photo", "ai_anime", "disk", "minecraft")]
    for path in (ROOT / "locales").glob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        missing = [key for key in keys if key not in data]
        assert not missing, (path.name, missing)
        # 案内の文はコマンドと保存先を差し込める
        assert "{commands}" in data["after.guide"] and "{output}" in data["after.guide"], path.name
