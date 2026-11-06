"""画像（テクスチャ）再生方式のアドオン (image_mode/build_image_addon.py) の構造のテスト。

ゲームでの動作はここでは確かめない (実機での確認は docs/IMAGE_MODE.md の手順)。
このテストは複数動画を入れた .mcaddon の中身の形 (JSON の整合) を確かめる。
"""
import io
import json
import shutil
import struct
import sys
import zipfile
import zlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "image_mode"))

import build_image_addon  # noqa: E402


def png(width, height):
    """PNG を自分で作る (画像のライブラリを使わない)。"""
    raw = b"".join(b"\x00" + bytes([9, 120, 240]) * width for _ in range(height))
    chunks = [("IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)),
              ("IDAT", zlib.compress(raw)), ("IEND", b"")]
    out = b"\x89PNG\r\n\x1a\n"
    for kind, body in chunks:
        out += struct.pack(">I", len(body)) + kind.encode("ascii") + body + struct.pack(">I", zlib.crc32(kind.encode("ascii") + body))
    return out


def video(video_id, title, frames, width, height, fps, **extra):
    entry = {"id": video_id, "title": title, "frames": [png(width, height) for _ in range(frames)],
             "width": width, "height": height, "fps": fps}
    entry.update(extra)
    return entry


@pytest.fixture(scope="module")
def built():
    videos = [video("badapple", "Bad Apple", 8, 256, 144, 20.0),
              video("intro", "Intro", 5, 256, 144, 6.0),
              video("bg_flat", "Background", 4, 256, 144, 20.0, hidden=True)]
    buffer = io.BytesIO()
    info = build_image_addon.build_image_addon(buffer, videos, namespace="bvpimg", pack_name="Images Test", screen_blocks=4.0)
    archive = zipfile.ZipFile(io.BytesIO(buffer.getvalue()))
    return info, archive


def read_json(archive, path):
    return json.loads(archive.read(path).decode("utf-8"))


def test_two_videos_get_their_own_files(built):
    _, archive = built
    names = set(archive.namelist())
    for video_id in ("badapple", "intro", "bg_flat"):
        assert f"BP/entities/{video_id}.json" in names
        assert f"RP/entity/{video_id}.json" in names
        assert f"RP/render_controllers/{video_id}.json" in names
        assert f"RP/models/{video_id}.geo.json" in names
    # テクスチャは動画ごとに別のフォルダに置く (同じ名前のフレームが混ざらない)
    assert "RP/textures/bvpimg/badapple/frame_0.png" in names
    assert "RP/textures/bvpimg/intro/frame_0.png" in names
    assert "RP/textures/bvpimg/badapple/frame_8.png" not in names
    assert "RP/textures/bvpimg/intro/frame_5.png" not in names
    assert "BP/scripts/main.js" in names and "BP/scripts/image_videos.js" in names
    # ブロック方式のデータは入れない
    assert "BP/scripts/codec.js" not in names and "BP/scripts/videos.js" not in names


def test_each_video_uses_its_own_entity(built):
    _, archive = built
    for video_id, frames in (("badapple", 8), ("intro", 5)):
        bp = read_json(archive, f"BP/entities/{video_id}.json")["minecraft:entity"]["description"]
        rp = read_json(archive, f"RP/entity/{video_id}.json")["minecraft:client_entity"]["description"]
        render = read_json(archive, f"RP/render_controllers/{video_id}.json")["render_controllers"][f"controller.render.bvpimg.{video_id}"]
        assert bp["identifier"] == f"bvpimg:{video_id}"
        assert rp["identifier"] == f"bvpimg:{video_id}"
        assert bp["properties"]["bvpimg:frame"]["range"] == [0, frames - 1]
        assert bp["properties"]["bvpimg:frame"]["client_sync"] is True
        assert render["arrays"]["textures"]["Array.frames"] == [f"Texture.f{index}" for index in range(frames)]
        assert set(render["arrays"]["textures"]["Array.frames"]) == {f"Texture.{name}" for name in rp["textures"]}
        assert render["textures"] == ["Array.frames[q.property('bvpimg:frame')]"]


def test_geometry_screen_width_and_aspect(built):
    _, archive = built
    geo = read_json(archive, "RP/models/badapple.geo.json")["minecraft:geometry"][0]
    assert geo["description"]["identifier"] == "geometry.bvpimg.badapple"
    assert geo["description"]["texture_width"] == 256
    assert geo["description"]["texture_height"] == 144
    cube = geo["bones"][0]["cubes"][0]
    assert cube["uv"]["north"]["uv_size"] == [256, 144]
    # --screen-blocks 4 → 画面の幅は 4 ブロック = 64 units
    assert cube["size"][0] == 64
    assert abs(cube["size"][0] / cube["size"][1] - 256 / 144) < 0.05


def test_video_list_matches_the_pack(built):
    _, archive = built
    script = archive.read("BP/scripts/image_videos.js").decode("utf-8")
    assert script.startswith("export const IMAGE_VIDEO_LIST = ")
    entries = json.loads(script[script.index("["):script.rindex("]") + 1])
    assert [e["id"] for e in entries] == ["badapple", "intro", "bg_flat"]
    by_id = {e["id"]: e for e in entries}
    assert by_id["badapple"]["frame_count"] == 8 and by_id["badapple"]["ticks_per_frame"] == 1
    # fps 6 → 20 tick/秒 ÷ 6 = 3 tick に 1 フレーム
    assert by_id["intro"]["fps"] == 6.0 and by_id["intro"]["ticks_per_frame"] == 3
    assert by_id["bg_flat"]["hidden"] is True
    assert by_id["badapple"]["width"] == 256 and by_id["badapple"]["height"] == 144
    # ブロック方式の videos.js の形と衝突しない (別のファイル・別の名前)
    assert "export const VIDEO_LIST" not in script
    assert "FRAME_DATA" not in script


def test_script_reads_the_video_list(built):
    _, archive = built
    script = archive.read("BP/scripts/main.js").decode("utf-8")
    assert "__NAMESPACE__" not in script
    assert 'const EVENT_NAMESPACE = "bvpimg";' in script
    assert 'import { IMAGE_VIDEO_LIST } from "./image_videos.js";' in script
    assert "const SCREENS_KEY = `${EVENT_NAMESPACE}:screens`;" in script
    assert "const PLAYING_KEY = `${EVENT_NAMESPACE}:playing`;" in script
    assert "const PROPERTY_NAME = `${EVENT_PREFIX}frame`;" in script
    assert "setProperty(PROPERTY_NAME" in script
    assert "system.runInterval" in script and "system.clearRunInterval" in script
    # 動画ごとに 1 本の runInterval (再生のハンドルは動画 ID の Map に持つ)
    assert "const playback = new Map();" in script
    # 動画の選択は enum の引数 (enumName は公式の型に記載がある)
    assert 'registry.registerEnum(VIDEO_ENUM, ids)' in script
    assert 'enumName: VIDEO_ENUM' in script
    assert "CustomCommandParamType.Integer" in script
    assert "@minecraft/server-ui" not in script
    # ブロック方式の動的なプロパティのキーと衝突しない
    assert "badapple:" not in script


def test_manifests(built):
    _, archive = built
    bp = read_json(archive, "BP/manifest.json")
    rp = read_json(archive, "RP/manifest.json")
    assert bp["header"]["uuid"] != rp["header"]["uuid"]
    assert bp["header"]["min_engine_version"] == rp["header"]["min_engine_version"]
    assert rp["header"]["uuid"] in [dep["uuid"] for dep in bp["dependencies"] if "uuid" in dep]
    assert bp["header"]["uuid"] in [dep["uuid"] for dep in rp["dependencies"]]
    assert [dep["module_name"] for dep in bp["dependencies"] if "module_name" in dep] == ["@minecraft/server"]
    assert bp["modules"][0]["entry"] == "scripts/main.js"
    # サブパックの memory_performance_tier は manifest V3 の実験的な設定なので入れていない
    assert "subpacks" not in rp


def test_rejects_bad_video_specs():
    buffer = io.BytesIO()
    with pytest.raises(ValueError):
        build_image_addon.build_image_addon(buffer, [], namespace="bvpimg")
    with pytest.raises(ValueError):
        build_image_addon.build_image_addon(buffer, [video("BadApple", "x", 2, 64, 36, 20)], namespace="bvpimg")
    with pytest.raises(ValueError):
        build_image_addon.build_image_addon(buffer, [video("a", "x", 2, 64, 36, 20), video("a", "y", 2, 64, 36, 20)], namespace="bvpimg")
    with pytest.raises(ValueError):
        build_image_addon.build_image_addon(buffer, [video("a", "x", 0, 64, 36, 20)], namespace="bvpimg")


def test_extract_frames_with_ffmpeg(tmp_path):
    """ffmpeg が無い環境では飛ばす (CI は ffmpeg が無い)。testsrc のフレームが実際に違うことを確かめる。"""
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg が無い")
    result = build_image_addon.frame_extract.extract_frames(None, 96, 54, 20.0, 4)
    assert len(result["frames"]) == 4
    assert result["width"] == 96 and result["height"] == 54
    assert result["ticks_per_frame"] == 1
    assert len({hash(data) for data in result["frames"]}) == 4
    for index, data in enumerate(result["frames"]):
        (tmp_path / f"frame_{index}.png").write_bytes(data)


def test_video_specs_checks_files(tmp_path):
    args = build_image_addon.parse_args(["--video", "a=" + str(tmp_path / "missing.mp4")])
    with pytest.raises(ValueError):
        build_image_addon.video_specs(args)
    # ID=PATH の形でないときはエラー
    with pytest.raises(ValueError):
        build_image_addon.video_specs(build_image_addon.parse_args(["--video", "a"]))
    # --sample は testsrc を使う (動画のファイルは要らない)
    specs = build_image_addon.video_specs(build_image_addon.parse_args(["--sample", "2"]))
    assert [s["id"] for s in specs] == ["sample1", "sample2"]
    assert all(s["source"] is None for s in specs)


def test_parse_args_rejects_bad_limits():
    parser_args = ["--sample", "1", "--limit", str(build_image_addon.MAX_FRAMES + 1)]
    with pytest.raises(SystemExit):
        build_image_addon.parse_args(parser_args)
    with pytest.raises(SystemExit):
        build_image_addon.parse_args(["--video", "a=x.mp4", "--sample", "1"])
    with pytest.raises(SystemExit):
        build_image_addon.parse_args([])
