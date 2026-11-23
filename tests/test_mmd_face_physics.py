"""MMD の表情 (モーフ)・物理 (髪の揺れ)・板の画素の細かさのテスト (docs/research/mmd_image_mode_design_2026-11.md)。

モデルは tests/mmd_fixture.py の箱の人形に、extras (頭の後ろに垂れる髪 3 節、頭と上半身の当たり、口のモーフ) を足したもの。
"""
import json
import math
import sys
import zipfile
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import convert_image_mode  # noqa: E402
import mmd_fixture as fixture  # noqa: E402
from image_mode import planes, rig, rig_anim, rig_face  # noqa: E402
from mvcodec import mmd, mmd_physics  # noqa: E402

HAIR_TIP = np.array([0.0, 9.5, 1.4, 1.0])


def lean(degrees, frames=30):
    """上半身を X 軸まわりに degrees 度回す踊り (0 → frames フレーム)。正は後ろへ反る (頭が +Z へ)。"""
    a = math.radians(degrees) / 2
    return [("上半身", 0, (0, 0, 0), (0, 0, 0, 1)), ("上半身", frames, (0, 0, 0), (math.sin(a), 0, 0, math.cos(a)))]


@pytest.fixture
def doll(tmp_path):
    def make(keys, morphs=()):
        pmx, vmd = fixture.write_doll(tmp_path, keys, morphs=morphs, extras=True)
        model = mmd.read_pmx(pmx)
        return model, mmd.Poser(model), mmd.read_vmd(vmd), pmx, vmd
    return make


# --- 読み込み ---

def test_pmx_morphs_rigid_bodies_and_joints_are_read(doll):
    model, _poser, _tracks, _pmx, vmd = doll(lean(0), morphs=[("あ", 0, 0.0), ("あ", 10, 1.0)])
    assert model.tail_error is None
    assert [(m["name"], m["kind"]) for m in model.morphs] == [
        ("あ", mmd.MORPH_VERTEX), ("にやり", mmd.MORPH_GROUP), ("うなずき", mmd.MORPH_BONE)]
    assert len(model.morphs[0]["vertices"]) == 4
    assert [b["mode"] for b in model.rigid_bodies] == [0, 0, 1, 1, 1]
    assert model.rigid_bodies[1]["shape"] == mmd.SHAPE_CAPSULE
    assert [(j["a"], j["b"]) for j in model.joints] == [(0, 2), (2, 3), (3, 4)]
    assert mmd.read_vmd_morphs(vmd) == {"あ": [(0, 0.0), (10, 1.0)]}
    # ボーンのキーの読み方は変わらない
    assert set(mmd.read_vmd(vmd)) == {"上半身"}


def test_a_broken_tail_keeps_the_bones(tmp_path):
    """モーフより後ろが壊れていても、ボーンまでは使える (理由は tail_error)。"""
    data = fixture.pmx_bytes(extras=True)
    path = tmp_path / "cut.pmx"
    path.write_bytes(data[:-200])
    model = mmd.read_pmx(path)
    assert len(model.bones) == len(fixture.BONES) + len(fixture.HAIR_BONES)
    assert model.tail_error


def test_group_and_bone_morphs():
    from io import BytesIO  # noqa: F401  (読み込みはファイル経由)
    import tempfile
    folder = Path(tempfile.mkdtemp())
    pmx, _vmd = fixture.write_doll(folder, lean(0), extras=True)
    model = mmd.read_pmx(pmx)
    offsets = mmd.morph_vertex_offsets(model, {"にやり": 1.0})
    moved = np.flatnonzero(np.abs(offsets).sum(axis=1) > 0)
    assert len(moved) == 4 and np.allclose(offsets[moved], [0, -fixture.MOUTH_OPEN / 2, 0])
    bones = mmd.morph_bone_offsets(model, {"うなずき": 0.5})
    head = fixture.INDEX["頭"]
    angle = 2 * math.degrees(math.atan2(bones[head][1][0], bones[head][1][3]))
    assert angle == pytest.approx(15, abs=1e-6)


def test_sample_morph_is_linear_between_keys():
    keys = [(0, 0.0), (10, 1.0), (20, 0.5)]
    assert mmd.sample_morph(keys, -5) == 0.0
    assert mmd.sample_morph(keys, 5) == pytest.approx(0.5)
    assert mmd.sample_morph(keys, 15) == pytest.approx(0.75)
    assert mmd.sample_morph(keys, 99) == 0.5


# --- 物理 ---

def tip_positions(skins, bone):
    return np.array([(skin[bone] @ HAIR_TIP)[:3] for skin in skins])


@pytest.mark.parametrize("preset", ["light", "standard", "full"])
def test_hair_hangs_down_when_leaning_back(doll, preset):
    """後ろへ反ると、体に付いたままの髪は斜めになるが、物理の髪は重力で真下へ垂れる。節の間は離れない。"""
    model, poser, tracks, _pmx, _vmd = doll(lean(70))
    frames = np.arange(0, 61)
    skins = mmd_physics.simulate(model, poser, tracks, frames, preset)
    rigid = np.array([poser.pose(tracks, float(f)) for f in frames])
    bone = fixture.INDEX_ALL["髪3"]
    root = (skins[-1][fixture.INDEX_ALL["髪1"]] @ np.array([0, 15.5, 1.4, 1.0]))[:3]
    tip = tip_positions(skins, bone)[-1]
    direction = (tip - root) / np.linalg.norm(tip - root)
    assert direction[1] < -0.95                      # ほぼ真下
    # 節の間 (親の節の先と、子の節の根元) は、どのコマでも 1 ボクセル (高精細で約 0.08) より離れない
    # (最後のコマだけを見ていたときは、揺れの途中で 0.19 離れるコマがあっても通り、CI の Linux では最後のコマで 0.11 になって落ちた)
    for skin in skins:
        for parent, child, point in (("髪1", "髪2", (0, 13.5, 1.4)), ("髪2", "髪3", (0, 11.5, 1.4))):
            a = (skin[fixture.INDEX_ALL[parent]] @ np.array([*point, 1.0]))[:3]
            b = (skin[fixture.INDEX_ALL[child]] @ np.array([*point, 1.0]))[:3]
            assert np.linalg.norm(a - b) < 0.08
    rigid_tip = tip_positions(rigid, bone)[-1]
    assert np.linalg.norm(rigid_tip - tip) > 2


@pytest.mark.parametrize("preset", ["light", "standard", "full"])
def test_hair_does_not_go_into_the_head(doll, preset):
    """前へおじぎしても、髪の剛体は頭 (球) と上半身 (カプセル) の当たりにほとんど入らない。"""
    model, poser, tracks, _pmx, _vmd = doll(lean(-80))
    skins = mmd_physics.simulate(model, poser, tracks, np.arange(0, 61), preset)
    head, torso = fixture.INDEX["頭"], fixture.INDEX["上半身"]
    # 鎖 (light) は剛体の中心ではなく節の先と真ん中で当たりを見るので、少しゆるく見る
    slack = 0.2 if preset == "light" else 0.15
    for skin in skins[::5]:
        centre_head = (skin[head] @ np.array([0, 15.5, 0, 1.0]))[:3]
        top = (skin[torso] @ np.array([0, 13.25, 0, 1.0]))[:3]
        bottom = (skin[torso] @ np.array([0, 10.75, 0, 1.0]))[:3]
        for name, centre in (("髪1", (0, 14.5, 1.4)), ("髪2", (0, 12.5, 1.4)), ("髪3", (0, 10.5, 1.4))):
            hair = (skin[fixture.INDEX_ALL[name]] @ np.array([*centre, 1.0]))[:3]
            assert np.linalg.norm(hair - centre_head) > 1.1 + 0.3 - slack
            along = np.clip(np.dot(hair - bottom, top - bottom) / np.dot(top - bottom, top - bottom), 0, 1)
            assert np.linalg.norm(hair - (bottom + (top - bottom) * along)) > 1.4 + 0.3 - slack


def test_physics_is_repeatable_and_off_matches_plain_poses(doll):
    model, poser, tracks, _pmx, _vmd = doll(lean(50))
    frames = np.arange(0, 31)
    a = mmd_physics.simulate(model, poser, tracks, frames, "light")
    b = mmd_physics.simulate(model, poser, tracks, frames, "light")
    assert np.array_equal(a, b)
    off = mmd_physics.simulate(model, poser, tracks, frames, None)
    plain = rig_anim.pose_song(poser, tracks, frames)
    assert np.allclose(off, plain)


def test_presets():
    assert set(mmd_physics.PRESETS) == {"light", "standard", "full"}
    assert mmd_physics.PRESETS["full"].self_collision and not mmd_physics.PRESETS["light"].self_collision
    assert [mmd_physics.PRESETS[n].solver for n in ("light", "standard", "full")] == ["chain", "rigid", "rigid"]
    steps = [mmd_physics.PRESETS[n].substeps for n in ("light", "standard", "full")]
    assert steps == sorted(steps)


def test_euler_matrix_follows_the_mmd_order():
    """剛体の回転は Y・X・Z の順 (R = Ry · Rx · Rz)。"""
    r = mmd_physics.euler_matrix((0.3, 0.5, 0.7))
    rx = mmd_physics.euler_matrix((0.3, 0, 0))
    ry = mmd_physics.euler_matrix((0, 0.5, 0))
    rz = mmd_physics.euler_matrix((0, 0, 0.7))
    assert np.allclose(r, ry @ rx @ rz)


# --- 板の画素 ---

def test_texels_make_the_images_finer_but_keep_the_quads(doll):
    model, _poser, _tracks, pmx, _vmd = doll(lean(0))
    model, materials = rig.load_model(pmx)
    one = rig.rig_figure(rig.build_rig(model, materials, [], voxels=40, texels=1), figure_blocks=3)
    two = rig.rig_figure(rig.build_rig(model, materials, [], voxels=40, texels=2), figure_blocks=3)
    assert one["quads"] == two["quads"]
    cubes_one = [c for b in one["bones"] for c in b.get("cubes", [])]
    cubes_two = [c for b in two["bones"] for c in b.get("cubes", [])]
    for a, b in zip(cubes_one, cubes_two):
        assert a["size"] == b["size"] and a["origin"] == b["origin"]
        [(face_a, uv_a)] = a["uv"].items()
        [(face_b, uv_b)] = b["uv"].items()
        assert face_a == face_b and uv_b["uv_size"] == [2 * v for v in uv_a["uv_size"]]


def test_a_flat_coloured_face_keeps_its_colour_when_finer(doll):
    """1 色の箱の面は、細かくしても同じ色 (細かい格子でも同じ色の表面が見つかる)。"""
    model, _poser, _tracks, pmx, _vmd = doll(lean(0))
    model, materials = rig.load_model(pmx)
    built = rig.build_rig(model, materials, [], voxels=40, texels=2, merge=False)
    torso = fixture.INDEX["上半身"]
    coarse = rig.cell_quads(built.rest.cells[built.members_v[built.members_p == torso]],
                            built.rest.rgb[built.members_v[built.members_p == torso]], None)
    fine = rig.part_quads(built, torso)
    big = max(range(len(coarse)), key=lambda i: coarse[i].width * coarse[i].height)
    a, b = coarse[big].image, fine[big].image
    assert b.shape[:2] == (2 * a.shape[0], 2 * a.shape[1])
    inner = a[1:-1, 1:-1, :3].reshape(-1, 3)
    finer = b[2:-2, 2:-2, :3].reshape(-1, 3)
    assert np.abs(np.median(inner, axis=0) - np.median(finer, axis=0)).max() <= 2


# --- 表情 ---

MOUTH = [("あ", 0, 0.0), ("あ", 10, 1.0), ("あ", 20, 0.0), ("にやり", 25, 0.0), ("にやり", 30, 1.0)]


def test_mouth_states_follow_the_morph_weights(doll):
    model, _poser, _tracks, pmx, vmd = doll(lean(0, 30), morphs=MOUTH)
    model, materials = rig.load_model(pmx)
    frames = np.arange(0, 31)
    tracks = rig_face.merged_morph_tracks(vmd)
    built = rig.build_rig(model, materials, [], voxels=48)
    patched = rig_face.build_patches(built, model, materials, tracks, frames, states=4, log=lambda _t: None)
    [patch] = patched.patches
    assert patch.morphs == ["あ"]
    assert len(patch.states) == 4 and np.allclose(patch.weights[0], 0)
    # 口を一番開けたフレーム (10) は、重みが一番大きい状態
    assert patch.labels[10] == int(np.argmax(patch.weights[:, 0]))
    assert patch.labels[0] == 0
    # 抜いたボクセルは部品に残っていない
    assert not np.isin(patch.removed, patched.members_v).any()
    # 口を開けた状態の板は、閉じた状態より下まで伸びる
    closed = patch.states[0][0][:, 2].min()
    opened = patch.states[int(np.argmax(patch.weights[:, 0]))][0][:, 2].min()
    assert opened < closed


def test_switch_channel_uses_pre_and_post_keys():
    channel, keys = rig_anim.switch_channel(np.array([0, 0, 1, 1, 0], bool), 10)
    assert keys == 3
    assert channel["0.0"] == [0, 0, 0]
    assert channel["0.2"] == {"pre": [0, 0, 0], "post": [1, 1, 1]}
    assert channel["0.4"] == {"pre": [1, 1, 1], "post": [0, 0, 0]}
    assert rig_anim.switch_channel(np.ones(5, bool), 10) == ([1, 1, 1], 1)
    assert rig_anim.switch_channel(np.zeros(5, bool), 10) == ([0, 0, 0], 1)


def test_the_addon_switches_the_mouth_and_shakes_the_hair(doll, tmp_path):
    keys = lean(60) + [("上半身", 60, (0, 0, 0), (0, 0, 0, 1))]
    _model, _poser, _tracks, pmx, vmd = doll(keys, morphs=MOUTH)
    out = tmp_path / "doll.mcaddon"
    assert convert_image_mode.main([str(pmx), "--motion", str(vmd), "-o", str(out), "--case", "--figure-voxels", "40",
                                    "--figure-blocks", "3", "--physics", "light", "--expression-states", "3"]) == 0
    with zipfile.ZipFile(out) as z:
        files = {name: z.read(name) for name in z.namelist()}
    short = "screen_doll_a"
    geometry = json.loads(files[f"RP/models/entity/{short}.geo.json"])["minecraft:geometry"][0]
    names = [b["name"] for b in geometry["bones"]]
    assert {"e0s0", "e0s1", "e0s2"} <= set(names)
    assert {b["name"]: b.get("parent") for b in geometry["bones"]}["e0s1"] in names
    body = json.loads(files[f"RP/animations/{short}.animation.json"])["animations"]
    [animation] = body.values()
    scales = [animation["bones"][f"e0s{s}"]["scale"] for s in range(3)]
    assert any(isinstance(c, dict) and any(isinstance(v, dict) and "pre" in v for v in c.values()) for c in scales)
    # 揺れる髪のボーン (髪2・髪3) は、踊りで回らなくても動く
    hair = [f"b{fixture.INDEX_ALL[n]}" for n in ("髪2", "髪3")]
    assert any(n in animation["bones"] and isinstance(animation["bones"][n].get("rotation"), dict) for n in hair)


def test_figure_detail_presets():
    args = convert_image_mode.parse_args(["doll.pmx", "-o", "x.mcaddon"])
    assert args.figure_detail == "high" and convert_image_mode.figure_resolution(args) == (256, 2)
    assert convert_image_mode.figure_resolution(args, "standard") == (256, 1)
    assert convert_image_mode.figure_resolution(args, "max") == (384, 2)
    args = convert_image_mode.parse_args(["doll.pmx", "-o", "x.mcaddon", "--figure-voxels", "64", "--figure-texels", "3"])
    assert convert_image_mode.figure_resolution(args) == (64, 3)
    assert args.physics == "standard"


def test_planes_uv_size_matches_the_image():
    image = np.zeros((6, 4, 4), np.uint8)
    quad = planes.Quad(0, 1, 0, 0, 2, 3, image)
    cube = planes.quad_cube(quad, planes.Placement(0, 0, 0), planes.Placing(scale=1.0, centre=(0, 0), offset=(0, 0, 0)))
    assert cube["uv"]["east"]["uv_size"] == [4, 6]


def test_nested_group_morphs_expand_quickly():
    """同じモーフを 2 回ずつ指すグループを 40 段重ねても、すぐ広げ終わる (広げた結果を使い回す)。"""
    import time
    model = mmd.PmxModel()
    model.positions = np.zeros((4, 3), np.float32)
    model.morphs = [{"name": "leaf", "kind": mmd.MORPH_VERTEX, "vertices": np.array([0]),
                     "offsets": np.array([[0.0, 1.0, 0.0]], np.float32)}]
    for level in range(40):
        model.morphs.append({"name": f"g{level}", "kind": mmd.MORPH_GROUP, "members": [(level, 0.5), (level, 0.5)]})
    start = time.perf_counter()
    expanded = mmd._expand_morphs(model, {"g39": 1.0})
    assert time.perf_counter() - start < 1.0
    # 16 段より深い所は使わない (壊れたファイルとみなす)
    assert expanded == {} or expanded[0] == pytest.approx(1.0)
    assert mmd._expand_morphs(model, {"g10": 1.0})[0] == pytest.approx(1.0)


def test_rigid_joint_limits_and_springs():
    """剛体の物理の部品: オイラー角 (Rx · Ry · Rz) の行き来、回転ベクトル、線分どうしの近い点、色分け。"""
    angles = np.array([0.3, -0.4, 0.5])
    q = mmd_physics.quat_from_euler_xyz(angles)
    assert np.allclose(mmd_physics.euler_xyz(q), angles)
    m = mmd_physics.quat_matrices(q)
    rx, ry, rz = (mmd_physics.quat_matrices(mmd_physics.quat_from_euler_xyz(np.eye(3)[k] * angles[k])) for k in range(3))
    assert np.allclose(m, rx @ ry @ rz)
    turn = mmd_physics.quat_from_euler_xyz(np.array([0.0, 0.0, 0.7]))
    assert np.allclose(mmd_physics.rotation_vector(turn), [0, 0, 0.7])
    a, b = mmd_physics._segment_closest(np.array([[0.0, 0, 0]]), np.array([[2.0, 0, 0]]),
                                        np.array([[1.0, 1, -1]]), np.array([[1.0, 1, 1]]))
    assert np.allclose(a, [[1, 0, 0]]) and np.allclose(b, [[1, 1, 0]])
    colours = mmd_physics._colour_groups(np.array([[0, 1], [1, 2], [2, 3], [0, 3]]), np.array([False, True, True, True]))
    for colour in colours:
        bodies = [b for k in colour for b in np.array([[0, 1], [1, 2], [2, 3], [0, 3]])[k] if b != 0]
        assert len(bodies) == len(set(bodies))
