"""同じ動画の立体版と平面版をセットでアドオンに入れるテスト。

- pack_builder: 平面版は audio_from で立体版の音声を鳴らし、音声のファイルは 1 回だけ入れる
- main.js (モックの Minecraft): audio_from の動画の音声の区切りを鳴らす。audio_from の無い古いアドオンは今までどおり
- Windows 版 (video_player_gui): 「平面の動画もセットで入れる」で <ID>_flat の平面版を作る (Tk が要るので CI で動く)
- ブラウザ版: 平面版の動画 ID の末尾が pack_builder と同じ
"""
import io
import json
import queue
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pack_builder  # noqa: E402
from test_main_js import make_scene_frames, run_addon, run_convert  # noqa: E402


def addon_contents(data):
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = z.namelist()
        videos_js = z.read("BP/scripts/videos.js").decode("utf-8")
    entries = json.loads(videos_js[videos_js.index("VIDEO_LIST = ") + 13:videos_js.rindex("]") + 1])
    return names, {e["id"]: e for e in entries}


def test_pack_builder_shares_the_sound(tmp_path):
    make_scene_frames(tmp_path / "frames")
    run_convert(tmp_path / "frames", tmp_path / "a.js")
    frames_js = (tmp_path / "a.js").read_text(encoding="utf-8")
    sounds = [(0, "ogg", b"OggS-0"), (1, "ogg", b"OggS-1")]
    data = pack_builder.build_addon_bytes([
        {"id": "clip", "title": "Clip (3D)", "frames_js": frames_js, "sounds": sounds},
        {"id": "clip_flat", "title": "Clip (2D)", "frames_js": frames_js, "sounds": sounds, "audio_from": "clip"},
    ], "Pair", "badapple")
    names, entries = addon_contents(data)
    assert "BP/scripts/frames_clip.js" in names and "BP/scripts/frames_clip_flat.js" in names
    assert [n for n in names if "/sounds/music/" in n] == [
        "RP/sounds/music/clip/chunk_0.ogg", "RP/sounds/music/clip/chunk_1.ogg"]
    assert entries["clip_flat"]["audio_from"] == "clip" and entries["clip_flat"]["audio_chunks"] == 2
    assert "audio_from" not in entries["clip"]


def test_pack_builder_rejects_a_missing_audio_source(tmp_path):
    make_scene_frames(tmp_path / "frames")
    run_convert(tmp_path / "frames", tmp_path / "a.js")
    with pytest.raises(ValueError):
        pack_builder.build_addon_bytes([{"id": "a", "frames_js": (tmp_path / "a.js").read_text(encoding="utf-8"),
                                         "audio_from": "nothing"}], "Pair", "badapple")


def test_flat_pair_dither():
    assert pack_builder.flat_pair_dither("none") == "blue_noise"
    assert pack_builder.flat_pair_dither("floyd") == "floyd"


@pytest.mark.skipif(shutil.which("node") is None, reason="node が必要")
def test_main_js_plays_the_audio_of_audio_from(tmp_path):
    make_scene_frames(tmp_path / "frames")
    run_convert(tmp_path / "frames", tmp_path / "frames_demo.js", "--keyframe-interval", "10")
    out = run_addon(tmp_path / "addon", tmp_path / "frames_demo.js", audio_from="demo_3d")
    assert out["sounds"] and out["sounds"][0] == "badapple.demo_3d.chunk_0"
    assert all(".demo." not in s for s in out["sounds"])


def test_web_uses_the_same_flat_suffix():
    worker = (ROOT / "web" / "worker.js").read_text(encoding="utf-8")
    suffix = re.search(r'const FLAT_PAIR_SUFFIX = "([^"]+)";', worker).group(1)
    assert suffix == pack_builder.FLAT_PAIR_SUFFIX
    assert "audio_from: backgroundId || job.id" in worker


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg が必要")
def test_windows_app_adds_the_flat_version(tmp_path):
    pytest.importorskip("tkinter")
    import video_player_gui as gui
    from gui_i18n import Translator

    video = tmp_path / "clip.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=96x54:rate=10:duration=3",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", str(video)], check=True)

    class Var:
        def __init__(self, value):
            self.value = value

        def get(self):
            return self.value

    class Headless:
        def __init__(self):
            self.tr = Translator("en")
            self.messages = queue.Queue()
            self.keyframe_interval_var = Var(30)
            self.perceptual_var = Var(True)

        def _run(self, command):
            gui.PackBuilderApp._run(self, command)

        def _extract_audio(self, *args):
            return gui.PackBuilderApp._extract_audio(self, *args)

        def _write_previews(self, *args):
            return gui.PackBuilderApp._write_previews(self, *args)

    app = Headless()
    output = tmp_path / "pair.mcaddon"
    gui.PackBuilderApp._build_pack(app, [{"file_path": str(video), "video_id": "clip", "thumb_sec": 0}], output,
                                   "Pair", "badapple", 32, 18, 10, None, "full", "none", "cpu", "relief", 8,
                                   None, False, None, False, True)
    log = []
    while not app.messages.empty():
        log.append(app.messages.get())
    assert not any(isinstance(m, tuple) and m[0] == "error" for m in log), log
    names, entries = addon_contents(output.read_bytes())
    assert entries["clip"]["title"] == "clip (3D)" and entries["clip_flat"]["title"] == "clip (2D)"
    assert entries["clip_flat"]["audio_from"] == "clip"
    assert entries["clip_flat"]["audio_chunks"] == entries["clip"]["audio_chunks"] > 0
    assert not any("/sounds/music/clip_flat/" in n for n in names)
    with zipfile.ZipFile(output) as z:
        flat = z.read("BP/scripts/frames_clip_flat.js").decode("utf-8")
        relief = z.read("BP/scripts/frames_clip.js").decode("utf-8")
    assert '"layout": "screen"' in flat.replace('":"', '": "') or '"layout":"screen"' in flat
    assert "heightfield" in relief or "volume" in relief
