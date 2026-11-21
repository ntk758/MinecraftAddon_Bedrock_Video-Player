"""2026-11-19 の全体の監査で見つけた問題の、再発を防ぐテスト (docs/audit/2026-11-19-progress.md の AUD2-*)。

壊れた MMD のファイルは、tests/mmd_fixture.py の人形 (実在のモデルは使わない) の決まった場所を書き換えて作る。
直す前は固まった (終わらなかった) ものがあるので、そういう呼び出しは別のスレッドで動かし、時間内に終わることも確かめる。
"""
import math
import struct
import sys
import threading
import tracemalloc
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mmd_fixture  # noqa: E402
import convert_image_mode as cim  # noqa: E402
from image_mode import glass, rig, rig_anim  # noqa: E402
from mvcodec import mmd  # noqa: E402

DANCE = [("センター", 0, (0, 0, 0), (0, 0, 0, 1)), ("センター", 30, (0, -2, 0), (0, 0, 0, 1)),
         ("左足ＩＫ", 0, (0, 0, 0), (0, 0, 0, 1)), ("左足ＩＫ", 30, (0, 3, -1), (0, 0, 0, 1))]


def _finishes(function, seconds=20.0):
    """function() を別のスレッドで呼び、(終わったか, 戻り値か例外) を返す。直す前は終わらなかった呼び出しに使う。"""
    result = {}

    def run():
        try:
            result["value"] = function()
        except BaseException as error:  # noqa: BLE001 (呼んだ側で中身を確かめる)
            result["error"] = error

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(seconds)
    return not thread.is_alive(), result


def _offsets():
    """mmd_fixture.pmx_bytes() の中の、書き換える所のバイトの位置。"""
    texts = sum(4 + len(s.encode("utf-16-le")) for s in ("テスト人形", "test doll", "", ""))
    vcount = 17 + texts
    vstart = vcount + 4
    fcount = vstart + 8 * len(mmd_fixture.BOXES) * (32 + 1 + 4 + 4)
    tcount = fcount + 4 + 6 * 2 * 3 * len(mmd_fixture.BOXES) * 4
    return {"vcount": vcount, "vstart": vstart, "fcount": fcount, "tcount": tcount}


def _write(tmp_path, data, name="doll.pmx"):
    path = tmp_path / name
    path.write_bytes(bytes(data))
    return path


def _doll(tmp_path, keys=None):
    return mmd_fixture.write_doll(tmp_path, keys or DANCE)


# --- AUD2-01: 長さが負の文字列で固まらない・数が大きすぎるファイルで大きな配列を作らない ---

def test_negative_text_length_is_rejected_instead_of_looping(tmp_path):
    data = bytearray(mmd_fixture.pmx_bytes())
    at = _offsets()["tcount"]
    struct.pack_into("<i", data, at, 0x7FFFFFFF)   # テクスチャの数
    struct.pack_into("<i", data, at + 4, -4)       # 最初のテクスチャの名前の長さ (負)
    done, result = _finishes(lambda: mmd.read_pmx(_write(tmp_path, data)))
    assert done, "read_pmx が終わらない (負の長さで読む位置が戻っている)"
    assert isinstance(result.get("error"), ValueError)
    assert "壊れ" in str(result["error"])


def test_reader_never_moves_backwards():
    reader = mmd._Reader(b"\x00" * 8)
    with pytest.raises(ValueError):
        reader.take(-4)
    assert reader.pos == 0


def test_counts_larger_than_the_file_are_rejected_before_allocating(tmp_path):
    data = bytearray(mmd_fixture.pmx_bytes())
    struct.pack_into("<i", data, _offsets()["vcount"], 0x7FFFFFFF)   # 頂点の数 (ファイルに入りきらない)
    path = _write(tmp_path, data)
    tracemalloc.start()
    try:
        with pytest.raises(ValueError, match="壊れ"):
            mmd.read_pmx(path)
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 64 * 2 ** 20, f"読む前に {peak / 2 ** 20:.0f} MiB 確保した"


def test_vmd_key_count_larger_than_the_file_is_rejected(tmp_path):
    data = bytearray(mmd_fixture.vmd_bytes(DANCE))
    struct.pack_into("<I", data, 50, 0xFFFFFFFF)
    path = _write(tmp_path, data, "dance.vmd")
    with pytest.raises(ValueError, match="壊れ"):
        mmd.read_vmd(path)


# --- AUD2-02: ボーンの親子が輪になった PMX で固まらない ---

@pytest.mark.parametrize("bone, parent", [(9, 9), (9, 10)])   # 左ひざが自分の親 / 左ひざ <-> 左足首
def test_bone_parent_loops_are_rejected(tmp_path, monkeypatch, bone, parent):
    bones = list(mmd_fixture.BONES)
    name, position, _parent, layer = bones[bone]
    bones[bone] = (name, position, parent, layer)
    monkeypatch.setattr(mmd_fixture, "BONES", bones)
    pmx, vmd = mmd_fixture.write_doll(tmp_path, DANCE)
    model = mmd.read_pmx(pmx)
    with pytest.raises(ValueError, match="輪"):
        mmd.Poser(model)
    done, result = _finishes(lambda: mmd.mmd_to_vox(pmx, vmd, tmp_path / "out.vox", height=16, fps=5, duration=1))
    assert done, "mmd_to_vox が終わらない"
    assert isinstance(result.get("error"), ValueError)


# --- AUD2-03: 踊り・曲の長さの上限 ---

def test_dance_with_a_far_away_key_is_rejected_quickly(tmp_path):
    keys = DANCE + [("センター", 4_000_000_000, (0, 1, 0), (0, 0, 0, 1))]
    pmx, vmd = _doll(tmp_path, keys)
    tracks = mmd.read_vmd(vmd)
    done, result = _finishes(lambda: rig_anim.song_frames(tracks, rig_anim.ANIMATION_RATE))
    assert done and isinstance(result.get("error"), ValueError)
    assert "長すぎ" in str(result["error"])
    done, result = _finishes(lambda: mmd.mmd_to_vox(pmx, vmd, tmp_path / "out.vox", height=16, fps=5))
    assert done and isinstance(result.get("error"), ValueError)
    # 長さを決めれば、その長さだけ作れる (ブロックモード・ブラウザ版の「長さ」)
    frames, _size = mmd.mmd_to_vox(pmx, vmd, tmp_path / "short.vox", height=16, fps=5, duration=1)
    assert frames == 6


def test_song_longer_than_the_limit_is_rejected():
    limit = mmd.MAX_MOTION_SECONDS
    assert len(rig_anim.song_frames({}, 30, seconds=limit)) == int(limit * 30) + 1
    with pytest.raises(ValueError, match="長すぎ"):
        rig_anim.song_frames({}, 30, seconds=limit + 1)


def test_image_mode_reports_a_far_away_key_as_an_error(tmp_path, capsys):
    keys = DANCE + [("センター", 4_000_000_000, (0, 1, 0), (0, 0, 0, 1))]
    pmx, vmd = _doll(tmp_path, keys)
    done, result = _finishes(lambda: cim.main([str(pmx), "--motion", str(vmd), "--figure-voxels", "24",
                                               "-o", str(tmp_path / "out.mcaddon")]))
    assert done and result.get("value") == 1
    assert "長すぎ" in capsys.readouterr().err


# --- AUD2-04: 壊れた PMX は ValueError (IndexError のトレースバックにしない) ---

def test_too_few_header_values_are_rejected(tmp_path):
    data = mmd_fixture.pmx_bytes()
    data = data[:8] + bytes([3]) + data[9:12] + data[17:]
    with pytest.raises(ValueError, match="壊れ"):
        mmd.read_pmx(_write(tmp_path, data))


def test_unknown_index_size_is_rejected(tmp_path):
    data = bytearray(mmd_fixture.pmx_bytes())
    data[11] = 3   # 頂点の番号の大きさ (1・2・4 のどれか)
    with pytest.raises(ValueError, match="壊れ"):
        mmd.read_pmx(_write(tmp_path, data))


def test_face_vertex_index_out_of_range_is_rejected(tmp_path):
    data = bytearray(mmd_fixture.pmx_bytes())
    struct.pack_into("<i", data, _offsets()["fcount"] + 4, 1_000_000)
    with pytest.raises(ValueError, match="壊れ"):
        mmd.read_pmx(_write(tmp_path, data))


def test_vertex_bone_index_out_of_range_falls_back_to_the_first_bone(tmp_path):
    data = bytearray(mmd_fixture.pmx_bytes())
    struct.pack_into("<i", data, _offsets()["vstart"] + 33, 999)   # 最初の頂点のボーン
    pmx = _write(tmp_path, data)
    vmd = tmp_path / "dance.vmd"
    vmd.write_bytes(mmd_fixture.vmd_bytes(DANCE))
    model = mmd.read_pmx(pmx)
    assert int(model.bone_indices.max()) < len(model.bones)
    assert model.bone_indices[0, 0] == 0
    frames, _size = mmd.mmd_to_vox(pmx, vmd, tmp_path / "out.vox", height=16, fps=5, duration=1)
    assert frames == 6


# --- AUD2-05: VMD の NaN・無限大 ---

def test_non_finite_vmd_values_become_the_rest_pose(tmp_path):
    keys = [("センター", 0, (math.nan, 0, math.inf), (math.nan, 0, 0, math.nan)),
            ("センター", 30, (0, -2, 0), (0, 0, 0, 1))]
    pmx, vmd = _doll(tmp_path, keys)
    tracks = mmd.read_vmd(vmd)
    first = tracks["センター"][0]
    assert np.isfinite(first[1]).all() and np.allclose(first[2], [0, 0, 0, 1])
    skin = mmd.Poser(mmd.read_pmx(pmx)).pose(tracks, 15)
    assert np.isfinite(skin).all()
    frames, _size = mmd.mmd_to_vox(pmx, vmd, tmp_path / "out.vox", height=16, fps=5, duration=1)
    assert frames == 6


def test_valid_vmd_values_are_kept_exactly(tmp_path):
    rotation = np.array([0.1, 0.2, 0.3, math.sqrt(1 - 0.14)], np.float32)  # 長さ 1 (MMD の書く値)
    keys = [("センター", 0, (0.25, -1.5, 3.0), tuple(rotation.tolist())), ("センター", 30, (0, 0, 0), (0, 0, 0, 1))]
    _pmx, vmd = _doll(tmp_path, keys)
    first = mmd.read_vmd(vmd)["センター"][0]
    assert first[1].tolist() == [0.25, -1.5, 3.0]
    assert np.allclose(first[2], rotation, atol=0)


def test_vmd_rotations_are_normalized(tmp_path):
    """長さが 1 でない回転は向きだけを使う (2 倍の長さで人物が消えていた。#202)。"""
    keys = [("センター", 0, (0, 0, 0), (0.2, 0.4, 0.6, 1.8)), ("センター", 30, (0, 0, 0), (0, 0, 0, 2))]
    pmx, vmd = _doll(tmp_path, keys)
    tracks = mmd.read_vmd(vmd)
    for _frame, _position, rotation, _curve in tracks["センター"]:
        assert abs(np.linalg.norm(rotation) - 1) < 1e-9
    assert np.allclose(tracks["センター"][1][2], [0, 0, 0, 1])
    frames, _size = mmd.mmd_to_vox(pmx, vmd, tmp_path / "out.vox", height=16, fps=5, duration=1)
    assert frames == 6


# --- AUD2-06・07: --figure-voxels と --views の上限 ---

def test_figure_voxels_has_an_upper_limit(tmp_path):
    out = str(tmp_path / "x.mcaddon")
    assert cim.parse_args(["doll.pmx", "-o", out, "--figure-voxels", str(rig.MAX_VOXELS)]).figure_voxels == rig.MAX_VOXELS
    with pytest.raises(SystemExit):
        cim.parse_args(["doll.pmx", "-o", out, "--figure-voxels", str(rig.MAX_VOXELS + 1)])
    with pytest.raises(ValueError):
        rig.rest_grid(mmd.read_pmx(_doll(tmp_path)[0]), voxels=rig.MAX_VOXELS + 1)


def test_views_has_an_upper_limit(tmp_path):
    out = str(tmp_path / "x.mcaddon")
    assert cim.parse_args(["a.vox", "-o", out, "--views", str(cim.MAX_VIEWS)]).views == cim.MAX_VIEWS
    with pytest.raises(SystemExit):
        cim.parse_args(["a.vox", "-o", out, "--views", str(cim.MAX_VIEWS + 1)])


# --- AUD2-10: アドオンのスクリプトは、取り出し方 (CRLF / LF) によらず同じ中身 ---

def test_image_mode_script_is_packed_with_lf_line_endings(tmp_path, monkeypatch):
    source = cim.IMAGE_MAIN_SCRIPT.read_text(encoding="utf-8")
    crlf = tmp_path / "image_main.js"
    crlf.write_bytes(source.replace("\n", "\r\n").encode("utf-8"))   # Windows の core.autocrlf で取り出したのと同じ
    monkeypatch.setattr(cim, "IMAGE_MAIN_SCRIPT", crlf)
    frames = [np.zeros((36, 64, 3), np.uint8)]
    files, _report = cim.build_files([{"id": "v", "title": "v", "fps": 10, "frames": iter(frames)}], width=64,
                                     height=36, methods=["a"], namespace="ns", pack_name="p", log=lambda _t: None)
    packed = files["BP/scripts/image_main.js"]
    assert b"\r" not in packed
    assert packed.decode("utf-8") == source.replace("\r\n", "\n")


# --- AUD2-08: Pillow が大きすぎると断る画像 (DecompressionBombError) ---

def _bomb(tmp_path):
    path = tmp_path / "huge.png"
    Image.new("1", (20000, 20000)).save(path)   # 4 億画素 (Pillow の上限の約 2 倍)。1 ビットなので小さい
    return path


def test_case_picture_that_pillow_refuses_is_a_clean_error(tmp_path):
    images = {"back": str(_bomb(tmp_path))}
    with pytest.raises(ValueError, match="ケースの絵を読めませんでした"):
        glass.case_texture((2.0, 2.0, 3.0), glass.CaseOptions(images=images))


def test_model_texture_that_pillow_refuses_falls_back_to_the_material_colour(tmp_path):
    model = mmd.read_pmx(_doll(tmp_path)[0])
    _bomb(tmp_path)
    model.textures = ["huge.png"]
    model.materials[0]["texture"] = 0
    materials = mmd._Materials(model, tmp_path)
    assert materials.ranges[0][2] is None
