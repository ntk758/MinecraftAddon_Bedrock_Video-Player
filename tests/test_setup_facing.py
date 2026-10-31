"""setup の向き (face / wall) と、音声が入っていない動画の知らせのテスト。

setup face は、プレイヤーが見ている方向 (東西南北) に向けてスクリーンを置く。setup wall は平面の動画を壁のように
立てて置く。どの向きでも、プレイヤーから見たスクリーンは北向きに置いたときと同じに見える (足元を中心に回しただけ)。
main.js をモックの Minecraft (tests/js/minecraft_mock) で動かして確かめる。
"""
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_anamorphic import MOVED, PAUSE, WATCH, busy, no_tick_budget  # noqa: E402,F401  (busy は fixture)
from test_codec_v4 import make_scene_frames, run_convert  # noqa: E402
from test_playback_control import drive, reference_frames  # noqa: E402
from test_volume3d import convert_vox, make_depth_walk  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node が必要")

START = {"x": 0.5, "y": 64, "z": 100.5}
ORIGIN = (0, 100)
# 見ている方向と、北向きのときの足元からのずれ (x, z) をその向きのずれにする回転
FACINGS = {
    "north": ({"x": 0, "z": -1}, lambda x, z: (x, z)),
    "east": ({"x": 1, "z": 0.2}, lambda x, z: (-z, x)),
    "south": ({"x": -0.3, "z": 1}, lambda x, z: (-x, -z)),
    "west": ({"x": -1, "z": 0}, lambda x, z: (z, -x)),
}


@pytest.fixture(scope="module")
def videos(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("facing")
    make_scene_frames(tmp / "frames")
    flat = tmp / "flat.js"
    run_convert(tmp / "frames", flat, "--keyframe-interval", "1000")
    walk = make_depth_walk(tmp)
    convert_vox(walk, tmp / "volume.js")
    convert_vox(walk, tmp / "stage.js", "--view", "anamorphic")
    return {"flat": flat, "volume": tmp / "volume.js", "stage": tmp / "stage.js"}


def play(tmp_path, path, message, view):
    steps = [dict(START), {"view": view}, {"event": "badapple:setup", "message": message}, {"event": "badapple:start"},
             {"until": "bvp.playback_finished"}, {"tick": 20}]
    out = drive(tmp_path, {"clip": path}, steps)
    assert out["errors"] == []
    return out


def placed(out):
    """置いたブロックを、足元のブロックからのずれ ((dx, y, dz) -> ブロック) にする。"""
    return {(x - ORIGIN[0], y, z - ORIGIN[1]): block for x, y, z, block in out["allBlocks"]}


def ticking_boxes(out):
    boxes = []
    for command in out["commands"]:
        if command.startswith("tickingarea add"):
            x0, _y0, z0, x1, _y1, z1 = map(int, command.split()[2:8])
            boxes.append((x0, z0, x1, z1))
    return boxes


@pytest.mark.parametrize("kind, message", [("flat", "face"), ("flat", "wall"), ("volume", "face"), ("stage", "face")])
def test_screen_turns_with_the_player(videos, tmp_path, kind, message):
    """どの向きでも、北向きに置いたときを足元を中心に回したのと同じブロックを置き、常時読み込みはその範囲を覆う。"""
    north = placed(play(tmp_path / "north", videos[kind], message, FACINGS["north"][0]))
    assert len(north) > 50
    if kind == "stage":
        # 人物が写っている (目の位置を舞台の座標へ正しく戻せないと、壁と床の色だけになる)
        assert len(set(north.values()) - {"minecraft:light_gray_concrete", "minecraft:black_concrete", "minecraft:air"}) > 0
    for name in ("east", "south", "west"):
        view, turn = FACINGS[name]
        out = play(tmp_path / name, videos[kind], message, view)
        expected = {(*turn(dx, dz)[:1], y, turn(dx, dz)[1]): block for (dx, y, dz), block in north.items()}
        assert placed(out) == expected, name
        assert any(f"bvp.facing_{name}" in m for m in out["messages"])
        boxes = ticking_boxes(out)
        for x, _y, z, _block in out["allBlocks"]:
            assert any(x0 <= x <= x1 and z0 <= z <= z1 for x0, z0, x1, z1 in boxes), (name, x, z)


def test_face_north_is_the_same_as_plain_setup(videos, tmp_path):
    """北を見て setup face したときは、オプション無しの setup (以前の版の置き方) と同じ。"""
    plain = play(tmp_path / "plain", videos["flat"], "", FACINGS["east"][0])
    north = play(tmp_path / "north", videos["flat"], "face", FACINGS["north"][0])
    assert placed(plain) == placed(north)
    assert not any("bvp.setup_facing" in m for m in plain["messages"])


def test_wall_stands_the_flat_screen_up_in_front_of_the_player(videos, tmp_path):
    """setup wall: 平面の動画を、足元の 3 ブロック前に左右中央をそろえて立てる (行 0 が一番上)。"""
    path = videos["flat"]
    from mvcodec.decode import load_frame_data

    data = load_frame_data(path)
    final = reference_frames(data)[-1]
    width, height = data["width"], data["height"]
    out = play(tmp_path / "addon", path, "wall", FACINGS["north"][0])
    expected = {}
    for p, block in enumerate(final):
        row, col = divmod(p, width)
        expected[(col - width // 2, START["y"] + height - 1 - row, -3)] = block
    assert placed(out) == expected
    assert any("bvp.setup_wall" in m for m in out["messages"])


def test_layout_is_kept_when_switching_videos(videos, tmp_path):
    """setup face の後で別の動画に切り替えても、同じ向きで置き直す。"""
    steps = [dict(START), {"view": FACINGS["west"][0]}, {"event": "badapple:setup", "message": "face"},
             {"event": "badapple:play", "message": "volume"}, {"until": "bvp.playback_finished"}]
    out = drive(tmp_path / "switch", {"flat": videos["flat"], "volume": videos["volume"]}, steps)
    assert out["errors"] == []
    alone = play(tmp_path / "alone", videos["volume"], "face", FACINGS["west"][0])
    assert placed(out) == placed(alone)


@pytest.mark.parametrize("chunks, warned", [(0, True), (3, False), (None, False)])
def test_video_without_audio_is_reported(videos, tmp_path, chunks, warned):
    """音声の入っていない動画を再生すると知らせる。数の無い古いアドオンや音声のある動画では知らせない。"""
    extra = {} if chunks is None else {"clip": {"audio_chunks": chunks}}
    steps = [dict(START), {"event": "badapple:setup"}, {"event": "badapple:start"}, {"tick": 5}]
    out = drive(tmp_path / "addon", {"clip": videos["flat"]}, steps, listing_extra=extra)
    assert out["errors"] == []
    assert any("bvp.no_audio" in m for m in out["messages"]) == warned


def turn_position(position, turn):
    """北向きのときの位置 (足元 START からのずれを回す) を、その向きの位置にする。"""
    dx, dz = turn(position["x"] - START["x"], position["z"] - START["z"])
    return {**position, "x": START["x"] + dx, "z": START["z"] + dz}


@pytest.mark.parametrize("ticks", [20, 21])
@pytest.mark.parametrize("name", ["east", "south", "west"])
def test_pause_mid_frame_and_move_the_eye_in_a_turned_stage(busy, tmp_path, name, ticks):
    """setup face で回した舞台でも、描きかけのフレームで一時停止して目を動かし、再開して最後まで再生すると、止めている間と
    最後のワールドが、北向きの結果を足元を中心に回したものと全マス一致する (目の位置を舞台の座標へ戻す回転が効いている)。"""
    path = busy[20][0]

    def run(facing):
        view, turn = FACINGS[facing]
        steps = [dict(START), {"view": view}, {"event": "badapple:setup", "message": "face"},
                 turn_position(WATCH, turn), {"event": "badapple:start"}, {"tick": ticks}, PAUSE,
                 turn_position(MOVED, turn), {"tick": 30}, {"world": "paused"}, PAUSE,
                 {"until": "bvp.playback_finished"}, {"tick": 40}, {"world": "end"}]
        out = drive(tmp_path / facing, {"stage": path}, steps, prepare=no_tick_budget)
        assert out["errors"] == []
        return out

    def in_blocks(world):
        return {(x - ORIGIN[0], y, z - ORIGIN[1]): block for x, y, z, block in world["blocks"]}

    north = run("north")
    turned = run(name)
    turn = FACINGS[name][1]
    for k in range(2):
        expected = {(*turn(dx, dz)[:1], y, turn(dx, dz)[1]): block for (dx, y, dz), block in in_blocks(north["worlds"][k]).items()}
        got = in_blocks(turned["worlds"][k])
        assert len(got) > 1000
        differing = sum(1 for key in set(expected) | set(got) if expected.get(key) != got.get(key))
        assert differing == 0, (name, "paused" if k == 0 else "end", differing)
