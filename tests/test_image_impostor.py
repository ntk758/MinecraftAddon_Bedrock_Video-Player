"""画像モードの「角度で絵を切り替える立体」(インポスター、image_mode/impostor.py) の試作のテスト。"""
import json
import zipfile

import numpy as np
import pytest

import convert_image_mode
from image_mode import atlas, impostor, rp
from mvcodec.volume import write_vox
from test_image_main import props, run

RED, BLUE, WHITE = 1, 2, 3


def palette():
    colours = np.zeros((256, 4), np.uint8)
    colours[RED - 1] = (255, 0, 0, 255)
    colours[BLUE - 1] = (0, 0, 255, 255)
    colours[WHITE - 1] = (255, 255, 255, 255)
    return colours


def figure():
    """幅 6・奥行き 6・高さ 8 の柱。東 (+x) の側面が赤、北 (+y、奥) の側面が青、ほかは白。"""
    volume = np.zeros((6, 6, 8), np.uint8)
    volume[1:5, 1:5, :] = WHITE
    volume[4, 1:5, :] = RED
    volume[1:5, 4, :] = BLUE
    return volume


def coloured_columns(image, colour):
    """その色の画素がある列 (左から) の範囲 (min, max)。無ければ None。"""
    mask = np.all(image[..., :3] == colour, axis=-1) & (image[..., 3] == 255)
    cols = np.nonzero(mask.any(axis=0))[0]
    return (int(cols.min()), int(cols.max())) if len(cols) else None


def render(angle, volume=None):
    volume = figure() if volume is None else volume
    rgb = palette()[:, :3]
    shape = volume.shape
    width, height, scale = impostor.cell_size(shape, 32)
    return impostor.render_view(volume, rgb, shape, angle, width, height, scale)


def test_background_is_transparent_and_figure_is_opaque():
    image = render(0)
    assert image.shape[2] == 4
    assert set(np.unique(image[..., 3])) == {0, 255}
    assert image[0, 0, 3] == 0                      # 角は背景
    assert image[image.shape[0] // 2, image.shape[1] // 2, 3] == 255   # 真ん中は柱


def test_views_turn_counter_clockwise_seen_from_above():
    """向き 0 は正面 (南、-y) から。東 (+x) の赤い面は右の縁に細く見え、北 (奥) の青は見えない。
    90 度 (反時計回りに東へまわる) では赤い面が正面いっぱいに、180 度 (北から) では青が正面、赤は左の縁。"""
    shade = impostor.FACE_SHADE
    red_side = (int(round(255 * shade[(1, 0, 0)])), 0, 0)
    blue_side = (0, 0, int(round(255 * shade[(0, 1, 0)])))
    front = render(0)
    assert coloured_columns(front, blue_side) is None
    assert coloured_columns(front, red_side) is None  # 真正面からは横の面は見えない
    east = render(90)
    red = coloured_columns(east, red_side)
    assert red is not None and red[1] - red[0] >= east.shape[1] // 4
    back = render(180)
    assert coloured_columns(back, blue_side) is not None
    # 少しだけ回すと、東の赤い面は 0 度から右へ (+) なら右の縁、北の側から (180 + 20) なら左の縁に出る
    right = coloured_columns(render(20), red_side)
    left = coloured_columns(render(160), red_side)
    centre = render(0).shape[1] / 2
    assert right is not None and right[0] > centre
    assert left is not None and left[1] < centre


def test_render_cells_orders_slots_by_frame_then_view():
    volumes = [figure(), np.zeros((6, 6, 8), np.uint8)]
    rgbs = [palette()[:, :3]] * 2
    width, height, scale = impostor.cell_size((6, 6, 8), 16)
    cells = list(impostor.render_cells(volumes, rgbs, (6, 6, 8), 4, width, height, scale))
    assert len(cells) == 8
    assert all(cell[..., 3].any() for cell in cells[:4])       # コマ 0 の 4 方向
    assert not any(cell[..., 3].any() for cell in cells[4:])   # コマ 1 は空


def test_atlas_keeps_alpha_and_rejects_mixed_channels():
    rgba = [np.full((4, 6, 4), (10, 20, 30, 0), np.uint8), np.full((4, 6, 4), 200, np.uint8)]
    sheets, layout = atlas.pack_sheets(rgba, 6, 4, max_sheet=64)
    assert sheets[0].mode == "RGBA" and layout["frame_count"] == 2
    assert np.asarray(sheets[0])[0, 0, 3] == 0
    with pytest.raises(ValueError):
        atlas.pack_sheets([rgba[0], np.zeros((4, 6, 3), np.uint8)], 6, 4, max_sheet=64)


def video(**extra):
    base = {"id": "fig", "fps": 10, "frame_count": 5, "width": 8, "height": 16, "sheet_width": 80,
            "sheet_height": 80, "cols": 8, "rows": 4, "cells_per_sheet": 32, "sheets": 2, "gutter": 1,
            "screen_blocks": [3, 6], "texture_paths": ["t/0", "t/1"], "lighting": "bright"}
    return {**base, **extra}


def test_resource_pack_rotates_a_root_bone_toward_the_camera():
    v = video(views=8, loop=True, cells=40)
    geometry = rp.geometry_json(v, "a", "ns")["minecraft:geometry"][0]["bones"]
    assert geometry[0] == {"name": "root", "pivot": [0, 0, 0]}
    assert all(bone["parent"] == "root" for bone in geometry[1:])
    entity = rp.client_entity_json(v, "a", "ns")["minecraft:client_entity"]["description"]
    assert entity["materials"] == {"default": "entity_alphatest"}
    assert entity["scripts"]["animate"] == ["face"]
    lines = entity["scripts"]["pre_animation"]
    assert any("q.rotation_to_camera(1)" in line for line in lines)
    assert "v.slot = v.frame * 8 + v.view;" in lines
    # 実機 (1.26.63) で決めた符号 (DECISIONS.md の A18): 向きは体の向き - カメラへの向き、板はその逆に回す
    assert "v.rel = 1 * (q.body_y_rotation - q.rotation_to_camera(1));" in lines
    assert "v.face = -1 * v.rel;" in lines
    assert "v.sheet = math.floor(v.slot / 32);" in lines
    # 繰り返す: 終わりで止めずに頭へ
    assert any("math.mod(v.base_frame" in line and ", 5)" in line for line in lines)
    files = rp.rp_files([{**v, "methods": ["a"]}], "ns")
    animation = json.loads(files["animations/screen_fig_a.animation.json"])
    bones = animation["animations"]["animation.ns.screen_fig_a.face"]["bones"]
    assert bones == {"root": {"rotation": [0, "v.face", 0]}}
    with pytest.raises(ValueError):
        rp.rp_files([{**v, "methods": ["c"]}], "ns")


def test_ordinary_videos_are_unchanged():
    v = video()
    entity = rp.client_entity_json(v, "a", "ns")["minecraft:client_entity"]["description"]
    assert entity["materials"] == {"default": "entity"} and "animations" not in entity
    assert not any("rotation_to_camera" in line or "math.mod(v.base_frame" in line
                   for line in entity["scripts"]["pre_animation"])
    assert "root" not in [bone["name"] for bone in rp.geometry_json(v, "a", "ns")["minecraft:geometry"][0]["bones"]]


def write_figure_vox(path, frames=3):
    volume = figure()
    models = []
    for _ in range(frames):
        x, y, z = np.nonzero(volume)
        voxels = np.stack([x, y, z, volume[x, y, z]], 1).astype(np.uint8)
        models.append((volume.shape, voxels))
    path.write_bytes(write_vox(models, palette()))


def test_vox_input_builds_a_looping_impostor_addon(tmp_path):
    source = tmp_path / "fig.vox"
    write_figure_vox(source)
    output = tmp_path / "fig_image.mcaddon"
    assert convert_image_mode.main([str(source), "-o", str(output), "--views", "4", "--height", "32",
                                    "--figure-blocks", "3"]) == 0
    with zipfile.ZipFile(output) as z:
        names = z.namelist()
        script = z.read("BP/scripts/image_videos.js").decode("utf-8")
    assert "RP/animations/screen_fig_a.animation.json" in names
    meta = json.loads(script.split("IMAGE_VIDEOS = ", 1)[1].rsplit(";", 1)[0])[0]
    assert meta["views"] == 4 and meta["loop"] is True
    assert meta["frame_count"] == 3 and meta["cells"] == 12 and meta["fps"] == 10
    assert meta["screen_blocks"][1] == 3 and meta["audio_chunks"] == 0
    assert meta["methods"] == ["a"]


def test_vox_and_videos_cannot_be_mixed(tmp_path, capsys):
    source = tmp_path / "fig.vox"
    write_figure_vox(source)
    other = tmp_path / "clip.mp4"
    other.write_bytes(b"")
    assert convert_image_mode.main([str(source), str(other), "-o", str(tmp_path / "x.mcaddon")]) != 0


def test_looping_video_restarts_instead_of_pausing(tmp_path):
    clip = {"id": "fig", "title": "fig", "fps": 10, "frame_count": 10, "audio_chunks": 0, "audio_chunk_seconds": 4,
            "screen_blocks": [3, 6], "methods": ["a"], "width": 8, "height": 16, "loop": True, "views": 4}
    out = run(tmp_path, [{"cmd": "setup"}, {"tick": 2}, {"cmd": "start"}, {"tick": 25}, {"snap": "after_end"}],
              videos=[clip])
    snap = {s["label"]: s for s in out["snaps"]}["after_end"]
    state = props(snap)
    # 1 秒の動画を 1.25 秒: 止まらずに 2 周目の頭のあたり
    assert state["state"] == 1
    assert state["frame"] < 5
