"""モーションキャプチャ (BVH) を MMD の踊り (VMD) にする所 (mvcodec/bvh.py) のテスト。

BVH は CMU のデータベース (cgspeed の BVH) と同じ骨の名前の、T ポーズの小さな人を一から書く。モデルは
tests/mmd_fixture.py の箱の人形 (腕は真横に伸びた T ポーズ、正面は -Z、左は +X)。
"""
import math
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import mmd_fixture as fixture  # noqa: E402
from mvcodec import bvh, mmd  # noqa: E402

# (名前, 親, OFFSET, End Site のずれか None)。腰の高さ 10、脚は 5 + 5、腕は横に 3 + 3
JOINTS = [
    ("Hips", None, (0, 10, 0), None),
    ("LeftUpLeg", "Hips", (1, -0.5, 0), None),
    ("LeftLeg", "LeftUpLeg", (0, -4.5, 0), None),
    ("LeftFoot", "LeftLeg", (0, -4.5, 0), None),
    ("LeftToeBase", "LeftFoot", (0, -0.5, 1), (0, 0, 0.5)),
    ("RightUpLeg", "Hips", (-1, -0.5, 0), None),
    ("RightLeg", "RightUpLeg", (0, -4.5, 0), None),
    ("RightFoot", "RightLeg", (0, -4.5, 0), None),
    ("RightToeBase", "RightFoot", (0, -0.5, 1), (0, 0, 0.5)),
    ("LowerBack", "Hips", (0, 0, 0), None),
    ("Spine", "LowerBack", (0, 2, 0), None),
    ("Spine1", "Spine", (0, 2, 0), None),
    ("Neck", "Spine1", (0, 1, 0), None),
    ("Head", "Neck", (0, 1, 0), (0, 1, 0)),
    ("LeftShoulder", "Spine1", (0.5, 0, 0), None),
    ("LeftArm", "LeftShoulder", (1.5, 0, 0), None),
    ("LeftForeArm", "LeftArm", (3, 0, 0), None),
    ("LeftHand", "LeftForeArm", (3, 0, 0), (1, 0, 0)),
    ("RightShoulder", "Spine1", (-0.5, 0, 0), None),
    ("RightArm", "RightShoulder", (-1.5, 0, 0), None),
    ("RightForeArm", "RightArm", (-3, 0, 0), None),
    ("RightHand", "RightForeArm", (-3, 0, 0), (-1, 0, 0)),
]


def write_bvh(path, frames, frame_time=1 / 120):
    """frames: コマごとの {骨の名前: (Z, X, Y の回転 (度))}、腰の移動は "_pos"。"""
    names = [j[0] for j in JOINTS]
    children = {n: [c[0] for c in JOINTS if c[1] == n] for n in names}
    info = {j[0]: j for j in JOINTS}
    lines = ["HIERARCHY"]

    def emit(name, depth):
        _n, parent, offset, end = info[name]
        pad = "  " * depth
        lines.append(f"{pad}{'ROOT' if parent is None else 'JOINT'} {name}")
        lines.append(pad + "{")
        lines.append(f"{pad}  OFFSET {offset[0]} {offset[1]} {offset[2]}")
        if parent is None:
            lines.append(f"{pad}  CHANNELS 6 Xposition Yposition Zposition Zrotation Xrotation Yrotation")
        else:
            lines.append(f"{pad}  CHANNELS 3 Zrotation Xrotation Yrotation")
        for child in children[name]:
            emit(child, depth + 1)
        if end is not None:
            lines.extend([f"{pad}  End Site", pad + "  {", f"{pad}    OFFSET {end[0]} {end[1]} {end[2]}", pad + "  }"])
        lines.append(pad + "}")

    emit("Hips", 0)
    lines += ["MOTION", f"Frames: {len(frames)}", f"Frame Time: {frame_time}"]
    order = []

    def walk(name):
        order.append(name)
        for child in children[name]:
            walk(child)

    walk("Hips")
    for values in frames:
        row = []
        for name in order:
            if name == "Hips":
                row += list(values.get("_pos", (0, 10, 0)))
            row += list(values.get(name, (0, 0, 0)))
        lines.append(" ".join(f"{v:g}" for v in row))
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def doll(tmp_path):
    pmx, _vmd = fixture.write_doll(tmp_path, [("頭", 0, (0, 0, 0), (0, 0, 0, 1))])
    return pmx


def bone_point(skin, bone, point):
    return (skin[bone] @ np.append(np.asarray(point, np.float64), 1.0))[:3]


def test_read_bvh_and_forward_kinematics(tmp_path):
    path = write_bvh(tmp_path / "a.bvh", [{}, {"LeftArm": (90, 0, 0)}])
    skeleton = bvh.read_bvh(path)
    assert skeleton.names[0] == "Hips" and len(skeleton.names) == len(JOINTS)
    assert skeleton.motion.shape == (2, 6 + 3 * (len(JOINTS) - 1))
    assert skeleton.frame_time == pytest.approx(1 / 120)
    rest, _r, ends = bvh.forward(skeleton, skeleton.motion[0])
    hand = skeleton.names.index("LeftHand")
    assert np.allclose(rest[hand], [8, 14, 0])
    raised, _r, _e = bvh.forward(skeleton, skeleton.motion[1])
    # Z まわり 90 度で、横の腕が上を向く
    assert np.allclose(raised[hand], [2, 20, 0])
    assert np.allclose(ends[hand], [9, 14, 0])


def test_roles_follow_cmu_and_mixamo_names(tmp_path):
    skeleton = bvh.read_bvh(write_bvh(tmp_path / "a.bvh", [{}]))
    roles = bvh.find_roles(skeleton)
    assert skeleton.names[roles["chest"]] == "Spine1"
    assert skeleton.names[roles["r_forearm"]] == "RightForeArm"
    skeleton.names = [f"mixamorig:{n}" for n in skeleton.names]
    assert bvh.find_roles(skeleton)["l_upleg"] == roles["l_upleg"]


def test_raising_the_left_arm_raises_the_dolls_left_arm(tmp_path):
    pmx = doll(tmp_path)
    frames = [{}] * 60 + [{"LeftArm": (90, 0, 0)}] * 61
    path = write_bvh(tmp_path / "raise.bvh", frames)
    out = bvh.bvh_to_vmd(path, pmx, tmp_path / "raise.vmd", log=lambda _t: None)
    model = mmd.read_pmx(pmx)
    poser = mmd.Poser(model)
    tracks = mmd.read_vmd(out)
    assert "左腕" in tracks
    start = poser.pose(tracks, 0.0)
    up = poser.pose(tracks, 30.0)
    arm, elbow = fixture.INDEX["左腕"], fixture.INDEX["左ひじ"]
    shoulder = bone_point(up, arm, (2, 14, 0))
    hand = bone_point(up, elbow, (5, 14, 0))
    direction = (hand - shoulder) / np.linalg.norm(hand - shoulder)
    assert direction[1] > 0.99            # 上を向く
    rest_hand = bone_point(start, elbow, (5, 14, 0))
    assert np.allclose(rest_hand, (5, 14, 0), atol=1e-3)   # 最初は T ポーズのまま


def test_knee_bend_survives_the_leg_ik(tmp_path):
    """ひざを曲げた踊りでも、足ＩＫ が脚を元の伸びた形に戻さない (IK のボーンを足首の位置へ置くキーがある)。"""
    pmx = doll(tmp_path)
    frames = [{}] * 30 + [{"LeftUpLeg": (0, -60, 0), "LeftLeg": (0, 90, 0)}] * 31
    out = bvh.bvh_to_vmd(write_bvh(tmp_path / "knee.bvh", frames), pmx, tmp_path / "knee.vmd", log=lambda _t: None)
    model = mmd.read_pmx(pmx)
    poser = mmd.Poser(model)
    tracks = mmd.read_vmd(out)
    assert "左足ＩＫ" in tracks
    with_ik = poser.pose(tracks, 15.0)
    without = poser.pose(tracks, 15.0, use_ik=False)
    ankle = fixture.INDEX["左足首"]
    a = bone_point(with_ik, ankle, (1, 0.5, 0))
    b = bone_point(without, ankle, (1, 0.5, 0))
    assert np.linalg.norm(a - b) < 0.3
    # 足首は伸ばした形 (y = 0.5) より上にある (ひざが曲がっている)
    assert a[1] > 2


def test_hips_movement_is_scaled_by_leg_length(tmp_path):
    pmx = doll(tmp_path)
    frames = [{"_pos": (0, 10, 0)}] * 30 + [{"_pos": (0, 12, 0)}] * 31
    out = bvh.bvh_to_vmd(write_bvh(tmp_path / "jump.bvh", frames), pmx, tmp_path / "jump.vmd", log=lambda _t: None)
    tracks = mmd.read_vmd(out)
    # BVH の脚 (もも → 足首) は 9、人形の脚は 9.5。2 上がると人形では 2 × 9.5 / 9
    centre = tracks[mmd.CENTRE_BONE]
    assert centre[-1][1][1] == pytest.approx(2 * 9.5 / 9, rel=1e-3)


def test_resampled_to_30_fps(tmp_path):
    pmx = doll(tmp_path)
    out = bvh.bvh_to_vmd(write_bvh(tmp_path / "a.bvh", [{}] * 121), pmx, tmp_path / "a.vmd", log=lambda _t: None)
    frames = [key[0] for key in mmd.read_vmd(out)["左腕"]]
    assert frames == list(range(31))


def test_broken_bvh_is_refused(tmp_path):
    bad = tmp_path / "bad.bvh"
    bad.write_text("HELLO", encoding="utf-8")
    with pytest.raises(ValueError):
        bvh.read_bvh(bad)
    bad.write_text("HIERARCHY ROOT Hips { OFFSET 0 0 0 CHANNELS 3 Xposition Yposition Zposition } MOTION Frames: 0 "
                   "Frame Time: 0.01", encoding="utf-8")
    with pytest.raises(ValueError):
        bvh.read_bvh(bad)
