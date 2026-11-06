"""Windows 版 (video_player_gui.py) の画像（テクスチャ）再生方式の統合。

ffmpeg を実行せず、frame_extract.extract_frames と build_image_addon を差し替えて、
別アドオンの出力先・接頭辞・ログの形を確認する。
"""
import queue
from pathlib import Path

import pytest

import gui_i18n
import video_player_gui
from image_mode.frame_extract import MAX_FPS
from video_player_gui import IMAGE_MAX_FRAMES, image_frame_limit, image_video_specs


@pytest.fixture
def app(monkeypatch, tmp_path):
    tk = pytest.importorskip("tkinter")
    monkeypatch.setattr(gui_i18n, "settings_path", lambda: tmp_path / "settings.json")
    monkeypatch.setattr(video_player_gui, "load_settings", lambda: {})
    monkeypatch.setattr(video_player_gui, "save_settings", lambda settings: None)
    try:
        instance = video_player_gui.PackBuilderApp(language="ja")
    except tk.TclError:
        pytest.skip("ディスプレイが無い環境")
    instance.withdraw()
    yield instance
    instance.destroy()


class ImageBuildApp:
    """PackBuilderApp._build_image_pack が使うものだけを持つ代わり (画面を作らないので安定して実行できる)。"""

    def __init__(self, language: str) -> None:
        self.tr = gui_i18n.Translator(language)
        self.messages = queue.Queue()

    _build_image_pack = video_player_gui.PackBuilderApp._build_image_pack


def logged(app) -> list[str]:
    texts = []
    while True:
        try:
            message = app.messages.get_nowait()
        except queue.Empty:
            break
        texts.append(message if isinstance(message, str) else message[1])
    return texts


def fake_frames(source, width, height, fps, limit, ffmpeg=None, run_kwargs=None):
    """frame_extract.extract_frames の代わり。limit の枚数と ffmpeg の指定を受け取る。"""
    assert ffmpeg == "ffmpeg"
    return {"frames": [b"\x89PNG"] * limit, "width": width, "height": height,
            "fps": min(float(fps), MAX_FPS), "ticks_per_frame": 1}


def entries(tmp_path):
    """動画の一覧。画面が持つ video_tree と同じ形の dict。"""
    return [{"video_id": "clip", "file_path": str(tmp_path / "clip.mp4"), "duration": 2.0},
            {"video_id": "still", "file_path": str(tmp_path / "photo.png"), "duration": None},
            {"video_id": "model", "file_path": str(tmp_path / "model.vox"), "duration": 2.0},
            {"video_id": "mmd", "file_path": str(tmp_path / "dance.pmx"), "duration": 2.0}]


def test_image_video_specs_keeps_only_videos_with_picture():
    """立体 (.vox) と MMD (.pmx) は元の映像のコマが無いので画像方式では使えない。"""
    specs, skipped = image_video_specs(entries(Path("C:/videos")))
    assert [spec["id"] for spec in specs] == ["clip", "still"]
    assert skipped == ["model", "mmd"]
    assert [spec["source"] for spec in specs] == [Path("C:/videos/clip.mp4"), Path("C:/videos/photo.png")]


def test_image_frame_limit_uses_the_duration_and_caps_at_the_limit():
    assert image_frame_limit(None, 10) == IMAGE_MAX_FRAMES
    assert image_frame_limit(0, 10) == IMAGE_MAX_FRAMES
    assert image_frame_limit(2.0, 10) == 21
    assert image_frame_limit(2.0, MAX_FPS) == 41
    assert image_frame_limit(2.0, 30) == 41  # 20 より大きい fps は 20 に clamp した枚数
    assert image_frame_limit(1000.0, 20) == IMAGE_MAX_FRAMES


def test_image_mode_checkbox_starts_off(app):
    """新しい設定は既定で off (既存のブロック方式の作り方を変えない)。"""
    assert app.image_mode_var.get() is False
    app.image_mode_var.set(True)
    assert app.image_mode_var.get() is True


def test_build_image_pack_writes_a_separate_addon_next_to_the_block_one(tmp_path, monkeypatch):
    app = ImageBuildApp("ja")
    seen: dict = {}

    def fake_build(output, videos, namespace, pack_name, description=None):
        seen["output"] = Path(output)
        seen["namespace"] = namespace
        seen["pack_name"] = pack_name
        seen["description"] = description
        seen["videos"] = videos
        return {"paths": ["BP/manifest.json"], "videos": [{"id": v["id"]} for v in videos], "texture_mib": 1.234}

    monkeypatch.setattr(video_player_gui.frame_extract, "extract_frames", fake_frames)
    monkeypatch.setattr(video_player_gui, "build_image_addon", fake_build)

    output = tmp_path / "VideoPlayer.mcaddon"
    result = app._build_image_pack(entries(tmp_path), output, "Block Video Player", "badapple",
                                   64, 36, 10, 2.0, "ffmpeg")

    assert result == tmp_path / "VideoPlayer_images.mcaddon"  # ブロック方式のファイル名は変えない
    assert seen["namespace"] == "badapple_img"  # ブロック方式と同じ接頭辞だとコマンドの名前が衝突する
    assert seen["pack_name"] == "Block Video Player_images"
    assert seen["description"] == app.tr("pack.image_desc")
    assert [video["id"] for video in seen["videos"]] == ["clip", "still"]
    # 画面が指定した「動画の長さ」から決めた枚数を、この回のすべての動画に使う (動画ごとの長さは見ていない)
    assert len(seen["videos"][0]["frames"]) == 21
    assert len(seen["videos"][1]["frames"]) == 21

    texts = logged(app)
    assert any("model" in text for text in texts)  # 使えなかった動画の理由を 1 件ずつ書く
    assert any("mmd" in text for text in texts)
    assert any(str(result) in text for text in texts)
    assert all(isinstance(text, str) for text in texts)  # 失敗を stop_message にしない (ブロック方式の成功はそのまま)


def test_build_image_pack_without_a_duration_uses_the_cap(tmp_path, monkeypatch):
    app = ImageBuildApp("ja")
    seen: dict = {}

    def fake_build(output, videos, namespace, pack_name, description=None):
        seen["frames"] = [len(video["frames"]) for video in videos]
        return {"paths": [], "videos": [{"id": v["id"]} for v in videos], "texture_mib": 0.0}

    monkeypatch.setattr(video_player_gui.frame_extract, "extract_frames", fake_frames)
    monkeypatch.setattr(video_player_gui, "build_image_addon", fake_build)
    app._build_image_pack(entries(tmp_path), tmp_path / "VideoPlayer.mcaddon", "Block Video Player",
                          "badapple", 64, 36, 10, None, "ffmpeg")
    assert seen["frames"] == [IMAGE_MAX_FRAMES, IMAGE_MAX_FRAMES]


def test_build_image_pack_failure_leaves_the_block_addon_alone(tmp_path, monkeypatch):
    app = ImageBuildApp("ja")

    def broken(source, width, height, fps, limit, ffmpeg=None, run_kwargs=None):
        raise RuntimeError("ffmpeg がありません")

    monkeypatch.setattr(video_player_gui.frame_extract, "extract_frames", broken)
    result = app._build_image_pack(entries(tmp_path), tmp_path / "VideoPlayer.mcaddon",
                                   "Block Video Player", "badapple", 64, 36, 10, 2.0, "ffmpeg")
    assert result is None
    texts = logged(app)
    assert "ffmpeg がありません" in "\n".join(texts)


def test_build_image_pack_without_usable_videos_only_logs(tmp_path):
    app = ImageBuildApp("ja")
    result = app._build_image_pack([{"video_id": "model", "file_path": str(tmp_path / "model.vox")}],
                                   tmp_path / "VideoPlayer.mcaddon", "Block Video Player", "badapple",
                                   64, 36, 10, 2.0, "ffmpeg")
    assert result is None
    assert logged(app)  # 理由だけ残す (例外は画面に出さない)
