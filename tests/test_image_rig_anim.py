"""骨で動かす人物のアニメーション (image_mode/rig_anim.py) と、MMD から画像モードのアドオンを作る所のテスト。

モデルと踊りは tests/mmd_fixture.py の箱の人形。ゲームの中の計算は、A20 (Minecraft の実機で決めた回転の決まり) の
とおりに rig_anim.evaluate で行い、モデルを Poser で変形した正しい姿勢と比べる。
"""
import json
import math
import shutil
import sys
import wave
import zipfile
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import convert_image_mode  # noqa: E402
from mmd_fixture import INDEX, write_doll  # noqa: E402
from image_mode import budget, rig, rig_anim  # noqa: E402
from mvcodec import mmd  # noqa: E402

S45, C45 = math.sin(math.pi / 4), math.cos(math.pi / 4)
# 左腕を 90 度上げ、センターを 2 上げて 1 前へ (その場で踊らせるので前は消える)。足は IK で床に残るので、ひざが曲がる
DANCE = [("左腕", 0, (0, 0, 0), (0, 0, 0, 1)), ("左腕", 30, (0, 0, 0), (0, 0, S45, C45)),
         ("左足ＩＫ", 0, (0, 0, 0), (0, 0, 0, 1)), ("左足ＩＫ", 30, (0, 3, -1), (0, 0, 0, 1)),
         ("センター", 0, (0, 0, 0), (0, 0, 0, 1)), ("センター", 30, (0, 2, 1), (0, 0, 0, 1))]
ID = "animation.test.doll.dance"


@pytest.fixture
def doll(tmp_path):
    pmx, vmd = write_doll(tmp_path, DANCE)
    model, materials = rig.load_model(pmx)
    return model, materials, mmd.read_vmd(vmd), pmx, vmd


def unit(axis):
    return np.eye(3)[axis]


# --- 回転の決まり (A20) ---

def test_game_rotation_follows_the_on_device_result():
    """A20 で実機で読んだ向き: rotation [90, 0, 0] で奥 (+y) の腕が上 (+z) へ、上の腕が手前 (−y) へ。
    [0, 90, 0] で右 (+x) の腕が手前 (−y) へ、奥の腕が右へ (上下の軸の周りに −90 度)。"""
    r = rig_anim.game_rotation([90, 0, 0])
    assert np.allclose(r @ unit(1), unit(2)) and np.allclose(r @ unit(2), -unit(1))
    r = rig_anim.game_rotation([0, 90, 0])
    assert np.allclose(r @ unit(0), -unit(1)) and np.allclose(r @ unit(1), unit(0))
    r = rig_anim.game_rotation([0, 0, 90])
    assert np.allclose(r @ unit(1), unit(1)) and np.allclose(r @ unit(0), -unit(2))
    # 順番は 1 つ目 → 2 つ目 → 3 つ目
    both = rig_anim.game_rotation([90, 0, 90])
    assert np.allclose(both, rig_anim.game_rotation([0, 0, 90]) @ rig_anim.game_rotation([90, 0, 0]))


def test_euler_angles_give_back_the_same_rotation():
    rng = np.random.default_rng(1)
    values = rng.uniform(-180, 180, (200, 3))
    values[:10, 1] = 90   # ジンバルロック
    values[10:20, 1] = -90
    matrices = rig_anim.game_rotation(values)
    back = rig_anim.to_game_values(rig_anim.euler_from_matrix(matrices))
    assert np.allclose(rig_anim.game_rotation(back), matrices, atol=1e-9)


def test_continuous_euler_angles_do_not_jump():
    """2 回転 (720 度) する回転を 5 度ずつ並べても、隣の時刻の値は 5 度ほどしか変わらない (±180 度で跳ばない)。"""
    angles = np.radians(np.arange(0, 725, 5))
    axis = np.array([0.3, 0.8, -0.5])
    axis /= np.linalg.norm(axis)
    k = np.array([[0, -axis[2], axis[1]], [axis[2], 0, -axis[0]], [-axis[1], axis[0], 0]])
    matrices = np.array([np.eye(3) + math.sin(a) * k + (1 - math.cos(a)) * k @ k for a in angles])[:, None]
    euler = rig_anim.continuous_euler(matrices)
    steps = np.degrees(np.abs(np.diff(euler[:, 0], axis=0))).max()
    assert steps < 15
    assert np.allclose(rig_anim.game_rotation(rig_anim.to_game_values(euler[:, 0])), matrices[:, 0], atol=1e-9)


# --- 書き出したアニメーションで、正しい姿勢に戻るか ---

def built_motion(doll, voxels=32):
    model, materials, tracks, _pmx, _vmd = doll
    poser = mmd.Poser(model)
    frames = rig_anim.song_frames(tracks)
    skins = rig_anim.pose_song(poser, tracks, frames)
    built = rig.build_rig(model, materials, [tracks], voxels=voxels, posed=[skins])
    motion = rig_anim.Motion(built, skins, poser)
    return built, motion, skins, poser


def test_the_exported_animation_reproduces_the_dance(doll):
    built, motion, skins, poser = built_motion(doll)
    movers = motion.movers()
    assert movers == [INDEX["センター"]]  # 上下に動くのはセンターだけ (前後はその場で踊らせるので消える)
    figure = rig.rig_figure(built, figure_blocks=4.0, movers=movers, extent=motion.extent)
    placing = figure["rig"]["placing"]
    animation, stats = rig_anim.build_animation(motion, placing, ID, movers=movers)
    assert stats["rotation_keys"] < stats["raw_keys"] / 3
    worst = 0.0
    for f in range(len(skins)):
        game = rig_anim.evaluate(animation, ID, motion, placing, f / rig_anim.ANIMATION_RATE)
        skin = skins[f].astype(np.float64).copy()
        skin[:, :3, 3] -= rig_anim.centre_shift(poser, skin)
        truth = rig_anim.voxel_transforms(built.grid, skin[built.bones])
        for k, bone in enumerate(built.bones):
            cells = built.rest.cells[built.members_v[built.members_p == bone]]
            points = np.concatenate([cells, cells + 1]).astype(np.float64)
            a = points @ game[k, :3, :3].T + game[k, :3, 3]
            b = points @ truth[k, :3, :3].T + truth[k, :3, 3]
            worst = max(worst, float(np.linalg.norm(a - b, axis=1).max()))
    # キーを落としたずれは、ボーン 1 本で 0.25 ボクセル以下。親子 2 段 (足 → ひざ) でも 0.5 以下
    assert worst <= 0.5
    # 移動のボーンは geometry で回るボーンの親になり、上へ 2 (MMD) × 拡大の分だけ動く
    bones = {b["name"]: b for b in figure["bones"]}
    assert bones["b0"]["parent"] == "m0" and "parent" not in bones["m0"] and "cubes" not in bones["m0"]
    position = animation["animations"][ID]["bones"]["m0"]["position"]
    last = position[max(position, key=float)]
    assert last[0] == 0 and last[2] == 0
    assert last[1] == pytest.approx(2 * built.grid.scale * placing.scale, abs=1e-2)


def test_a_figure_that_does_not_move_has_no_keys(doll):
    model, materials, _tracks, _pmx, _vmd = doll
    poser = mmd.Poser(model)
    skins = rig_anim.pose_song(poser, {}, rig_anim.song_frames({}, seconds=1.0))
    built = rig.build_rig(model, materials, [], voxels=32)
    motion = rig_anim.Motion(built, skins, poser)
    figure = rig.rig_figure(built, figure_blocks=4.0)
    animation, stats = rig_anim.build_animation(motion, figure["rig"]["placing"], ID)
    assert animation["animations"][ID]["bones"] == {} and stats["movers"] == 0
    assert animation["animations"][ID]["animation_length"] == 1


def test_the_case_fits_the_dance_not_only_the_rest_pose(doll):
    """腕を上げる踊りなら、人物の高さ (ケースの高さ) は初期姿勢より高くなる。"""
    built, motion, _skins, _poser = built_motion(doll)
    still = rig.rig_figure(built, figure_blocks=4.0)
    dancing = rig.rig_figure(built, figure_blocks=4.0, extent=motion.extent)
    assert dancing["size_blocks"][2] > still["size_blocks"][2]


# --- MMD から画像モードのアドオン ---

def load(addon):
    with zipfile.ZipFile(addon) as z:
        return {name: z.read(name) for name in z.namelist()}


def videos_of(files):
    text = files["BP/scripts/image_videos.js"].decode("utf-8")
    return json.loads("[" + text.split("IMAGE_VIDEOS = [", 1)[1].rsplit("];", 1)[0] + "]")


def test_a_mmd_model_with_a_dance_builds_a_bone_driven_addon(doll, tmp_path):
    _model, _materials, _tracks, pmx, vmd = doll
    out = tmp_path / "doll.mcaddon"
    assert convert_image_mode.main([str(pmx), "--motion", str(vmd), "-o", str(out), "--case", "--figure-voxels", "32",
                                    "--figure-blocks", "3"]) == 0
    files = load(out)
    short = "screen_doll_a"
    entity = json.loads(files[f"RP/entity/{short}.entity.json"])["minecraft:client_entity"]["description"]
    assert entity["animations"] == {"dance": "animation.bvpimg.screen_doll_a.dance"}
    assert entity["scripts"]["animate"] == ["dance"]
    assert any(line.startswith("v.secs = ") for line in entity["scripts"]["pre_animation"])
    animation = json.loads(files[f"RP/animations/{short}.animation.json"])
    body = animation["animations"]["animation.bvpimg.screen_doll_a.dance"]
    assert body["anim_time_update"] == "math.clamp(v.secs, 0, 0.999)"
    assert {"b4", "m0"} <= set(body["bones"])
    controllers = json.loads(files[f"RP/render_controllers/{short}.render_controllers.json"])["render_controllers"]
    assert controllers[f"controller.render.bvpimg.{short}"]["part_visibility"] == [{"*": "q.property('bvpimg:state') != 0"}]
    geometry = json.loads(files[f"RP/models/entity/{short}.geo.json"])["minecraft:geometry"][0]
    names = [b["name"] for b in geometry["bones"]]
    assert names[:2] == ["m0", "b0"] and len(names) == len(set(names))
    parents = {b["name"]: b.get("parent") for b in geometry["bones"]}
    assert all(p is None or p in names for p in parents.values())
    [video] = videos_of(files)
    # 1 秒の踊りを、1 秒 20 コマの時計で数える。繰り返さない
    assert video["fps"] == 20 and video["frame_count"] == 21 and "loop" not in video
    assert video["glass"]["master"] == "figure" and video["glass"]["figure"]["rig"]["bones"] == 5
    assert video["audio_chunks"] == 0


def test_mmd_options_need_a_mmd_model(doll, tmp_path):
    _model, _materials, _tracks, pmx, vmd = doll
    other = tmp_path / "x.vox"
    other.write_bytes(b"")
    assert convert_image_mode.main([str(other), "--motion", str(vmd), "-o", str(tmp_path / "a.mcaddon")]) == 1
    assert convert_image_mode.main([str(pmx), "--motion", str(vmd), "--start", "1", "-o", str(tmp_path / "b.mcaddon")]) == 1
    assert convert_image_mode.main([str(pmx), "--motion", str(tmp_path / "none.vmd"), "-o", str(tmp_path / "c.mcaddon")]) == 1
    assert convert_image_mode.main([str(pmx), "--solid", "views", "-o", str(tmp_path / "d.mcaddon")]) == 1
    assert convert_image_mode.main([str(pmx), "--priority", "quality", "-o", str(tmp_path / "e.mcaddon")]) == 1


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg が必要")
def test_a_song_sets_the_length_and_the_dance_holds_its_last_pose(doll, tmp_path):
    _model, _materials, _tracks, pmx, vmd = doll
    song = tmp_path / "song.wav"
    with wave.open(str(song), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\0\0" * 8000 * 6)  # 6 秒
    out = tmp_path / "doll.mcaddon"
    assert convert_image_mode.main([str(pmx), "--motion", str(vmd), "--audio", str(song), "-o", str(out),
                                    "--figure-voxels", "32", "--audio-chunk-seconds", "4"]) == 0
    files = load(out)
    [video] = videos_of(files)
    assert video["frame_count"] == 121 and video["audio_chunks"] == 2
    animation = json.loads(files["RP/animations/screen_doll_a.animation.json"])
    body = animation["animations"]["animation.bvpimg.screen_doll_a.dance"]
    assert body["animation_length"] == 6
    # 踊りは 1 秒で終わり、その後は最後の姿勢のまま (キーは 1 秒の後ろに 1 つだけ)
    times = sorted(float(t) for t in body["bones"]["b4"]["rotation"])
    assert times[-1] == 6.0 and all(t <= 1.0 for t in times[:-1])


def test_budget_plan_leaves_room_for_the_figure_and_the_case():
    plain = budget.plan([188.0], 256, 144, 10, "quality", 2)
    reserved = budget.plan([188.0], 256, 144, 10, "quality", 2, reserved_bytes=100 * 1024 * 1024)
    assert reserved["budget_bytes"] == plain["budget_bytes"] - 100 * 1024 * 1024
    assert reserved["bytes"] <= reserved["budget_bytes"] < plain["bytes"] or reserved["fps"] < plain["fps"]


def test_the_figure_test_pack_has_the_five_variants(doll, tmp_path):
    """重ね代 0・1・2 と陰の 3 通りを見比べる試験用のアドオン (scripts/make_rig_figure_test_pack.py)。"""
    import importlib.util

    _model, _materials, _tracks, pmx, vmd = doll
    script = ROOT / "scripts" / "make_rig_figure_test_pack.py"
    spec = importlib.util.spec_from_file_location("make_rig_figure_test_pack", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    out = tmp_path / "figure_test.mcaddon"
    assert module.main([str(pmx), str(vmd), str(out), "--voxels", "32", "--seconds", "2"]) == 0
    files = load(out)
    videos = {v["id"]: v for v in videos_of(files)}
    assert set(videos) == {"j0", "j1", "j2", "sn", "sw"}
    assert videos["sw"]["lighting"] == "world" and videos["j1"]["lighting"] == "bright"
    assert all(v["loop"] for v in videos.values())
    controllers = json.loads(files["RP/render_controllers/screen_sw_a.render_controllers.json"])["render_controllers"]
    assert "ignore_lighting" not in controllers["controller.render.bvpimg.screen_sw_a"]
    body = json.loads(files["RP/animations/screen_j0_a.animation.json"])["animations"]
    assert next(iter(body.values()))["anim_time_update"].startswith("math.mod(v.secs, ")


def test_inputs_can_be_left_out_only_by_callers_that_pass_figures(tmp_path):
    with pytest.raises(SystemExit):
        convert_image_mode.parse_args(["-o", str(tmp_path / "a.mcaddon")])
    with pytest.raises(SystemExit):
        convert_image_mode.parse_args([], allow_no_inputs=True)  # 出力の場所が決まらない
    args = convert_image_mode.parse_args(["-o", str(tmp_path / "a.mcaddon")], allow_no_inputs=True)
    with pytest.raises(convert_image_mode.ConvertError):
        convert_image_mode.build(args)
