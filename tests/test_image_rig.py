"""骨で動かす人物 (image_mode/rig.py。docs/research/glass_case_practical.md の 4 章) のテスト。

モデルと踊りは tests/mmd_fixture.py の箱の人形 (実在のモデルは使わない)。部品をボーンの変形どおりに動かした形を、
モデルを変形してボクセルにした正しい形 (コマ送りと同じ) と比べる (設計の 3.3 と同じ比べ方)。
"""
import math
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from mmd_fixture import BOXES, INDEX, write_doll  # noqa: E402
from image_mode import planes, rig  # noqa: E402
from mvcodec import mmd  # noqa: E402

S45, C45 = math.sin(math.pi / 4), math.cos(math.pi / 4)
# 左腕を 90 度上げ、左足を IK で持ち上げる (ひざが曲がる)。ほかのボーンは動かない
DANCE = [("左腕", 0, (0, 0, 0), (0, 0, 0, 1)), ("左腕", 30, (0, 0, 0), (0, 0, S45, C45)),
         ("左足ＩＫ", 0, (0, 0, 0), (0, 0, 0, 1)), ("左足ＩＫ", 30, (0, 3, -1), (0, 0, 0, 1)),
         ("センター", 0, (0, 0, 0), (0, 0, 0, 1)), ("センター", 30, (0, 0, 0), (0, 0, 0, 1))]
MARGIN = 16  # 踊った形も格子に入るように


@pytest.fixture
def doll(tmp_path):
    pmx, vmd = write_doll(tmp_path, DANCE)
    model, materials = rig.load_model(pmx)
    return model, materials, mmd.read_vmd(vmd)


def names(bones):
    return {BONE_NAMES[b] for b in bones}


BONE_NAMES = {i: name for name, i in INDEX.items()}


def box_vertices(name):
    """BOXES の箱の頂点の番号 (mmd_fixture は箱ごとに 8 頂点。角は x・y・z の (-1, 1) の順)。"""
    number = [b[2] for b in BOXES].index(name)
    return {(x, y, z): number * 8 + i for i, (x, y, z) in
            enumerate((x, y, z) for x in (-1, 1) for y in (-1, 1) for z in (-1, 1))}


def true_cells(model, materials, grid, skin):
    """モデルを変形してボクセルにした、正しい表面 (コマ送りと同じ)。"""
    cells = mmd.voxel_colours(model, materials, mmd.skin_vertices(model, skin), np.array(grid.origin), grid.scale,
                              grid.size)
    return set(cells)


def near(cell, cells):
    x, y, z = cell
    return any((x + dx, y + dy, z + dz) in cells for dx in (-1, 0, 1) for dy in (-1, 0, 1) for dz in (-1, 0, 1))


def silhouette(cells, axis):
    keep = [a for a in range(3) if a != axis]
    return {(c[keep[0]], c[keep[1]]) for c in cells}


# --- 三角形とボクセル ---

def test_triangle_bones_picks_the_strongest_bone_and_the_joint_partner():
    model = SimpleNamespace(
        faces=np.array([[0, 1, 2], [3, 4, 5]]),
        bone_indices=np.array([[1, 0, 0, 0], [1, 2, 0, 0], [1, 0, 0, 0], [1, 0, 0, 0], [1, 0, 0, 0], [1, 2, 0, 0]]),
        bone_weights=np.array([[1, 0, 0, 0], [0.5, 0.5, 0, 0], [1, 0, 0, 0], [1, 0, 0, 0], [1, 0, 0, 0],
                               [0.8, 0.2, 0, 0]], np.float32))
    dominant, second = rig.triangle_bones(model)
    assert dominant.tolist() == [1, 1]
    # 1 つ目は 2 番目のボーンが合計の 1/6 (関節)、2 つ目は 1/15 (関節としない)
    assert second.tolist() == [2, -1]


def test_surface_voxels_match_the_flip_book_voxels_and_vote_for_the_bone(doll):
    model, materials, _tracks = doll
    grid = rig.rest_grid(model, 32)
    rest = rig.surface_voxels(model, materials, grid)
    expected = mmd.voxel_colours(model, materials, model.positions, np.array(grid.origin), grid.scale, grid.size)
    assert set(map(tuple, rest.cells.tolist())) == set(expected)
    for cell, rgb in zip(map(tuple, rest.cells.tolist()), rest.rgb):
        assert np.allclose(rgb, expected[cell])
    # 左腕の箱の真ん中あたり (ひじとの境目と、上半身から離れた所) のボクセルは、どれも左腕の部品
    centre = grid.to_mmd(rest.cells + 0.5)
    arm = (centre[:, 0] > 2.5) & (centre[:, 0] < 4.5) & (np.abs(centre[:, 1] - 14) < 1)
    assert arm.any() and set(rest.bone[arm].tolist()) == {INDEX["左腕"]}
    # 人形のウェイトは箱ごとに 1 本なので、関節の相手はどこにも無い
    assert (rest.second == -1).all()


def test_fill_interior_fills_the_closed_boxes_with_the_nearest_surface(doll):
    model, materials, _tracks = doll
    grid = rig.rest_grid(model, 32)
    surface = rig.surface_voxels(model, materials, grid)
    rest = rig.fill_interior(surface)
    assert len(rest.cells) > len(surface.cells)
    assert rest.surface.sum() == len(surface.cells)
    inner = ~rest.surface
    # 埋めたボクセルは、どれもどれかの箱の中
    centre = grid.to_mmd(rest.cells[inner] + 0.5)
    inside_any = np.zeros(len(centre), bool)
    for box_centre, half, _bone, _colour in BOXES:
        inside_any |= (np.abs(centre - np.array(box_centre)) <= np.array(half) + 1 / grid.scale).all(axis=1)
    assert inside_any.all()
    # 上半身の箱の真ん中は埋まり、上半身の色とボーンを受け継ぐ
    middle = tuple(np.floor(grid.to_voxel((0, 12, 0))).astype(int).tolist())
    found = [i for i, c in enumerate(map(tuple, rest.cells.tolist())) if c == middle]
    assert found and not rest.surface[found[0]]
    assert rest.bone[found[0]] == INDEX["上半身"]
    assert np.allclose(rest.rgb[found[0]], [0.9 * 255] * 3)


# --- ボーン ---

def test_bones_that_never_move_are_merged_into_their_parent(doll):
    model, materials, tracks = doll
    built = rig.build_rig(model, materials, [tracks], voxels=32)
    assert names(built.bones) == {"センター", "左腕", "左足", "左ひざD"}
    parent = {BONE_NAMES[b]: (BONE_NAMES[p] if p >= 0 else None) for b, p in built.parents.items()}
    # 上半身・下半身は動かないのでセンターにまとまり、腕と足の親はセンターになる
    assert parent == {"センター": None, "左腕": "センター", "左足": "センター", "左ひざD": "左足"}
    assert built.bones[0] == INDEX["センター"]
    # 左ひじは左腕に、右の腕・足と頭はセンターにまとまる
    assert built.target[INDEX["左ひじ"]] == INDEX["左腕"]
    assert built.target[INDEX["右ひざ"]] == built.target[INDEX["頭"]] == INDEX["センター"]
    # どのボクセルも、残したボーンのどれかの部品
    assert set(built.members_p.tolist()) <= set(built.bones)
    assert built.poses_checked > 1


def test_without_merging_every_bone_with_voxels_and_its_ancestors_is_kept(doll):
    model, materials, tracks = doll
    built = rig.build_rig(model, materials, [tracks], voxels=32, merge=False)
    assert names(built.bones) == {"センター", "下半身", "上半身", "頭", "左腕", "左ひじ", "右腕", "右ひじ", "左足",
                                  "右足", "右ひざ", "左ひざD"}
    # 頂点の付いていない左ひざ (左ひざD が付与で受ける) と IK は geometry に入れない
    assert INDEX["左ひざ"] not in built.bones and INDEX["左足ＩＫ"] not in built.bones
    # 親が先に来る
    order = {b: i for i, b in enumerate(built.bones)}
    assert all(p < 0 or order[p] < order[b] for b, p in built.parents.items())
    # 踊りが無ければ、全部が根にまとまる (動かない人形)
    still = rig.build_rig(model, materials, [], voxels=32)
    assert still.bones == [INDEX["センター"]]


# --- 動かした形 ---

@pytest.mark.parametrize("frame", [15, 30])
def test_moving_the_parts_with_the_bones_matches_the_skinned_doll(doll, frame):
    model, materials, tracks = doll
    built = rig.build_rig(model, materials, [tracks], voxels=48, margin=MARGIN)
    skin = mmd.Poser(model).pose(tracks, frame)
    truth = true_cells(model, materials, built.grid, skin)
    moved = set(map(tuple, rig.posed_cells(built, skin).tolist()))
    moved_surface = set(map(tuple, rig.posed_cells(built, skin, surface_only=True).tolist()))
    missed = sum(not near(c, moved) for c in truth) / len(truth)
    extra = sum(not near(c, truth) for c in moved_surface) / len(moved_surface)
    assert missed <= 0.005 and extra <= 0.01
    for axis in (1, 0):  # 正面 (奥行きを潰す)・横
        want, got = silhouette(truth, axis), silhouette(moved, axis)
        # 影の欠けは、斜めの縁で格子に戻すときの丸めの 1 画素だけ (縁から離れた欠けは無い)
        assert len(want - got) / len(want) <= 0.02
        assert all(any((u + du, v + dv) in got for du in (-1, 0, 1) for dv in (-1, 0, 1)) for u, v in want - got)
    # 比べ方が効いているか: 初期姿勢のまま動かさない形は、腕と足の分だけ大きく外れる
    still = set(map(tuple, rig.posed_cells(built, mmd.Poser(model).pose({}, 0)).tolist()))
    assert sum(not near(c, still) for c in truth) / len(truth) > 0.05


# --- 重ね代 ---

def blend_knee(model, partner="左足"):
    """ひざの上下の頂点のウェイトを 7:3 に分ける (本物のモデルの関節と同じく、ウェイトが 2 本に分かれる)。太ももの下は
    左足 0.7・左ひざD 0.3、すねの上は左ひざD 0.7・partner 0.3。partner を「左ひざ」にすると、すねの関節の相手は
    部品の無いボーン (左ひざD が付与で受けるボーン。どの三角形でも一番にならない) になる。"""
    thigh, shin = box_vertices("左足"), box_vertices("左ひざD")
    for corner, v in thigh.items():
        if corner[1] == -1:
            model.bone_indices[v] = [INDEX["左足"], INDEX["左ひざD"], 0, 0]
            model.bone_weights[v] = [0.7, 0.3, 0, 0]
    for corner, v in shin.items():
        if corner[1] == 1:
            model.bone_indices[v] = [INDEX["左ひざD"], INDEX[partner], 0, 0]
            model.bone_weights[v] = [0.7, 0.3, 0, 0]


def duplicates(built):
    counts = np.bincount(built.members_v, minlength=len(built.rest.cells))
    return np.nonzero(counts > 1)[0]


def test_parts_that_only_touch_are_not_overlapped(doll):
    """人形の太ももは下半身の箱に、すねは太ももに触れているが、ウェイトが分かれていないので重ねない。"""
    model, materials, tracks = doll
    built = rig.build_rig(model, materials, [tracks], voxels=32, overlap=2)
    assert len(built.members_v) == len(built.rest.cells)


@pytest.mark.parametrize("partner", ["左足", "左ひざ"])
def test_joints_overlap_into_the_neighbouring_part(doll, partner):
    """関節の相手が部品の無いボーン (左ひざ) でも、その親の部品 (左足) との関節として重ねる。"""
    model, materials, tracks = doll
    blend_knee(model, partner)
    thigh, shin = INDEX["左足"], INDEX["左ひざD"]
    built = {k: rig.build_rig(model, materials, [tracks], voxels=32, overlap=k) for k in (0, 1, 2)}
    assert len(duplicates(built[0])) == 0
    assert 0 < len(duplicates(built[1])) < len(duplicates(built[2]))
    for k in (1, 2):
        b = built[k]
        extra = slice(len(b.rest.cells), None)
        added_v, added_p = b.members_v[extra], b.members_p[extra]
        # 足したのは、太ももとすねの間だけ
        own = np.vectorize(b.target.get)(b.rest.bone[added_v])
        assert {(int(o), int(p)) for o, p in zip(own, added_p)} <= {(thigh, shin), (shin, thigh)}
        # 足したボクセルは、足した先の部品 (もとのボクセル) から k ボクセル以内 (6 方向にたどって)
        part = np.vectorize(b.target.get)(b.rest.bone)
        for v, p in zip(added_v, added_p):
            original = b.rest.cells[part == p]
            assert np.abs(original - b.rest.cells[v]).sum(axis=1).min() <= k


# --- geometry ---

def test_model_point_matches_the_corners_of_quad_cube():
    placing = planes.Placing(scale=2.5, centre=(4.0, 3.0), offset=(0.0, -5.0, 0.0))
    quad = planes.Quad(axis=1, k=2, a0=1, b0=3, width=4, height=2, image=np.zeros((2, 4, 4), np.uint8))
    cube = planes.quad_cube(quad, planes.Placement(0, 0, 0), placing)
    corners = [planes.model_point((x, 2, z), placing) for x in (1, 5) for z in (3, 5)]
    lo = np.min(corners, axis=0)
    hi = np.max(corners, axis=0)
    assert np.allclose(cube["origin"], lo) and np.allclose(cube["size"], hi - lo)


def test_rig_figure_has_one_bone_per_part_with_its_pivot(doll):
    model, materials, tracks = doll
    built = rig.build_rig(model, materials, [tracks], voxels=32)
    figure = rig.rig_figure(built, figure_blocks=4.0)
    bones = {b["name"]: b for b in figure["bones"]}
    assert list(bones) == [rig.bone_name(b) for b in built.bones]
    assert bones["b4"]["parent"] == "b0" and bones["b16"]["parent"] == "b8" and "parent" not in bones["b0"]
    assert figure["quads"] == sum(len(b["cubes"]) for b in figure["bones"])
    assert figure["frames"] == 1 and len(figure["sheets"]) == 1
    # pivot は初期姿勢のボーンの位置 (geometry の座標)。高さ 32 ボクセルを 4 ブロック (64) にするので 1 ボクセル = 2
    placing = figure["rig"]["placing"]
    assert placing.scale == pytest.approx(2.0)
    shoulder = built.grid.to_voxel((2, 14, 0))
    assert bones["b4"]["pivot"] == pytest.approx(planes.model_point(shoulder, placing), abs=1e-3)
    # 東西は mapping の向き (A19: ボクセルの右 = geometry の x)、高さは床からの 14 単位
    assert bones["b4"]["pivot"][1] == pytest.approx(14 * built.grid.scale * 2, abs=1e-3)
    assert bones["b4"]["pivot"][0] > bones["b0"]["pivot"][0]
    # 人物は床 (y = 0) から 4 ブロックの高さ。頭のてっぺんは 1 ボクセルの丸めの内
    tops = [c["origin"][1] + c["size"][1] for b in figure["bones"] for c in b["cubes"]]
    bottoms = [c["origin"][1] for b in figure["bones"] for c in b["cubes"]]
    assert abs(max(tops) - 64) <= 2 and 0 <= min(bottoms) <= 2
    # UV はシートの中
    for b in figure["bones"]:
        for cube in b["cubes"]:
            (face, uv), = cube["uv"].items()
            x, y = uv["uv"]
            w, h = uv["uv_size"]
            assert 0 <= x and x + w <= figure["sheet_width"] and 0 <= y and y + h <= figure["sheet_height"]
