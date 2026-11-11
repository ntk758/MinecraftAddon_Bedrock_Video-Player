"""画像モードのリソースパック JSON (image_mode/rp.py) のテスト。"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from image_mode import rp

NS = "bvp"


def make_video(**over):
    """64x36 のコマ 300 枚、2048x2048 のシート (gutter 1) の見本。"""
    video = {
        "id": "demo",
        "title": "Demo",
        "fps": 30,
        "frame_count": 300,
        "width": 64,
        "height": 36,
        "sheet_width": 2048,
        "sheet_height": 2048,
        "gutter": 1,
        "screen_blocks": [16, 9],
        "audio_chunks": 0,
        "audio_chunk_seconds": 4,
        "methods": ["a", "c"],
    }
    video.update(over)
    video["cols"] = (video["sheet_width"] + video["gutter"]) // (video["width"] + video["gutter"])
    video["rows"] = (video["sheet_height"] + video["gutter"]) // (video["height"] + video["gutter"])
    video["cells_per_sheet"] = video["cols"] * video["rows"]
    video["sheets"] = -(-video["frame_count"] // video["cells_per_sheet"])
    video["texture_paths"] = [f"textures/bvp_image/{video['id']}/sheet_{n}" for n in range(video["sheets"])]
    return video


@pytest.fixture(scope="module")
def video():
    return make_video()


@pytest.fixture(scope="module")
def files(video):
    return rp.rp_files([video], NS)


def load(files, path):
    return json.loads(files[path])


def test_files_are_valid_json_with_indent_and_newline(files):
    assert set(files) == {
        "entity/screen_demo_a.entity.json", "models/entity/screen_demo_a.geo.json",
        "render_controllers/screen_demo_a.render_controllers.json",
        "entity/screen_demo_c.entity.json", "models/entity/screen_demo_c.geo.json",
        "render_controllers/screen_demo_c.render_controllers.json",
        "materials/entity.material",
    }
    for path, text in files.items():
        json.loads(text)
        assert text.endswith("}\n"), path
        assert text.startswith("{\n  "), path  # indent 2


def test_identifiers_match_spec(files):
    for m in "ac":
        entity = load(files, f"entity/screen_demo_{m}.entity.json")["minecraft:client_entity"]["description"]
        assert entity["identifier"] == f"bvp:screen_demo_{m}"
        assert entity["geometry"] == {"default": f"geometry.bvp.screen_demo_{m}"}
        assert entity["render_controllers"] == [f"controller.render.bvp.screen_demo_{m}"]
        geo = load(files, f"models/entity/screen_demo_{m}.geo.json")["minecraft:geometry"][0]["description"]
        assert geo["identifier"] == f"geometry.bvp.screen_demo_{m}"
        rc = load(files, f"render_controllers/screen_demo_{m}.render_controllers.json")
        assert list(rc["render_controllers"]) == [f"controller.render.bvp.screen_demo_{m}"]
        assert rc["format_version"] == "1.10.0"
    assert load(files, "models/entity/screen_demo_a.geo.json")["format_version"] == "1.12.0"
    assert load(files, "entity/screen_demo_a.entity.json")["format_version"] == "1.10.0"
    # 方式ごとのマテリアル
    mats = {m: load(files, f"entity/screen_demo_{m}.entity.json")["minecraft:client_entity"]["description"]["materials"]
            for m in "ac"}
    assert mats["a"] == {"default": "entity_alphatest"}
    assert mats["c"] == {"default": "bvp_uv_anim"}


def test_geometry_description_covers_screen(files):
    for m in "ac":
        desc = load(files, f"models/entity/screen_demo_{m}.geo.json")["minecraft:geometry"][0]["description"]
        assert desc["texture_width"] == 2048 and desc["texture_height"] == 2048
        assert desc["visible_bounds_width"] >= 19  # 16x9 の対角 (約 18.4) 以上
        assert desc["visible_bounds_height"] >= 19
        assert desc["visible_bounds_offset"] == [0, 4.5, 0]


def test_method_a_bones_and_visibility(files, video):
    cells = video["cells_per_sheet"]
    geo = load(files, "models/entity/screen_demo_a.geo.json")["minecraft:geometry"][0]
    assert [b["name"] for b in geo["bones"]] == [f"c{i}" for i in range(cells)]
    for bone in geo["bones"]:
        assert len(bone["cubes"]) == 1
        assert bone["cubes"][0]["size"] == [256, 144, 0]  # 16x9 ブロック = 256x144 (1/16 ブロック)
        assert bone["cubes"][0]["origin"] == [-128, 0, 0]
    rc = load(files, "render_controllers/screen_demo_a.render_controllers.json")
    ctl = rc["render_controllers"]["controller.render.bvp.screen_demo_a"]
    vis = ctl["part_visibility"]
    assert len(vis) == cells + 1
    assert all(len(entry) == 1 for entry in vis)
    assert vis[0] == {"*": False}
    assert list(vis[1]) == ["c0"] and list(vis[-1]) == [f"c{cells - 1}"]
    assert "v.cell == 0" in vis[1]["c0"] and "q.property('bvp:state') != 0" in vis[1]["c0"]
    assert "uv_anim" not in ctl
    assert ctl["arrays"] == {"textures": {"Array.sheets": ["Texture.sheet_0"]}}
    assert ctl["textures"] == ["Array.sheets[v.sheet]"]
    assert ctl["geometry"] == "Geometry.default"
    assert ctl["materials"] == [{"*": "Material.default"}]


def test_method_a_cell_uvs_inside_sheet_without_overlap(files, video):
    geo = load(files, "models/entity/screen_demo_a.geo.json")["minecraft:geometry"][0]
    gutter = video["gutter"]
    rects = []
    for bone in geo["bones"]:
        uv = bone["cubes"][0]["uv"]
        assert list(uv) == ["north"]
        (u, v), (w, h) = uv["north"]["uv"], uv["north"]["uv_size"]
        assert (w, h) == (64, 36)
        assert 0 <= u and 0 <= v and u + w <= 2048 and v + h <= 2048
        rects.append((u, v, u + w, v + h))
    assert len({(r[0], r[1]) for r in rects}) == len(rects)
    # 全組の重なり検査。gutter 分だけ広げた矩形でも重ならない = 隣と gutter 以上離れている
    for i, a in enumerate(rects):
        for b in rects[i + 1:]:
            apart = (a[2] + gutter <= b[0] or b[2] + gutter <= a[0]
                     or a[3] + gutter <= b[1] or b[3] + gutter <= a[1])
            assert apart, (a, b)
    # SPEC.md の式
    for i, r in enumerate(rects):
        assert (r[0], r[1]) == ((i % video["cols"]) * 65, (i // video["cols"]) * 37)


def test_method_c_uv_anim_and_material(files, video):
    geo = load(files, "models/entity/screen_demo_c.geo.json")["minecraft:geometry"][0]
    assert len(geo["bones"]) == 1 and len(geo["bones"][0]["cubes"]) == 1
    assert geo["bones"][0]["cubes"][0]["uv"]["north"] == {"uv": [0, 0], "uv_size": [64, 36]}
    ctl = load(files, "render_controllers/screen_demo_c.render_controllers.json")[
        "render_controllers"]["controller.render.bvp.screen_demo_c"]
    anim = ctl["uv_anim"]
    assert anim["scale"] == [1, 1]
    assert anim["offset"][0] == f"math.mod(v.cell, {video['cols']}) * 65 / 2048"
    assert anim["offset"][1] == f"math.floor(v.cell / {video['cols']}) * 37 / 2048"
    assert ctl["part_visibility"] == [{"*": "q.property('bvp:state') != 0"}]
    material = load(files, "materials/entity.material")
    assert material["materials"]["bvp_uv_anim:entity_alphatest"] == {"+defines": ["USE_UV_ANIM"]}
    assert rp.material_json() == material


def test_material_only_when_method_c_used():
    only_a = rp.rp_files([make_video(methods=["a"])], NS)
    assert "materials/entity.material" not in only_a
    assert not any(path.endswith("_c.entity.json") for path in only_a)
    only_c = rp.rp_files([make_video(methods=["c"])], NS)
    assert "materials/entity.material" in only_c


def test_pre_animation_uses_properties_and_numbers(files, video):
    for m in "ac":
        desc = load(files, f"entity/screen_demo_{m}.entity.json")["minecraft:client_entity"]["description"]
        script = "\n".join(desc["scripts"]["pre_animation"])
        for prop in ("state", "frame", "seq"):
            assert f"q.property('bvp:{prop}')" in script
        assert "* 30)" in script  # fps
        assert "math.min(" in script and "math.floor(" in script
        assert ", 299)" in script  # frame_count - 1
        assert f"v.frame / {video['cells_per_sheet']}" in script
        assert f"v.sheet * {video['cells_per_sheet']}" in script
        assert "(v.seq_seen ?? -1)" in script


def test_numbers_follow_video_dict():
    """複数シート・別の fps / gutter でも数が追従する。"""
    video = make_video(fps=24, frame_count=5000, sheet_width=512, sheet_height=256, gutter=2,
                       screen_blocks=[8, 4.5])
    assert video["sheets"] > 1
    out = rp.rp_files([video], "ns2")
    desc = json.loads(out["entity/screen_demo_c.entity.json"])["minecraft:client_entity"]["description"]
    script = "\n".join(desc["scripts"]["pre_animation"])
    assert "* 24)" in script and ", 4999)" in script
    assert f"v.frame / {video['cells_per_sheet']}" in script
    assert "q.property('ns2:state')" in script
    assert list(desc["textures"]) == [f"sheet_{n}" for n in range(video["sheets"])]
    assert desc["textures"]["sheet_1"] == "textures/bvp_image/demo/sheet_1"
    rc = json.loads(out["render_controllers/screen_demo_c.render_controllers.json"])
    ctl = rc["render_controllers"]["controller.render.ns2.screen_demo_c"]
    assert ctl["arrays"]["textures"]["Array.sheets"] == [f"Texture.sheet_{n}" for n in range(video["sheets"])]
    assert ctl["uv_anim"]["offset"][0] == f"math.mod(v.cell, {video['cols']}) * 66 / 512"
    assert ctl["uv_anim"]["offset"][1] == f"math.floor(v.cell / {video['cols']}) * 38 / 256"
    geo = json.loads(out["models/entity/screen_demo_c.geo.json"])["minecraft:geometry"][0]
    assert geo["bones"][0]["cubes"][0]["size"] == [128, 72, 0]
    assert geo["description"]["texture_width"] == 512


def test_defaults_when_optional_keys_missing():
    video = make_video()
    for key in ("fps", "gutter", "screen_blocks"):
        del video[key]
    out = rp.rp_files([video], NS)
    desc = json.loads(out["entity/screen_demo_a.entity.json"])["minecraft:client_entity"]["description"]
    assert "* 30)" in "\n".join(desc["scripts"]["pre_animation"])
    geo = json.loads(out["models/entity/screen_demo_a.geo.json"])["minecraft:geometry"][0]
    assert geo["bones"][0]["cubes"][0]["size"] == [256, 144, 0]  # 幅 16、高さ 16 * 36 / 64 = 9


def test_invalid_method_rejected(video):
    with pytest.raises(ValueError):
        rp.geometry_json(video, "b", NS)


def test_last_frame_stays_inside_texture_array(video):
    """最後のコマでも v.sheet が配列の範囲に収まる (添字の折り返しに頼らない)。"""
    assert (video["frame_count"] - 1) // video["cells_per_sheet"] <= video["sheets"] - 1
    assert re.search(r"math\.min\([^;]*, 299\)", "\n".join(rp.pre_animation(video, NS)))
