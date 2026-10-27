"""アドオンの組み立て (pack_builder.py、GUI とブラウザ版で共通) と、ブラウザ版のサイトの組み立てのテスト。"""
import io
import json
import struct
import sys
import zipfile
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import convert  # noqa: E402
from pack_builder import AUDIO_CHUNK_SECONDS, build_addon, build_addon_bytes  # noqa: E402


def wav(seconds, rate=22050):
    """ブラウザ版と同じ形式の WAV (16bit モノラル)。"""
    samples = (np.sin(np.arange(int(seconds * rate)) * 0.1) * 8000).astype("<i2").tobytes()
    header = b"RIFF" + struct.pack("<I", 36 + len(samples)) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
    return header + b"data" + struct.pack("<I", len(samples)) + samples


@pytest.fixture(scope="module")
def frames_js(tmp_path_factory):
    from test_codec_v4 import make_scene_frames

    tmp = tmp_path_factory.mktemp("pack")
    make_scene_frames(tmp / "frames")
    out = tmp / "frames.js"
    # ブラウザ版と同じく、PPM のコマを 1 スレッドで変換する
    for path in sorted((tmp / "frames").glob("*.png")):
        from PIL import Image

        Image.open(path).convert("RGB").save(path.with_suffix(".ppm"))
        path.unlink()
    assert convert.main(["--frames-dir", str(tmp / "frames"), "--output", str(out), "--width", "24", "--height", "16",
                         "--fps", "10", "--device", "cpu", "--threads", "1", "--palette", "auto"]) == 0
    return out.read_text(encoding="utf-8")


def test_build_addon_with_wav_audio(frames_js, tmp_path):
    videos = [{"id": "clip", "title": "Clip", "frames_js": frames_js, "thumbnail": b"\x89PNG fake",
               "sounds": [(1, "wav", wav(3)), (0, "wav", wav(AUDIO_CHUNK_SECONDS))]}]
    data = build_addon_bytes(videos, "My Pack", "movie")
    archive = zipfile.ZipFile(io.BytesIO(data))
    names = set(archive.namelist())
    assert {"BP/manifest.json", "RP/manifest.json", "BP/scripts/main.js", "BP/scripts/codec.js", "BP/scripts/videos.js",
            "BP/scripts/frames_clip.js", "RP/sounds/music/clip/chunk_0.wav", "RP/sounds/music/clip/chunk_1.wav",
            "RP/pack_icon.png", "RP/sounds/sound_definitions.json"} <= names
    sounds = json.loads(archive.read("RP/sounds/sound_definitions.json"))["sound_definitions"]
    # パスは拡張子なし (ゲームが .ogg / .wav を探す)
    assert sounds["movie.clip.chunk_1"]["sounds"][0]["name"] == "sounds/music/clip/chunk_1"
    # メモリの少ない端末 (スマートフォンなど) でも読み込ませる
    assert sounds["movie.clip.chunk_1"]["sounds"][0]["load_on_low_memory"] is True
    main_js = archive.read("BP/scripts/main.js").decode("utf-8")
    assert 'const EVENT_NAMESPACE = "movie";' in main_js
    bp = json.loads(archive.read("BP/manifest.json"))
    rp = json.loads(archive.read("RP/manifest.json"))
    assert bp["dependencies"][-1]["uuid"] == rp["header"]["uuid"] and rp["dependencies"][0]["uuid"] == bp["header"]["uuid"]
    assert '"frame_count":' in archive.read("BP/scripts/videos.js").decode("utf-8")
    # ファイルに書いても同じ中身
    out = tmp_path / "pack.mcaddon"
    entries = build_addon(out, videos, "My Pack", "movie")
    assert entries[0]["id"] == "clip" and entries[0]["frame_count"] > 0
    # 音声の区切りの数 (0 ならゲーム内で「音声が入っていない」と知らせる)
    assert entries[0]["audio_chunks"] == 2
    assert build_addon(tmp_path / "silent.mcaddon", [{**videos[0], "sounds": []}], "My Pack", "movie")[0]["audio_chunks"] == 0
    assert set(zipfile.ZipFile(out).namelist()) == names


def test_build_web_site(tmp_path):
    sys.path.insert(0, str(ROOT / "scripts"))
    from build_web import APP_FILES, build

    out = build(tmp_path / "site")
    for name in ("index.html", "app.js", "worker.js", "style.css", "files.json"):
        assert (out / name).is_file(), name
    files = json.loads((out / "files.json").read_text(encoding="utf-8"))
    assert files == APP_FILES
    for required in ("convert.py", "pack_builder.py", "mvcodec/entropy.py", "locales/ja.json", "main.js", "manifest.json"):
        assert required in files and (out / "app" / required).is_file(), required


def test_vox_animation_addon(tmp_path):
    """ブラウザ版と同じ手順で、MagicaVoxel の立体アニメからサムネイルとアドオンを作る。"""
    from PIL import Image

    from mvcodec.decode import iter_decoded_frames, load_frame_data
    from mvcodec.volume import write_vox
    from pack_builder import vox_thumbnail_png

    palette = np.zeros((256, 4), np.uint8)
    palette[0] = (230, 40, 40, 255)
    palette[1] = (240, 240, 240, 255)
    models = []
    for t in range(5):
        cube = [[x, y, z, 1 if z < 2 else 2] for x in range(t, t + 3) for y in range(3) for z in range(4)]
        models.append(((8, 3, 4), np.array(cube, np.uint8)))
    vox_path = tmp_path / "walk.vox"
    vox_path.write_bytes(write_vox(models, palette))

    thumbnail = vox_thumbnail_png(vox_path)
    image = Image.open(io.BytesIO(thumbnail))
    assert image.size == (256, 256)
    # 赤と白の立体が描かれている (背景の黒だけではない)
    assert np.asarray(image.convert("RGB")).max(axis=(0, 1)).min() > 150

    out = tmp_path / "frames_walk.js"
    assert convert.main(["--input-vox", str(vox_path), "--mode", "vox", "--output", str(out), "--fps", "10",
                         "--palette", "auto", "--device", "cpu", "--threads", "1"]) == 0
    data = load_frame_data(out)
    assert (data["width"], data["height"], data["depth"]) == (8, 4, 3)
    assert sum(1 for _ in iter_decoded_frames(data)) == 5
    addon = build_addon_bytes([{"id": "walk", "title": "Walk", "frames_js": out.read_text(encoding="utf-8"),
                                "thumbnail": thumbnail, "sounds": []}], "Vox", "vox")
    archive = zipfile.ZipFile(io.BytesIO(addon))
    assert archive.read("RP/pack_icon.png") == thumbnail
    assert '"depth":3' in archive.read("BP/scripts/videos.js").decode("utf-8").replace(" ", "")
