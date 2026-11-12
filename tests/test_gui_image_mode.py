"""Windows 版の「画像モードのアドオンも作る」(video_player_gui.PackBuilderApp._build_image_pack) のテスト。

画面は作らず、メッセージの行き先と翻訳だけを持つ代わりのオブジェクトで呼ぶ。動画は ffmpeg の合成映像。
"""
from __future__ import annotations

import json
import queue
import shutil
import subprocess
import zipfile
from types import SimpleNamespace

import pytest

import video_player_gui as gui
from gui_i18n import Translator

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg が必要")


def fake_app():
    return SimpleNamespace(messages=queue.Queue(), tr=Translator("en"))


def drain(app):
    out = []
    while not app.messages.empty():
        out.append(app.messages.get_nowait())
    return out


def make_video(path, duration=2):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size=320x180:rate=30:duration={duration}",
                    "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}", "-shortest",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)], check=True)
    return path


def test_image_mode_size_follows_the_screen_aspect_ratio():
    assert gui.image_mode_size(128, 72) == (256, 144)
    assert gui.image_mode_size(64, 64) == (256, 256)
    # 縦に極端に長くても、横の 4 倍まで
    assert gui.image_mode_size(8, 200) == (256, 1024)


@needs_ffmpeg
def test_builds_a_separate_image_addon_next_to_the_block_addon(tmp_path):
    video = make_video(tmp_path / "clip.mp4")
    output = tmp_path / "My_Pack.mcaddon"
    app = fake_app()
    entries = [{"file_path": str(video), "video_id": "clip"},
               {"file_path": str(tmp_path / "walk.vox"), "video_id": "walk"}]
    result = gui.PackBuilderApp._build_image_pack(app, entries, output, "My Pack", "movie", 128, 72, None, "ffmpeg")
    assert result == tmp_path / "My_Pack_image.mcaddon"
    messages = drain(app)
    assert any("walk" in m for m in messages)  # .vox は使わない
    assert any("/movie_img:setup" in m for m in messages)
    with zipfile.ZipFile(result) as archive:
        names = archive.namelist()
        manifest = json.loads(archive.read("BP/manifest.json"))
        videos_js = archive.read("BP/scripts/image_videos.js").decode("utf-8")
    assert "BP/scripts/image_main.js" in names
    assert any(name.startswith("RP/sounds/bvp_image/clip/") for name in names)
    assert manifest["header"]["name"].startswith("My Pack (Image)")
    assert "movie_img" in videos_js
    # 30 fps・2 秒 = 60 コマ、256×144
    assert '"frame_count": 60' in videos_js and '"width": 256' in videos_js


@needs_ffmpeg
def test_a_failure_is_logged_and_does_not_raise(tmp_path):
    app = fake_app()
    entries = [{"file_path": str(tmp_path / "missing.mp4"), "video_id": "clip"}]
    result = gui.PackBuilderApp._build_image_pack(app, entries, tmp_path / "out.mcaddon", "P", "movie", 128, 72, None,
                                                  "ffmpeg")
    assert result is None
    assert any("Could not build the image-mode add-on" in m for m in drain(app))


def test_only_vox_and_mmd_means_nothing_to_build(tmp_path):
    app = fake_app()
    entries = [{"file_path": str(tmp_path / "a.vox"), "video_id": "a"}, {"file_path": str(tmp_path / "b.pmx"), "video_id": "b"}]
    assert gui.PackBuilderApp._build_image_pack(app, entries, tmp_path / "out.mcaddon", "P", "movie", 128, 72, None,
                                                "ffmpeg") is None
    assert any("No videos can be used in image mode" in m for m in drain(app))
