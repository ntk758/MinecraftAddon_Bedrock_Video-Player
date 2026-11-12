"""大きな画面: 世界の高さに入らない壁は置かずに知らせる。描画距離より大きい画面は知らせる。"""
import shutil

import numpy as np
import pytest
from PIL import Image

from test_codec_v4 import run_convert
from test_playback_control import drive

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node が必要")


def make_clip(tmp_path, width, height, name="clip"):
    folder = tmp_path / f"{name}_frames"
    folder.mkdir()
    for i in range(2):
        Image.fromarray(np.full((height, width, 3), (40 + i * 100, 80, 160), np.uint8)).save(folder / f"{i}.png")
    js = tmp_path / f"{name}.js"
    run_convert(folder, js, "--width", str(width), "--height", str(height), "--palette", "concrete")
    return js


def test_a_wall_taller_than_the_world_is_not_placed(tmp_path):
    """高さ 400 の画面を壁に立てようとすると、オーバーワールド (384) に入らないので置かずに知らせる (以前は黙って上が
    切れ、置けなかった行を毎フレーム置き直していた)。床に置くのはできる。"""
    js = make_clip(tmp_path, 8, 400)
    wall = drive(tmp_path / "wall", {"tall": js}, [{"event": "badapple:setup", "message": "wall"},
                                                   {"event": "badapple:start"}, {"tick": 30}])
    assert wall["errors"] == []
    assert any("bvp.setup_too_tall" in m and "400" in m and "384" in m for m in wall["messages"])
    assert not any("bvp.setup_done" in m for m in wall["messages"])
    assert wall["allBlocks"] == []
    floor = drive(tmp_path / "floor", {"tall": js}, [{"event": "badapple:setup"}])
    assert any("bvp.setup_done" in m for m in floor["messages"])


def test_a_very_large_screen_mentions_the_render_distance(tmp_path):
    js = make_clip(tmp_path, 200, 8)
    out = drive(tmp_path / "w", {"wide": js}, [{"event": "badapple:setup"}])
    assert any("bvp.setup_large" in m and "200" in m for m in out["messages"])
    small = drive(tmp_path / "s", {"small": make_clip(tmp_path, 64, 8, "small")}, [{"event": "badapple:setup"}])
    assert not any("bvp.setup_large" in m for m in small["messages"])


@pytest.mark.parametrize("render", ["rect", "list"])
def test_cells_in_loaded_chunks_are_drawn_when_part_of_the_screen_is_unloaded(tmp_path, render):
    """画面の一部のチャンクが読み込まれていなくても、読み込まれた側は正しく描く (以前は rect の箱・list のまとまりが
    読み込まれていない所にかかると全体が置かれず、読み込まれた側も 2 割ほど崩れていた)。"""
    from test_playback_control import reference_frames

    rng = np.random.default_rng(1)
    folder = tmp_path / "frames"
    folder.mkdir()
    for i in range(6):
        a = np.zeros((48, 48, 3), np.uint8)
        a[:] = (40 + i * 30, 90, 160)
        a[i * 8:i * 8 + 8] = rng.integers(0, 256, (8, 48, 3), dtype=np.uint8)
        Image.fromarray(a).save(folder / f"{i}.png")
    js = tmp_path / "clip.js"
    data = run_convert(folder, js, "--width", "48", "--height", "48", "--palette", "concrete")
    steps = [{"event": "badapple:setup"}, {"unloadedZ": 80}, {"event": "badapple:render", "message": render},
             {"event": "badapple:start"}, {"until": "bvp.playback_finished"}, {"settle": 3}, {"snap": "end"}]
    out = drive(tmp_path / "w", {"clip": js}, steps)
    assert out["errors"] == []
    snap = out["snaps"][0]
    expected = reference_frames(data)[-1]
    z0 = snap["anchor"]["z"]
    loaded = [p for p in range(48 * 48) if (z0 + p // 48) // 16 < 80 // 16]
    assert loaded and all(snap["board"][p] == expected[p] for p in loaded)
