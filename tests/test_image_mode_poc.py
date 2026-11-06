"""画像（テクスチャ）再生方式の PoC (image_mode/image_poc.py) の構造のテスト。

ゲームでの動作はここでは確かめない (実機での確認は docs/IMAGE_MODE.md の手順)。
このテストは .mcaddon の中身の形 (JSON の整合) を確かめる。
"""
import io
import json
import struct
import sys
import zipfile
import zlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "image_mode"))

import image_poc  # noqa: E402


def png(width, height):
    """PNG を自分で作る (画像のライブラリを使わない)。中身は単色の固定の色。"""
    raw = b"".join(b"\x00" + bytes([9, 120, 240]) * width for _ in range(height))
    chunks = [("IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)),
              ("IDAT", zlib.compress(raw)), ("IEND", b"")]
    out = b"\x89PNG\r\n\x1a\n"
    for kind, body in chunks:
        out += struct.pack(">I", len(body)) + kind.encode("ascii") + body + struct.pack(">I", zlib.crc32(kind.encode("ascii") + body))
    return out


@pytest.fixture(scope="module")
def built():
    frames = [png(192, 108) for _ in range(12)]
    buffer = io.BytesIO()
    info = image_poc.build_poc_addon(buffer, frames, 192, 108, fps=20.0, namespace="bvpimg")
    archive = zipfile.ZipFile(io.BytesIO(buffer.getvalue()))
    return info, archive


def read_json(archive, path):
    return json.loads(archive.read(path).decode("utf-8"))


def test_ticks_per_frame():
    assert image_poc.ticks_per_frame(20.0) == 1
    assert image_poc.ticks_per_frame(10.0) == 2
    assert image_poc.ticks_per_frame(5.0) == 4
    # fps > 20 は 1 tick に 1 フレームが上限なので 20 に clamp する
    assert image_poc.ticks_per_frame(30.0) == 1
    with pytest.raises(ValueError):
        image_poc.ticks_per_frame(0.0)


def test_png_size_reads_dims():
    assert image_poc.png_size(png(192, 108)) == (192, 108)
    with pytest.raises(ValueError):
        image_poc.png_size(b"not a png")


def test_zip_has_the_expected_files(built):
    _, archive = built
    names = set(archive.namelist())
    assert "BP/manifest.json" in names and "RP/manifest.json" in names
    assert "BP/entities/screen.json" in names
    assert "RP/entity/screen.json" in names
    assert "RP/render_controllers/screen.json" in names
    assert "RP/models/screen.geo.json" in names
    assert "BP/scripts/main.js" in names
    for index in range(12):
        assert f"RP/textures/bvpimg/screen/frame_{index}.png" in names
    # ブロック方式のデータは入れない (別方式なので main.js / codec.js の再生データを持たない)
    assert "BP/scripts/codec.js" not in names
    assert "BP/scripts/videos.js" not in names


def test_bp_property_matches_frame_count(built):
    _, archive = built
    doc = read_json(archive, "BP/entities/screen.json")
    description = doc["minecraft:entity"]["description"]
    assert description["identifier"] == "bvpimg:screen"
    prop = description["properties"]["bvpimg:frame"]
    assert prop["type"] == "int"
    assert prop["range"] == [0, 11]
    assert prop["default"] == 0
    # client_sync が無いと Resource Pack の描画がこの値を読めない
    assert prop["client_sync"] is True
    # プロパティ名は namespace の接頭辞が要る (公式のルール)
    assert prop is description["properties"]["bvpimg:frame"]
    assert len(description["properties"]) <= image_poc.ENTITY_PROPERTY_LIMIT


def test_rp_entity_and_render_controller_agree(built):
    _, archive = built
    client = read_json(archive, "RP/entity/screen.json")["minecraft:client_entity"]["description"]
    render = read_json(archive, "RP/render_controllers/screen.json")["render_controllers"]["controller.render.bvpimg.screen"]
    assert client["identifier"] == "bvpimg:screen"
    assert client["materials"] == {"default": "entity"}
    assert client["geometry"] == {"default": "geometry.bvpimg.screen"}
    assert client["render_controllers"] == ["controller.render.bvpimg.screen"]
    array = render["arrays"]["textures"]["Array.frames"]
    assert array == [f"Texture.f{index}" for index in range(12)]
    # 配列の中身と client entity の textures のキーが対応している
    assert set(array) == {f"Texture.{name}" for name in client["textures"]}
    assert render["textures"] == ["Array.frames[q.property('bvpimg:frame')]"]
    # BP のプロパティ名と RP が読む名前が同じ
    prop_name = list(read_json(archive, "BP/entities/screen.json")["minecraft:entity"]["description"]["properties"])[0]
    assert prop_name in render["textures"][0]


def test_geometry_matches_frame_size(built):
    _, archive = built
    geo = read_json(archive, "RP/models/screen.geo.json")["minecraft:geometry"][0]
    assert geo["description"]["identifier"] == "geometry.bvpimg.screen"
    assert geo["description"]["texture_width"] == 192
    assert geo["description"]["texture_height"] == 108
    cube = geo["bones"][0]["cubes"][0]
    assert cube["uv"]["north"]["uv_size"] == [192, 108]
    assert cube["uv"]["south"]["uv_size"] == [192, 108]
    # 画面の aspect ratio はテクスチャと同じ (16:9) にする
    size = cube["size"]
    assert abs(size[0] / size[1] - 192 / 108) < 0.02


def test_script_has_no_placeholders(built):
    _, archive = built
    script = archive.read("BP/scripts/main.js").decode("utf-8")
    for placeholder in ("__NAMESPACE__", "__FRAME_COUNT__", "__TICKS_PER_FRAME__"):
        assert placeholder not in script
    assert 'const EVENT_NAMESPACE = "bvpimg";' in script
    assert "const PROPERTY_NAME = `${EVENT_PREFIX}frame`;" in script
    assert "setProperty(PROPERTY_NAME" in script
    # UI の API は使わない (この PoC は @minecraft/server の API だけ)
    assert "@minecraft/server-ui" not in script


def test_manifest_dependencies(built):
    _, archive = built
    bp = read_json(archive, "BP/manifest.json")
    rp = read_json(archive, "RP/manifest.json")
    assert bp["header"]["min_engine_version"] == rp["header"]["min_engine_version"]
    assert bp["header"]["uuid"] != rp["header"]["uuid"]
    # BP → RP の依存 (アドオンとして一緒に読まれる) と RP → BP の依存
    pack_uuids = [dep["uuid"] for dep in bp["dependencies"] if "uuid" in dep]
    assert rp["header"]["uuid"] in pack_uuids
    assert bp["header"]["uuid"] in [dep["uuid"] for dep in rp["dependencies"]]
    # この PoC は server-ui を使わないので依存に残さない
    assert [dep["module_name"] for dep in bp["dependencies"] if "module_name" in dep] == ["@minecraft/server"]
    assert bp["modules"][0]["entry"] == "scripts/main.js"


def test_rejects_empty_frames():
    buffer = io.BytesIO()
    with pytest.raises(ValueError):
        image_poc.build_poc_addon(buffer, [], 192, 108)
