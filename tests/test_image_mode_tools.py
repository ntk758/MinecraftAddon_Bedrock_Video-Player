"""画像（テクスチャ）再生方式の、別アドオンを作るヘルプと、PPM → PNG の変換 (image_mode/)。

ブロック方式のアドオン (pack_builder.py) とは別の名前・別の接頭辞・別の出力ファイルになることを確認する。
"""
import io
import zipfile
from pathlib import Path

import pytest

from image_mode.build_image_addon import (
    IMAGE_NAMESPACE_SUFFIX,
    IMAGE_PACK_SUFFIX,
    MAX_FRAMES,
    build_image_addon_bytes,
    image_namespace,
    image_output_path,
    image_pack_name,
)
from image_mode.frame_extract import MAX_FPS, png_from_ppm, png_size, ticks_per_frame


def png(width: int = 32, height: int = 18, color=(10, 20, 30)) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, format="PNG")
    return buffer.getvalue()


def ppm(width: int = 32, height: int = 18, color=(10, 20, 30)) -> bytes:
    """web/app.js の canvasToPpm と同じ形の PPM (P6 の raw の RGB)。"""
    header = f"P6\n{width} {height}\n255\n".encode()
    pixel = bytes(color)
    return header + pixel * (width * height)


def test_image_namespace_and_pack_name_do_not_collide_with_the_block_addon():
    """両方のアドオンを一緒に入れると、ゲームに登録するコマンドの名前が同じで衝突する。"""
    assert image_namespace("badapple") == f"badapple{IMAGE_NAMESPACE_SUFFIX}"
    assert image_pack_name("Block Video Player") == f"Block Video Player{IMAGE_PACK_SUFFIX}"
    assert image_namespace("badapple") != "badapple"


def test_image_output_path_is_next_to_the_block_addon():
    assert image_output_path("dist/VideoPlayer.mcaddon") == Path("dist/VideoPlayer_images.mcaddon")
    assert image_output_path(Path("C:/out/a/VideoPlayer.mcaddon")) == Path("C:/out/a/VideoPlayer_images.mcaddon")


def test_image_mode_is_a_package_the_exe_and_the_browser_reads():
    """EXE とブラウザ版は `image_mode` をパッケージとして読む (video_player_gui.py と worker.js の読み方)。"""
    import image_mode
    import image_mode.build_image_addon as build_module
    import image_mode.frame_extract as frame_module

    assert image_mode.__file__.endswith("__init__.py")
    assert build_module.IMAGE_NAMESPACE_SUFFIX == IMAGE_NAMESPACE_SUFFIX
    assert callable(frame_module.extract_frames)


def test_build_image_addon_bytes_writes_a_pack_with_its_own_prefix():
    import json

    archive = zipfile.ZipFile(io.BytesIO(build_image_addon_bytes(
        [{"id": "clip", "title": "Clip", "frames": [png(), png()], "width": 32, "height": 18, "fps": 10},
         {"id": "other", "title": "Other", "frames": [png()], "width": 32, "height": 18, "fps": 10}],
        image_pack_name("Block Video Player"), image_namespace("badapple"),
        description="画像（テクスチャ）再生方式のアドオン")))
    names = archive.namelist()
    assert "BP/entities/clip.json" in names
    assert "RP/textures/badapple_img/clip/frame_0.png" in names
    assert "RP/render_controllers/other.json" in names
    assert "BP/scripts/image_videos.js" in names
    assert "BP/pack_icon.png" in names and "RP/pack_icon.png" in names

    bp_manifest = json.loads(archive.read("BP/manifest.json"))
    assert bp_manifest["header"]["name"].startswith("Block Video Player_images v")
    assert bp_manifest["header"]["description"] == "画像（テクスチャ）再生方式のアドオン"
    assert bp_manifest["dependencies"][0]["module_name"] == "@minecraft/server"  # UI の依存は残さない
    assert json.loads(archive.read("RP/manifest.json"))["header"]["description"] == "画像（テクスチャ）再生方式のアドオン"

    entity = json.loads(archive.read("BP/entities/clip.json"))["minecraft:entity"]
    assert entity["description"]["identifier"] == "badapple_img:clip"
    assert "badapple_img:frame" in entity["description"]["properties"]
    rp_entity = json.loads(archive.read("RP/entity/clip.json"))["minecraft:client_entity"]
    assert rp_entity["description"]["identifier"] == "badapple_img:clip"
    assert rp_entity["description"]["materials"]["default"] == "entity"  # カスタムマテリアルは作らない
    assert rp_entity["description"]["textures"]["f1"] == "textures/badapple_img/clip/frame_1"
    controller = json.loads(archive.read("RP/render_controllers/clip.json"))["render_controllers"][
        "controller.render.badapple_img.clip"]
    assert controller["arrays"]["textures"]["Array.frames"] == ["Texture.f0", "Texture.f1"]
    assert controller["textures"] == ["Array.frames[q.property('badapple_img:frame')]"]
    assert controller["geometry"] == "Geometry.default"
    assert controller["materials"] == [{"*": "Material.default"}]

    geometry = json.loads(archive.read("RP/models/clip.geo.json"))["minecraft:geometry"][0]
    assert geometry["description"]["identifier"] == "geometry.badapple_img.clip"
    assert geometry["description"]["texture_width"] == 32 and geometry["description"]["texture_height"] == 18

    scripts = archive.read("BP/scripts/main.js").decode("utf-8")
    assert 'const EVENT_NAMESPACE = "badapple_img";' in scripts
    assert "export const IMAGE_VIDEO_LIST" in archive.read("BP/scripts/image_videos.js").decode("utf-8")


def test_build_image_addon_rejects_bad_input():
    video = {"id": "clip", "title": "Clip", "frames": [png()], "width": 32, "height": 18, "fps": 10}
    with pytest.raises(ValueError):
        build_image_addon_bytes([], "x", "bvp_img")
    with pytest.raises(ValueError):
        build_image_addon_bytes([{**video, "id": "Clip-Name"}], "x", "bvp_img")
    with pytest.raises(ValueError):
        build_image_addon_bytes([{**video, "frames": []}], "x", "bvp_img")
    with pytest.raises(ValueError):
        build_image_addon_bytes([{**video, "frames": [png()] * (MAX_FRAMES + 1)}], "x", "bvp_img")


def test_png_from_ppm_keeps_the_canvas_size_and_colors():
    data = png_from_ppm(ppm(24, 12, (200, 40, 90)), 24, 12)
    assert png_size(data) == (24, 12)
    from PIL import Image

    assert Image.open(io.BytesIO(data)).getpixel((11, 5)) == (200, 40, 90)


def test_png_from_ppm_keeps_a_black_first_pixel():
    """画素の 0x00 はヘッダーの区切りと区別できないので、改行を 1 文字だけ飛ばす。"""
    data = png_from_ppm(ppm(4, 2, (0, 0, 0)), 4, 2)
    assert png_size(data) == (4, 2)
    from PIL import Image

    assert Image.open(io.BytesIO(data)).getpixel((0, 0)) == (0, 0, 0)
    assert Image.open(io.BytesIO(data)).getpixel((3, 1)) == (0, 0, 0)


def test_png_from_ppm_refuses_a_header_it_cannot_read():
    with pytest.raises(ValueError):
        png_from_ppm(b"P7 24 12 255\n" + b"\x00" * 288, 24, 12)


def test_frame_rate_over_20_is_clamped_to_the_tick_rate():
    assert ticks_per_frame(MAX_FPS) == 1
    assert ticks_per_frame(25) == 1
    assert ticks_per_frame(10) == 2
    assert ticks_per_frame(3) == 7
