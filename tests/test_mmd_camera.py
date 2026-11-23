"""MMD のカメラ (VMD のカメラのキー) を読み、画像モードの人物の座標の目と注視点にする所のテスト。
ゲームの中でプレイヤーの視点を動かす所 (image_mode/image_main.js) は tests/test_image_main.py のモックで確かめる。"""
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
from mvcodec import mmd  # noqa: E402

STILL = [("頭", 0, (0, 0, 0), (0, 0, 0, 1)), ("頭", 30, (0, 0, 0), (0, 0, 0, 1))]
# 0 フレーム: 正面から (既定)。30 フレーム: 横へ回る。31 フレーム: 1 フレームで切り替え (カット)
CAMERAS = [(0, -45.0, (0, 10, 0), (0, 0, 0), 30), (30, -45.0, (0, 10, 0), (0, math.pi / 2, 0), 30),
           (31, -20.0, (0, 12, 0), (0, 0, 0), 40)]


def test_read_and_sample_the_camera(tmp_path):
    _pmx, vmd = fixture.write_doll(tmp_path, STILL, cameras=CAMERAS)
    keys = mmd.read_vmd_camera(vmd)
    assert [k[0] for k in keys] == [0, 30, 31]
    assert keys[0][1] == -45.0 and keys[2][5] == 40
    # ボーン・表情のキーの読み方は変わらない
    assert set(mmd.read_vmd(vmd)) == {"頭"} and mmd.read_vmd_morphs(vmd) == {}
    centre, rotation, distance, fov = mmd.sample_camera(keys, 15)
    assert np.allclose(centre, (0, 10, 0)) and rotation[1] == pytest.approx(math.pi / 4) and distance == -45
    # 1 フレームの間はカット (補間しない)
    assert mmd.sample_camera(keys, 30.5)[2] == -45
    assert mmd.sample_camera(keys, 99)[2] == -20 and mmd.sample_camera(keys, 99)[3] == 40


def test_camera_eye():
    assert np.allclose(mmd.camera_eye((0, 10, 0), (0, 0, 0), -45), (0, 10, -45))   # 正面 (-Z) から
    side = mmd.camera_eye((0, 0, 0), (0, math.pi / 2, 0), -10)
    assert abs(side[0]) == pytest.approx(10) and side[2] == pytest.approx(0, abs=1e-9)   # 横へ回る
    up = mmd.camera_eye((0, 0, 0), (math.radians(30), 0, 0), -10)
    assert np.linalg.norm(up) == pytest.approx(10) and abs(up[1]) == pytest.approx(5)


def test_a_vmd_without_a_camera_reads_empty(tmp_path):
    _pmx, vmd = fixture.write_doll(tmp_path, STILL)
    assert mmd.read_vmd_camera(vmd) == []


def test_the_camera_goes_into_the_addon_in_figure_coordinates(tmp_path):
    pmx, vmd = fixture.write_doll(tmp_path, STILL, cameras=CAMERAS)
    out = tmp_path / "doll.mcaddon"
    assert convert_image_mode.main([str(pmx), "--motion", str(vmd), "-o", str(out), "--case", "--figure-voxels", "32",
                                    "--figure-blocks", "3", "--physics", "off"]) == 0
    with zipfile.ZipFile(out) as z:
        text = z.read("BP/scripts/image_videos.js").decode("utf-8")
    [video] = json.loads("[" + text.split("IMAGE_VIDEOS = [", 1)[1].rsplit("];", 1)[0] + "]")
    camera = video["glass"]["figure"]["rig"]["camera"]
    assert camera["rate"] == 20 and len(camera["points"]) == 21   # 1 秒の踊り
    eye, target = np.array(camera["points"][0][:3]), np.array(camera["points"][0][3:])
    # 正面から: 目は注視点の -z (人物の正面) 側、同じ高さ。人物の高さ 3 ブロックに合わせた大きさ
    assert eye[2] < target[2] and eye[1] == pytest.approx(target[1], abs=1e-3)
    assert abs(eye[0] - target[0]) < 1e-3
    # 30 フレーム (1 秒) では横から (x がずれて z がそろう)
    eye, target = np.array(camera["points"][20][:3]), np.array(camera["points"][20][3:])
    assert abs(eye[0] - target[0]) > 1 and abs(eye[2] - target[2]) < 0.05


def test_a_separate_camera_vmd_is_used(tmp_path):
    pmx, vmd = fixture.write_doll(tmp_path, STILL)
    camera = tmp_path / "camera.vmd"
    camera.write_bytes(fixture.vmd_bytes([], cameras=CAMERAS))
    out = tmp_path / "doll.mcaddon"
    assert convert_image_mode.main([str(pmx), "--motion", str(vmd), "-o", str(out), "--case", "--figure-voxels", "32",
                                    "--figure-blocks", "3", "--physics", "off", "--camera-motion", str(camera)]) == 0
    with zipfile.ZipFile(out) as z:
        assert '"camera"' in z.read("BP/scripts/image_videos.js").decode("utf-8")
    missing = tmp_path / "none.vmd"
    assert convert_image_mode.main([str(pmx), "--motion", str(vmd), "-o", str(out), "--case", "--figure-voxels", "32",
                                    "--camera-motion", str(missing)]) != 0


def test_pitch_still_works_from_the_side():
    """横から (y を 90 度回して) 見ても、x の角度で上から見下ろせる (左右の回転が一番外)。"""
    side = mmd.camera_eye((0, 0, 0), (0, math.pi / 2, 0), -10)
    above = mmd.camera_eye((0, 0, 0), (math.radians(30), math.pi / 2, 0), -10)
    assert abs(side[1]) < 1e-9 and abs(above[1]) == pytest.approx(5)
    assert np.sign(above[0]) == np.sign(side[0])
