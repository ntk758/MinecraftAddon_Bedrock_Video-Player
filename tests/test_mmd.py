"""MMD (.pmx + .vmd) から立体のアニメ (.vox) を作るテスト (mvcodec/mmd.py)。

モデルと踊りは tests/mmd_fixture.py が一から書き出す箱の人形 (実在のモデルは使わない)。
"""
import math
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from mmd_fixture import BONES, BOXES, INDEX, write_doll  # noqa: E402
from mvcodec.mmd import Poser, _bezier, mmd_to_vox, read_pmx, read_vmd, sample_track, skin_vertices  # noqa: E402
from mvcodec.volume import load_vox_frames  # noqa: E402

S45, C45 = math.sin(math.pi / 4), math.cos(math.pi / 4)


def bone_position(model, skin, name):
    i = INDEX[name]
    return skin[i, :3, :3] @ model.bones[i]["position"] + skin[i, :3, 3]


@pytest.fixture
def doll(tmp_path):
    keys = [("左腕", 0, (0, 0, 0), (0, 0, 0, 1)), ("左腕", 30, (0, 0, 0), (0, 0, S45, C45)),
            ("左足ＩＫ", 0, (0, 0, 0), (0, 0, 0, 1)), ("左足ＩＫ", 30, (0, 3, -1), (0, 0, 0, 1)),
            ("センター", 0, (0, 0, 0), (0, 0, 0, 1)), ("センター", 30, (0, 0, 0), (0, 0, 0, 1))]
    pmx, vmd = write_doll(tmp_path, keys)
    return read_pmx(pmx), read_vmd(vmd), pmx, vmd


def test_reads_model_and_motion(doll):
    model, tracks, _, _ = doll
    assert len(model.positions) == 8 * len(BOXES)
    assert len(model.faces) == 12 * len(BOXES)
    assert [b["name"] for b in model.bones] == [b[0] for b in BONES]
    assert model.bones[INDEX["左足ＩＫ"]]["ik"]["target"] == INDEX["左足首"]
    assert model.bones[INDEX["左ひざD"]]["append_parent"] == INDEX["左ひざ"]
    assert set(tracks) == {"左腕", "左足ＩＫ", "センター"}


def test_rest_pose_does_not_move_vertices(doll):
    model, _, _, _ = doll
    skin = Poser(model).pose({}, 0)
    assert np.allclose(skin_vertices(model, skin), model.positions, atol=1e-5)


def test_rotation_moves_the_child_bone(doll):
    """左腕を Z 軸まわりに 90 度回すと、ひじが肩の真上へ来る。途中のフレームは補間 (直線の補間曲線)。"""
    model, tracks, _, _ = doll
    poser = Poser(model)
    assert np.allclose(bone_position(model, poser.pose(tracks, 30), "左ひじ"), (2, 17, 0), atol=1e-4)
    halfway = bone_position(model, poser.pose(tracks, 15), "左ひじ")
    assert np.allclose(halfway, (2 + 3 * math.cos(math.pi / 4), 14 + 3 * math.sin(math.pi / 4), 0), atol=1e-3)


def test_leg_ik_reaches_the_target_and_bends_the_knee_forward(doll):
    model, tracks, _, _ = doll
    skin = Poser(model).pose(tracks, 30)
    assert np.linalg.norm(bone_position(model, skin, "左足首") - (1, 3.5, -1)) < 0.01
    knee = bone_position(model, skin, "左ひざ")
    assert knee[2] < -1  # 膝は正面 (-Z) へ曲がる
    # 付与: 「左ひざD」は左ひざと同じに回る
    assert np.allclose(skin[INDEX["左ひざD"]], skin[INDEX["左ひざ"]], atol=1e-6)
    # 右足は動かない
    assert np.allclose(bone_position(model, skin, "右足首"), (-1, 0.5, 0), atol=1e-4)


def test_bezier_interpolation():
    assert _bezier(20, 20, 107, 107, 0.3) == pytest.approx(0.3)
    # ゆっくり始まる曲線は、前半は直線より遅い
    assert _bezier(127, 0, 127, 127, 0.3) < 0.3
    keys = [(0, np.zeros(3), np.array([0, 0, 0, 1.0]), bytes([20] * 8 + [107] * 8) * 4),
            (10, np.array([10.0, 0, 0]), np.array([0, 0, 0, 1.0]), bytes([20] * 8 + [107] * 8) * 4)]
    position, _ = sample_track(keys, 4)
    assert position[0] == pytest.approx(4)


def test_mmd_to_vox_makes_a_front_facing_animation(doll, tmp_path):
    _, _, pmx, vmd = doll
    frames, size = mmd_to_vox(pmx, vmd, tmp_path / "doll.vox", height=40, fps=10)
    assert frames == 11  # 30 フレーム (1 秒) を 10 fps で
    vox_frames, palette = load_vox_frames(tmp_path / "doll.vox")
    assert len(vox_frames) == frames
    first = vox_frames[0]
    # load_vox_frames は (大きさ, ボクセル (x, y, z, 色)) を返す
    (sx, sy, sz), voxels = first
    assert (sx, sy, sz) == size
    heights = voxels[:, 2]
    assert 38 <= heights.max() - heights.min() + 1 <= 42  # 人形の高さは 40 ブロックくらい
    # 顔の目印 (赤) は頭の正面 = y の小さい側にある
    rgb = palette[voxels[:, 3] - 1, :3].astype(int)
    red = (rgb[:, 0] > 180) & (rgb[:, 1] < 80)
    head = voxels[:, 2] >= heights.max() - 6
    assert red.any()
    assert voxels[red, 1].mean() < voxels[head & ~red, 1].mean()


def test_gui_builds_an_anamorphic_addon_from_mmd(doll, tmp_path):
    """GUI の組み立て (画面なし): 動画リストの .pmx を、選んだ踊り (.vmd) で立体にして、だまし絵のアドオンを作る。"""
    import json
    import queue
    import zipfile

    import video_player_gui as gui
    from gui_i18n import Translator

    _, _, pmx, vmd = doll

    class Var:
        def __init__(self, value):
            self.value = value

        def get(self):
            return self.value

    class Headless:
        def __init__(self):
            self.tr = Translator("ja")
            self.messages = queue.Queue()
            self.keyframe_interval_var = Var(30)
            self.perceptual_var = Var(True)

        def _run(self, command):
            gui.PackBuilderApp._run(self, command)

        def _mmd_to_vox(self, *args):
            return gui.PackBuilderApp._mmd_to_vox(self, *args)

        def _extract_audio(self, *args):
            return gui.PackBuilderApp._extract_audio(self, *args)

    app = Headless()
    output = tmp_path / "mmd.mcaddon"
    entry = {"file_path": str(pmx), "video_id": "doll", "thumb_sec": 0, "motion": str(vmd)}
    gui.PackBuilderApp._build_pack(app, [entry], output, "MMD", "badapple", 128, 128, 10, None, "auto", "none",
                                   "cpu", "screen", 16, None, True, None)
    log = []
    while not app.messages.empty():
        log.append(str(app.messages.get()))
    assert output.is_file(), "\n".join(log)
    with zipfile.ZipFile(output) as z:
        text = z.read("BP/scripts/frames_doll.js").decode("utf-8")
    data = json.loads(text[text.index("{"):text.rindex("}") + 1])
    assert data["view"] == "anamorphic" and data["frame_count"] == 11
    # 高さの設定 (128) は上限の 64 ブロックにする (初期姿勢の頭の上が 64。この踊りは腕を頭より上へ上げるので、
    # 立体の高さは 64 × 18.4 / 16.5 ≈ 72 + 余白)
    assert 70 <= data["height"] <= 78
