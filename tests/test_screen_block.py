"""スクリーンブロック (<接頭辞>:video_screen) の結合テスト: main.js をモックの Minecraft で動かす (Node.js が必要)。

置く → 使う (操作画面) → 動画を選ぶ → 再生 → 削除の流れ、複数のスクリーンの独立、建物を壊さないこと、
ワールドを開き直したときの復元、コマンドで置く画面との共存、異常系 (壊れた保存・操作中の破壊・爆発など) を確かめる。
実機 (Minecraft) では確かめていない (docs/TESTING.md の 7 章)。
"""
import json
import shutil

import pytest

from test_main_js import collect_text, lang_keys
from test_playback_control import drive, reference_frames, scene  # noqa: F401  (scene は fixture)

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node が必要")

FACINGS = {"north": 0, "east": 1, "south": 2, "west": 3}
BLOCK = {"x": 0, "y": 64, "z": 90}
SCREEN_ID = "badapple:video_screen"

# 操作画面のボタンの位置
IDLE_LIBRARY, IDLE_SETTINGS, IDLE_REMOVE = 0, 1, 2
PLAY_OR_PAUSE, STOP, SEEK, LIBRARY, SETTINGS, REMOVE = 0, 1, 2, 3, 4, 5


def rotate_cell(facing, lx, lz):
    """main.js の rotateCell と同じ。"""
    if facing == 1:
        return -lz - 1, lx
    if facing == 2:
        return -lx - 1, -lz - 1
    if facing == 3:
        return lz, -lx - 1
    return lx, lz


def screen_cells(block, facing, width, height, scale=1):
    """スクリーンブロックの画面で、盤面の各マスが占めるワールドの位置 (マスごとに scale² 個)。
    平面はブロックの 1 つ上から壁のように立ち、左右の中央がブロックの真上 (main.js の screenLayoutFor)。"""
    f = FACINGS[facing]
    cx, cz = rotate_cell(f, (width * scale) // 2, 0)
    ax, ay, az = block["x"] - cx, block["y"] + 1, block["z"] - cz
    cells = []
    for p in range(width * height):
        col, row = p % width, p // width
        blocks = []
        for dx in range(scale):
            for dy in range(scale):
                ox, oz = rotate_cell(f, col * scale + dx, 0)
                blocks.append((ax + ox, ay + (height - 1 - row) * scale + dy, az + oz))
        cells.append(blocks)
    return cells


def world_of(out, label=None):
    """置かれているブロック (空気は除く。モックは空気に戻したマスも記録するため)。"""
    blocks = out["allBlocks"] if label is None else next(w["blocks"] for w in out["worlds"] if w["label"] == label)
    return {(x, y, z): block for x, y, z, block in blocks if block != "minecraft:air"}


def shown_board(world, cells):
    """画面に出ている盤面 (マスの正方形のブロックがそろっていなければ "mixed")。"""
    board = []
    for blocks in cells:
        ids = {world.get(b, "minecraft:air") for b in blocks}
        board.append(ids.pop() if len(ids) == 1 else "mixed")
    return board


def messages_with(out, key):
    return [m for m in out["messages"] if key in m]


def screen_record(out, label, screen_id="s1"):
    value = out["dps"][label].get(f"badapple:screen.{screen_id}")
    return json.loads(value) if value else None


def choose_video(index=0):
    """何も映していないスクリーンの操作画面で、動画の一覧から index 番目を選ぶ。"""
    return [{"selection": IDLE_LIBRARY}, {"selection": index}]


def assert_texts_are_translated(out):
    """プレイヤーに見せた文字 (チャットとフォーム) が、すべて翻訳のキーで、そのキーが翻訳ファイルにある。"""
    keys, literals = set(), []
    for message in out["messages"]:
        collect_text(json.loads(message), keys, literals)
    for form in out["forms"]:
        for part in [form["title"], form["body"], *form["buttons"], *form["sliders"],
                     *[t["label"] for t in form.get("toggles", [])], *[d["label"] for d in form.get("dropdowns", [])]]:
            if part is not None:
                collect_text(part, keys, literals)
    missing = {k for k in keys if k.startswith("bvp.")} - lang_keys()
    assert not missing, missing


def test_place_use_choose_and_play(scene, tmp_path):
    path, data = scene
    frames = reference_frames(data)
    steps = [{"placeScreen": BLOCK}, {"dp": "placed"},
             {"useScreen": BLOCK, "ui": choose_video()},
             {"runUntil": "bvp.screen.playing", "max": 200},
             {"runUntil": "bvp.screen.finished", "max": 600}, {"run": 3}, {"dp": "end"}]
    out = drive(tmp_path / "addon", {"a": path}, steps)
    assert out["errors"] == []
    # 置いただけで登録される (画面はまだ作らない)
    placed = screen_record(out, "placed")
    assert placed["media"] is None and placed["state"] == "idle" and placed["pos"] == [0, 64, 90]
    assert json.loads(out["dps"]["placed"]["badapple:screens"])["ids"] == ["s1"]
    assert messages_with(out, "bvp.screen.preparing") and messages_with(out, "bvp.screen.ready")
    # ブロックの上に壁のように立った画面に、最後のフレームが出ている
    world = world_of(out)
    assert world[(0, 64, 90)] == SCREEN_ID
    assert shown_board(world, screen_cells(BLOCK, "north", data["width"], data["height"])) == frames[-1]
    end = screen_record(out, "end")
    assert end["media"] == "a" and end["state"] == "paused" and end["w"] == data["width"] and end["h"] == data["height"]
    assert end["box"] == [-12, 65, 90, 11, 80, 90]
    # 操作画面: 何も映していないときは「動画を選ぶ・設定・削除」
    menus = [f for f in out["forms"] if "bvp.screen.title" in json.dumps(f["title"])]
    assert len(menus[0]["buttons"]) == 3
    # 音は、このスクリーンの動画のチャンク (近くにいる人へ)
    assert "badapple.a.chunk_0" in out["sounds"]
    assert_texts_are_translated(out)


@pytest.mark.parametrize("facing", ["north", "east", "south", "west"])
def test_the_screen_faces_the_way_the_block_was_placed(scene, tmp_path, facing):
    path, data = scene
    frames = reference_frames(data)
    steps = [{"placeScreen": BLOCK, "facing": facing}, {"useScreen": BLOCK, "ui": choose_video()},
             {"runUntil": "bvp.screen.finished", "max": 800}, {"run": 3}]
    out = drive(tmp_path / "addon", {"a": path}, steps)
    assert out["errors"] == []
    assert shown_board(world_of(out), screen_cells(BLOCK, facing, data["width"], data["height"])) == frames[-1]


def test_three_screens_play_pause_and_stop_independently(scene, tmp_path):
    path, data = scene
    frames = reference_frames(data)
    blank = data["level_blocks"][0]["block"]
    # 3 つとも音の届く範囲 (48 ブロック) に入るよう、プレイヤーは真ん中のスクリーンの前に立つ
    a, b, c = BLOCK, {"x": 26, "y": 64, "z": 90}, {"x": 52, "y": 64, "z": 90}
    steps = [{"x": 26}, {"placeScreen": a}, {"placeScreen": b}, {"placeScreen": c},
             {"useScreen": a, "ui": choose_video(0)}, {"runUntil": "bvp.screen.playing", "count": 1, "max": 200},
             {"useScreen": b, "ui": choose_video(1)}, {"runUntil": "bvp.screen.playing", "count": 2, "max": 200},
             {"useScreen": c, "ui": choose_video(2)}, {"runUntil": "bvp.screen.playing", "count": 3, "max": 200},
             {"run": 20}, {"intervals": "three"},
             {"useScreen": c, "ui": [{"selection": PLAY_OR_PAUSE}]}, {"run": 2},   # C を一時停止
             {"world": "before_stop"}, {"dp": "before_stop"},
             {"useScreen": a, "ui": [{"selection": STOP}]}, {"run": 2},           # A を停止
             {"dp": "after_stop"},
             {"runUntil": "bvp.screen.finished", "max": 600}, {"run": 5}, {"dp": "end"}]
    out = drive(tmp_path / "addon", {"a": path, "b": path, "c": path}, steps)
    assert out["errors"] == []
    cells = {name: screen_cells(pos, "north", data["width"], data["height"]) for name, pos in (("a", a), ("b", b), ("c", c))}
    before, end = world_of(out, "before_stop"), world_of(out)
    # A は止めて空 (パレットの先頭のブロック) になり、B は最後まで再生し、C は一時停止したまま変わらない
    assert shown_board(end, cells["a"]) == [blank] * len(frames[0])
    assert shown_board(end, cells["b"]) == frames[-1]
    assert shown_board(end, cells["c"]) == shown_board(before, cells["c"])
    # A を止めても、B と C の保存した状態は変わらない
    for screen in ("s2", "s3"):
        assert screen_record(out, "after_stop", screen) == screen_record(out, "before_stop", screen)
    assert screen_record(out, "end", "s1")["state"] == "stopped"
    assert screen_record(out, "end", "s2")["state"] == "paused"
    assert screen_record(out, "end", "s3")["state"] == "paused"
    assert len(messages_with(out, "bvp.screen.finished")) == 1
    # 音: 止めたスクリーンの音だけを止める。B の音を止めたのは、B が最後まで再生したときの 1 回だけ (A の停止では止めない)
    stops = [cmd for cmd in out["commands"] if cmd.startswith("stopsound")]
    assert "stopsound @a badapple.a.chunk_0" in stops and "stopsound @a badapple.c.chunk_0" in stops
    assert stops.count("stopsound @a badapple.b.chunk_0") == 1
    assert stops.index("stopsound @a badapple.b.chunk_0") > stops.index("stopsound @a badapple.a.chunk_0")
    assert {"badapple.a.chunk_0", "badapple.b.chunk_0", "badapple.c.chunk_0"} <= set(out["sounds"])
    # 描画のタイマーは 1 つだけ
    assert out["intervalCounts"]["three"] == 1


def test_a_screen_is_not_placed_over_existing_blocks(scene, tmp_path):
    path, data = scene
    stone = {"x": 3, "y": 70, "z": 90, "id": "minecraft:stone"}
    palette = {spec["block"] for spec in data["level_blocks"]}
    steps = [{"placeScreen": BLOCK}, {"setBlock": stone},
             {"useScreen": BLOCK, "ui": choose_video()}, {"runUntil": "bvp.screen.error", "max": 100}, {"run": 3},
             {"world": "refused"}, {"dp": "refused"},
             {"setBlock": {**stone, "id": "minecraft:air"}},
             {"useScreen": BLOCK, "ui": choose_video()}, {"runUntil": "bvp.screen.playing", "max": 200}]
    out = drive(tmp_path / "addon", {"a": path}, steps)
    assert out["errors"] == []
    blocked = messages_with(out, "bvp.screen.error.blocked")
    assert len(blocked) == 1 and all(f'"text":"{v}"' in blocked[0] for v in ("3", "70", "90", "1"))
    refused = world_of(out, "refused")
    # 石は残り、画面のブロックは 1 つも置いていない (ワールドを変えていない)
    assert refused[(3, 70, 90)] == "minecraft:stone"
    assert {pos: block for pos, block in refused.items() if block in palette} == {}
    assert screen_record(out, "refused")["media"] is None
    # 空けたら置ける
    assert messages_with(out, "bvp.screen.playing")


def test_too_tall_and_unloaded_areas_are_refused_without_changes(scene, tmp_path):
    path, data = scene
    palette = {spec["block"] for spec in data["level_blocks"]}
    high = {"x": 0, "y": 310, "z": 90}
    steps = [{"placeScreen": high}, {"useScreen": high, "ui": choose_video()}, {"run": 5}]
    out = drive(tmp_path / "high", {"a": path}, steps)
    assert out["errors"] == []
    assert messages_with(out, "bvp.screen.error.too_tall")
    assert not [b for b in out["allBlocks"] if b[3] in palette]

    # 東向きの画面は z = 78〜101 に立つ。z が 95 以上のチャンクが読み込まれていなければ置かない
    steps = [{"placeScreen": BLOCK, "facing": "east"}, {"unloadedZ": 95},
             {"useScreen": BLOCK, "ui": choose_video()}, {"run": 10}]
    out = drive(tmp_path / "unloaded", {"a": path}, steps)
    assert out["errors"] == []
    unloaded = messages_with(out, "bvp.screen.error.not_loaded")
    assert len(unloaded) == 1 and '"text":"95"' in unloaded[0]
    assert not [b for b in out["allBlocks"] if b[3] in palette]


def test_removing_a_screen_clears_only_the_blocks_it_placed(scene, tmp_path):
    path, data = scene
    cells = screen_cells(BLOCK, "north", data["width"], data["height"])
    inside = cells[5][0]
    outside = (20, 64, 90)
    steps = [{"placeScreen": BLOCK}, {"useScreen": BLOCK, "ui": choose_video()},
             {"runUntil": "bvp.screen.finished", "max": 800}, {"run": 3},
             # 画面の上にプレイヤーがチェストを置き、画面の外に石を置いた
             {"setBlock": {"x": inside[0], "y": inside[1], "z": inside[2], "id": "minecraft:chest"}},
             {"setBlock": {"x": outside[0], "y": outside[1], "z": outside[2], "id": "minecraft:stone"}},
             {"breakScreen": BLOCK}, {"run": 10}, {"dp": "after"}, {"run": 150}, {"intervals": "idle"}]
    out = drive(tmp_path / "addon", {"a": path}, steps)
    assert out["errors"] == []
    world = world_of(out)
    assert world == {inside: "minecraft:chest", outside: "minecraft:stone"}
    assert messages_with(out, "bvp.screen.removed")
    assert "badapple:screen.s1" not in out["dps"]["after"]
    assert json.loads(out["dps"]["after"]["badapple:screens"])["ids"] == []
    assert any(cmd.startswith("tickingarea remove bvp_screen_s1_") for cmd in out["commands"])
    # 何も描かなくなったら、描画のタイマーも止まる
    assert out["intervalCounts"]["idle"] == 0


def test_removing_from_the_menu_keeps_the_block(scene, tmp_path):
    path, data = scene
    steps = [{"placeScreen": BLOCK}, {"useScreen": BLOCK, "ui": choose_video()}, {"runUntil": "bvp.screen.playing", "max": 200},
             {"useScreen": BLOCK, "ui": [{"selection": REMOVE}, {"selection": 0}]}, {"run": 10}, {"dp": "after"},
             {"useScreen": BLOCK, "ui": []}, {"run": 2}, {"dp": "again"}]
    out = drive(tmp_path / "addon", {"a": path}, steps)
    assert out["errors"] == []
    assert world_of(out) == {(0, 64, 90): SCREEN_ID}
    assert json.loads(out["dps"]["after"]["badapple:screens"])["ids"] == []
    # もう一度使うと、新しいスクリーンになる
    assert json.loads(out["dps"]["again"]["badapple:screens"])["ids"] == ["s2"]


def test_scale_makes_the_screen_bigger_and_back(scene, tmp_path):
    path, data = scene
    frames = reference_frames(data)
    w, h = data["width"], data["height"]
    steps = [{"placeScreen": BLOCK}, {"useScreen": BLOCK, "ui": choose_video()},
             {"runUntil": "bvp.screen.playing", "max": 200},
             {"useScreen": BLOCK, "ui": [{"selection": SETTINGS}, {"formValues": [100, False, 1]}]},
             {"runUntil": "bvp.screen.scale_set", "max": 300},
             {"runUntil": "bvp.screen.finished", "max": 800}, {"run": 3}, {"world": "x2"}, {"dp": "x2"},
             {"useScreen": BLOCK, "ui": [{"selection": SETTINGS}, {"formValues": [100, False, 0]}]},
             {"runUntil": "bvp.screen.scale_set", "count": 2, "max": 300}, {"run": 20}]
    out = drive(tmp_path / "addon", {"a": path}, steps)
    assert out["errors"] == []
    settings = [f for f in out["forms"] if "bvp.screen.settings.title" in json.dumps(f["title"])][0]
    assert [len(d["items"]) for d in settings["dropdowns"]] == [4]
    x2 = world_of(out, "x2")
    assert shown_board(x2, screen_cells(BLOCK, "north", w, h, 2)) == frames[-1]
    assert screen_record(out, "x2")["scale"] == 2 and screen_record(out, "x2")["w"] == w * 2
    # 1 倍に戻すと、2 倍の画面のうち 1 倍の外は空気に戻り、1 倍の画面に同じ場面が出る
    end = world_of(out)
    small = {b for blocks in screen_cells(BLOCK, "north", w, h) for b in blocks}
    big = {b for blocks in screen_cells(BLOCK, "north", w, h, 2) for b in blocks}
    assert all(pos not in end for pos in big - small)
    assert shown_board(end, screen_cells(BLOCK, "north", w, h)) == frames[-1]


def test_screens_come_back_paused_after_reopening_the_world(scene, tmp_path):
    path, data = scene
    frames = reference_frames(data)
    steps = [{"placeScreen": BLOCK}, {"useScreen": BLOCK, "ui": choose_video()},
             {"runUntil": "bvp.screen.playing", "max": 200}, {"run": 70}, {"dp": "before"},
             {"restart": True}, {"forgetTicking": True}, {"worldLoad": True}, {"dp": "reopened"},
             {"useScreen": BLOCK, "ui": []}, {"run": 2},
             {"tickMark": "play"}, {"useScreen": BLOCK, "ui": [{"selection": PLAY_OR_PAUSE}]},
             {"runUntil": "bvp.screen.finished", "max": 800}, {"tickMark": "finished"}, {"run": 3}]
    out = drive(tmp_path / "addon", {"a": path}, steps)
    assert out["errors"] == []
    before = screen_record(out, "before")
    assert before["state"] == "playing" and before["at"] > 1
    # 開き直すと一時停止で戻り、操作画面にもそう出る
    assert screen_record(out, "reopened")["state"] == "paused"
    menu = [f for f in out["forms"] if "bvp.screen.title" in json.dumps(f["title"])][-1]
    assert "bvp.screen.state.paused" in json.dumps(menu["body"])
    # 再生すると、保存した位置から続ける (最初からなら 150 tick 以上かかる)
    assert out["tickMarks"]["finished"] - out["tickMarks"]["play"] < 140
    assert shown_board(world_of(out), screen_cells(BLOCK, "north", data["width"], data["height"])) == frames[-1]


def test_screen_blocks_and_the_command_screen_do_not_overlap(scene, tmp_path):
    path, data = scene
    # setup の画面 (足元から北へ広がる床) と重なる位置のスクリーンブロック
    under = {"x": 5, "y": 63, "z": 90}
    steps = [{"event": "badapple:setup"}, {"placeScreen": under}, {"useScreen": under, "ui": choose_video()}, {"run": 5}]
    out = drive(tmp_path / "first", {"a": path}, steps)
    assert out["errors"] == []
    assert messages_with(out, "bvp.screen.error.overlaps_command_screen")

    # 逆: スクリーンブロックの画面がある所に setup しようとすると、置かない
    steps = [{"placeScreen": {"x": 5, "y": 63, "z": 90}}, {"useScreen": {"x": 5, "y": 63, "z": 90}, "ui": choose_video()},
             {"runUntil": "bvp.screen.playing", "max": 200}, {"z": 92}, {"event": "badapple:setup"}, {"run": 3}]
    out = drive(tmp_path / "second", {"a": path}, steps)
    assert out["errors"] == []
    assert messages_with(out, "bvp.setup_overlaps_screen")
    assert not messages_with(out, "bvp.setup_done")


def test_the_command_screen_and_a_screen_block_play_together(scene, tmp_path):
    path, data = scene
    frames = reference_frames(data)
    far = {"x": 100, "y": 64, "z": 40}
    steps = [{"event": "badapple:setup"}, {"event": "badapple:play", "message": "a"},
             {"placeScreen": far}, {"useScreen": far, "ui": choose_video(1)},
             {"runUntil": "bvp.playback_finished", "max": 1000}, {"runUntil": "bvp.screen.finished", "max": 1000},
             {"run": 5}, {"snap": "command", "video": "a"}]
    out = drive(tmp_path / "addon", {"a": path, "b": path}, steps)
    assert out["errors"] == []
    assert out["snaps"][0]["board"] == frames[-1]
    assert shown_board(world_of(out), screen_cells(far, "north", data["width"], data["height"])) == frames[-1]


def test_at_most_four_screens_play_at_once(scene, tmp_path):
    path, _ = scene
    blocks = [{"x": 30 * i, "y": 64, "z": 90} for i in range(5)]
    steps = [step for b in blocks for step in ({"placeScreen": b}, {"useScreen": b, "ui": choose_video()}, {"run": 8})]
    out = drive(tmp_path / "addon", {"a": path}, steps)
    assert out["errors"] == []
    assert len(messages_with(out, "bvp.screen.playing")) == 4
    refused = messages_with(out, "bvp.screen.error.too_many_playing")
    assert len(refused) == 1 and '"text":"4"' in refused[0]


def test_an_explosion_or_a_silent_removal_clears_the_screen(scene, tmp_path):
    path, data = scene
    other = {"x": 60, "y": 64, "z": 90}
    steps = [{"placeScreen": BLOCK}, {"placeScreen": other},
             {"useScreen": BLOCK, "ui": choose_video()}, {"runUntil": "bvp.screen.playing", "max": 200},
             {"useScreen": other, "ui": choose_video()}, {"runUntil": "bvp.screen.playing", "count": 2, "max": 200},
             {"explode": BLOCK}, {"run": 10}, {"dp": "exploded"},
             # /setblock のようにイベント無しで消えても、ほかのスクリーンを使ったときの見回りで片付ける
             {"setBlock": {**other, "id": "minecraft:air"}}, {"run": 2},
             {"placeScreen": {"x": 120, "y": 64, "z": 90}}, {"useScreen": {"x": 120, "y": 64, "z": 90}, "ui": []}, {"run": 10},
             {"dp": "silent"}]
    out = drive(tmp_path / "addon", {"a": path}, steps)
    assert out["errors"] == []
    assert json.loads(out["dps"]["exploded"]["badapple:screens"])["ids"] == ["s2"]
    assert json.loads(out["dps"]["silent"]["badapple:screens"])["ids"] == ["s3"]
    palette = {spec["block"] for spec in data["level_blocks"]}
    assert [b for b in out["allBlocks"] if b[3] in palette] == []


def test_breaking_the_block_while_the_menu_is_preparing(scene, tmp_path):
    path, data = scene
    steps = [{"placeScreen": BLOCK}, {"useScreen": BLOCK, "ui": choose_video(), "wait": 1},
             {"breakScreen": BLOCK}, {"run": 20}, {"dp": "after"}]
    out = drive(tmp_path / "addon", {"a": path}, steps)
    assert out["errors"] == []
    palette = {spec["block"] for spec in data["level_blocks"]}
    assert [b for b in out["allBlocks"] if b[3] in palette] == []
    assert json.loads(out["dps"]["after"]["badapple:screens"])["ids"] == []
    assert not messages_with(out, "bvp.screen.playing")


def test_broken_saved_data_and_missing_videos(scene, tmp_path):
    path, _ = scene
    ghost = {"v": 1, "id": "s1", "media": "ghost", "renderer": "block", "mount": "block", "dim": "overworld",
             "pos": [0, 64, 90], "facing": 0, "state": "paused", "at": 3, "volume": 1, "loop": False, "scale": 1,
             "w": 24, "h": 16, "created": 0, "box": None, "pending": []}
    steps = [{"setDp": {"badapple:screens": json.dumps({"v": 1, "next": 5, "ids": ["s1", "s4"]}),
                        "badapple:screen.s1": json.dumps(ghost), "badapple:screen.s4": "{broken"}},
             {"placeScreen": BLOCK},
             {"useScreen": BLOCK, "ui": [{"selection": PLAY_OR_PAUSE}]}, {"run": 3},
             {"useScreen": BLOCK, "ui": [{"selection": LIBRARY}, {"selection": 0}]},
             {"runUntil": "bvp.screen.playing", "max": 200}, {"dp": "end"}]
    out = drive(tmp_path / "addon", {"a": path}, steps)
    assert out["errors"] == []
    missing = messages_with(out, "bvp.screen.error.no_media")
    assert len(missing) == 1 and '"text":"ghost"' in missing[0]
    assert json.loads(out["dps"]["end"]["badapple:screens"])["ids"] == ["s1"]
    assert screen_record(out, "end")["media"] == "a"

    steps = [{"setDp": {"badapple:screens": "{not json"}}, {"placeScreen": BLOCK},
             {"useScreen": BLOCK, "ui": choose_video()}, {"runUntil": "bvp.screen.playing", "max": 200}]
    out = drive(tmp_path / "corrupt", {"a": path}, steps)
    assert out["errors"] == []
    assert messages_with(out, "bvp.screen.playing")


def test_sound_reaches_only_players_near_the_screen(scene, tmp_path):
    path, _ = scene
    far = {"x": 300, "y": 64, "z": 90}
    steps = [{"placeScreen": far}, {"useScreen": far, "ui": choose_video(1)}, {"runUntil": "bvp.screen.playing", "max": 200},
             {"run": 5}]
    out = drive(tmp_path / "addon", {"a": path, "b": path}, steps)
    assert out["errors"] == []
    assert not any("badapple.b." in s for s in out["sounds"])


def test_volume_and_loop_settings_are_per_screen(scene, tmp_path):
    path, _ = scene
    a, b = BLOCK, {"x": 40, "y": 64, "z": 90}
    steps = [{"placeScreen": a}, {"placeScreen": b},
             {"useScreen": a, "ui": choose_video()}, {"runUntil": "bvp.screen.playing", "max": 200},
             {"useScreen": a, "ui": [{"selection": SETTINGS}, {"formValues": [30, True, 0]}]}, {"run": 3},
             {"useScreen": b, "ui": [{"selection": IDLE_SETTINGS}, {"formValues": [0, False]}]}, {"run": 3},
             {"runUntil": "bvp.screen.finished", "max": 400}, {"run": 200}, {"dp": "end"}]
    out = drive(tmp_path / "addon", {"a": path}, steps)
    assert out["errors"] == []
    first, second = screen_record(out, "end", "s1"), screen_record(out, "end", "s2")
    assert (first["volume"], first["loop"]) == (0.3, True)
    assert (second["volume"], second["loop"]) == (0, False)
    # ループするスクリーンは最後まで行っても止まらない (終わりの知らせも出さない)
    assert first["state"] == "playing"
    assert not messages_with(out, "bvp.screen.finished")
