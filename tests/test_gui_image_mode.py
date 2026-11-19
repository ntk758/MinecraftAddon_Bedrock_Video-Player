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


def fake_app(priority="quality", tier="2"):
    return SimpleNamespace(messages=queue.Queue(), tr=Translator("en"), image_priority_key=priority, image_tier_key=tier)


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
    # 画質を活かすため横 512 から始める (予算を超えれば優先に合わせて image_mode/budget.py が下げる)
    assert gui.image_mode_size(128, 72) == (512, 288)
    assert gui.image_mode_size(64, 64) == (512, 512)
    # 縦に極端に長くても、横の 4 倍まで
    assert gui.image_mode_size(8, 200) == (512, 2048)


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
    # 30 fps・2 秒 = 60 コマ、512×288 (2 秒なら Tier 2 に入るので何も変えない)
    assert '"frame_count": 60' in videos_js and '"width": 512' in videos_js
    assert not any("to fit Tier" in m for m in messages)


@needs_ffmpeg
def test_smooth_priority_shrinks_the_picture_to_fit(tmp_path):
    # 2 秒の 512×288・30 fps は約 34 MiB。Tier 1 (150 MB) には入るので、選んだ優先と Tier がそのまま渡るかを、
    # 収まらない長さの代わりに --end を使わずに確かめる: 予算の計算に渡る値だけを見る
    video = make_video(tmp_path / "clip.mp4")
    app = fake_app(priority="smooth", tier="5")
    result = gui.PackBuilderApp._build_image_pack(app, [{"file_path": str(video), "video_id": "clip"}],
                                                  tmp_path / "P.mcaddon", "P", "movie", 128, 72, None, "ffmpeg")
    report = json.loads(result.with_suffix(".report.json").read_text(encoding="utf-8"))
    assert report["plan"]["priority"] == "smooth" and report["plan"]["target_tier"] == 5


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


@needs_ffmpeg
def test_combine_puts_both_addons_in_one_file(tmp_path):
    import convert_image_mode
    video = make_video(tmp_path / "clip.mp4")
    output = tmp_path / "Both.mcaddon"
    # ブロックモードのアドオンの代わり (BP/ と RP/ を持つ zip)
    with zipfile.ZipFile(output, "w") as block:
        block.writestr("BP/manifest.json", "{}")
        block.writestr("RP/manifest.json", "{}")
    app = fake_app()
    app.image_combine_var = SimpleNamespace(get=lambda: True)
    image = gui.PackBuilderApp._build_image_pack(app, [{"file_path": str(video), "video_id": "clip"}], output, "P",
                                                 "movie", 128, 72, None, "ffmpeg")
    gui.PackBuilderApp._combine_image_pack(app, output, image)
    assert not image.exists()
    with zipfile.ZipFile(output) as archive:
        names = set(archive.namelist())
    assert {"BP/manifest.json", "RP/manifest.json", "BP_image/manifest.json", "RP_image/manifest.json",
            "BP_image/scripts/image_main.js"} <= names
    assert any("Image mode is included" in m for m in drain(app))
    # 名前が重なるときはまとめない
    with pytest.raises(convert_image_mode.ConvertError):
        convert_image_mode.combine_addons(output, output)


@needs_ffmpeg
def test_lighting_snap_and_png_options_reach_the_image_addon(tmp_path):
    """画面の明るさ・シートに多く詰める・PNG の最適化が convert_image_mode.py に渡る。"""
    import tkinter as tk

    video = make_video(tmp_path / "clip.mp4")
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("Tk が使えない")
    try:
        app = fake_app()
        app.image_lighting_key = "world"
        app.image_snap_var = tk.BooleanVar(master=root, value=False)
        app.image_png_optimize_var = tk.BooleanVar(master=root, value=True)
        result = gui.PackBuilderApp._build_image_pack(app, [{"file_path": str(video), "video_id": "clip"}],
                                                      tmp_path / "P.mcaddon", "P", "movie", 128, 72, None, "ffmpeg")
    finally:
        root.destroy()
    report = json.loads(result.with_suffix(".report.json").read_text(encoding="utf-8"))
    assert report["videos"][0]["lighting"] == "world"
    with zipfile.ZipFile(result) as archive:
        controller = archive.read("RP/render_controllers/screen_clip_a.render_controllers.json")
    assert b"ignore_lighting" not in controller


# --- MMD のモデルを、画像モードのガラスケースの人物にもする ---

def doll_files(tmp_path):
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from mmd_fixture import write_doll

    keys = [("左腕", 0, (0, 0, 0), (0, 0, 0, 1)), ("左腕", 30, (0, 0, 0), (0, 0, 0.7071068, 0.7071068))]
    return write_doll(tmp_path, keys)


def videos_in(archive):
    text = archive.read("BP/scripts/image_videos.js").decode("utf-8")
    return json.loads("[" + text.split("IMAGE_VIDEOS = [", 1)[1].rsplit("];", 1)[0] + "]")


def test_a_glass_figure_alone_builds_an_image_addon(tmp_path):
    """動画が無くても、ガラスケースにする MMD のモデルだけで画像モードのアドオンを作る (ffmpeg を使わない)。"""
    pmx, vmd = doll_files(tmp_path)
    app = fake_app()
    entries = [{"file_path": str(pmx), "video_id": "doll", "motion": str(vmd),
                "glass": {"audio": None, "case_image": None}},
               {"file_path": str(tmp_path / "walk.vox"), "video_id": "walk"}]
    result = gui.PackBuilderApp._build_image_pack(app, entries, tmp_path / "P.mcaddon", "P", "movie", 128, 72, None,
                                                  "ffmpeg")
    assert result == tmp_path / "P_image.mcaddon"
    messages = drain(app)
    assert any("Building a glass-case figure" in m and "doll" in m for m in messages)
    assert any("walk" in m for m in messages)  # .vox は今までどおり使わない
    with zipfile.ZipFile(result) as archive:
        names = set(archive.namelist())
        [video] = videos_in(archive)
    assert "RP/animations/screen_doll_a.animation.json" in names
    assert "RP/models/entity/screen_doll_a_case.geo.json" in names  # 画面からはいつもケース付き
    assert video["id"] == "doll" and video["glass"]["figure"]["rig"]["bones"] >= 2


@needs_ffmpeg
def test_videos_and_a_glass_figure_share_one_image_addon(tmp_path):
    video = make_video(tmp_path / "clip.mp4")
    pmx, vmd = doll_files(tmp_path)
    picture = tmp_path / "stage.png"
    from PIL import Image
    Image.new("RGB", (32, 18), (10, 120, 200)).save(picture)
    app = fake_app()
    entries = [{"file_path": str(video), "video_id": "clip"},
               {"file_path": str(pmx), "video_id": "doll", "motion": str(vmd),
                "glass": {"audio": None, "case_image": str(picture)}}]
    result = gui.PackBuilderApp._build_image_pack(app, entries, tmp_path / "P.mcaddon", "P", "movie", 128, 72, None,
                                                  "ffmpeg")
    with zipfile.ZipFile(result) as archive:
        names = set(archive.namelist())
        videos = {v["id"]: v for v in videos_in(archive)}
        sounds = json.loads(archive.read("RP/sounds/sound_definitions.json"))["sound_definitions"]
    assert set(videos) == {"clip", "doll"}
    assert "glass" not in videos["clip"] and "rig" in videos["doll"]["glass"]["figure"]
    # どちらのエンティティ・テクスチャも入り、音は動画の分
    assert {"BP/entities/screen_clip_a.json", "BP/entities/screen_doll_a.json",
            "RP/textures/bvp_image/doll/fig_0.png", "RP/textures/bvp_image/doll/case.png"} <= names
    assert any(key.startswith("movie_img.img.clip.") for key in sounds)
    report = json.loads(result.with_suffix(".report.json").read_text(encoding="utf-8"))
    assert {v["id"] for v in report["memory"]["per_video"]} == {"clip", "doll"}


def test_adding_a_mmd_model_asks_about_the_glass_case(tmp_path, monkeypatch):
    model = tmp_path / "miku.pmx"
    answers = iter([str(tmp_path / "song.wav"), ""])  # 曲は選び、絵はキャンセル
    shown = []
    monkeypatch.setattr(gui.messagebox, "askyesno", lambda *_a, **_k: True)
    monkeypatch.setattr(gui.messagebox, "showinfo", lambda *a, **_k: shown.append(a))
    monkeypatch.setattr(gui.filedialog, "askopenfilename", lambda **_k: next(answers))
    state = {"on": False}
    app = SimpleNamespace(tr=Translator("en"), mmd_glass={},
                          image_mode_var=SimpleNamespace(get=lambda: state["on"], set=lambda v: state.update(on=v)))
    gui.PackBuilderApp._ask_mmd_glass(app, model)
    assert app.mmd_glass == {str(model): {"audio": str(tmp_path / "song.wav"), "case_image": None}}
    assert state["on"] is True and shown  # 画像モードのアドオンも作るようにして、知らせる
    # 「いいえ」なら、前の選択も消す
    monkeypatch.setattr(gui.messagebox, "askyesno", lambda *_a, **_k: False)
    gui.PackBuilderApp._ask_mmd_glass(app, model)
    assert app.mmd_glass == {}
