"""再生 (main.js) の不具合の再現テスト: setup のし直し・動画の切り替え・シーク・ワールドの開き直し・置けなかった
まとまり・遅れたときのコマ飛ばし・音量。

main.js をモックの Minecraft で動かす (tests/test_playback_control.py の drive)。ワールドの開き直しは、メモリの中の
プレイヤーと選択を消して (dynamic property は残して) まねる。
"""
import json
import shutil

import numpy as np
import pytest
from PIL import Image

from test_codec_v4 import run_convert
from test_playback_control import drive, reference_frames

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node が必要")

W, H = 24, 16


def frames(folder, n, color_of, w=W, h=H):
    folder.mkdir(parents=True)
    for i in range(n):
        Image.fromarray(color_of(i, w, h)).save(folder / f"frame_{i:04d}.png")
    return folder


def moving(i, w, h):
    a = np.zeros((h, w, 3), np.uint8)
    a[:] = (20, 40, 200)
    x = i % (w - 4)
    a[4:8, x:x + 4] = (250, 240, 30)
    return a


def hooks(work, extra=None):
    """開き直しのまねと、音のログ (音量) を足す。"""
    server = work / "node_modules" / "@minecraft" / "server" / "index.js"
    s = server.read_text(encoding="utf-8")
    s = s.replace("  playSound(id) {\n    sounds.push(id);", "  playSound(id, o) {\n    sounds.push(id); soundLog.push(o?.volume);")
    s = s.replace("export const options = {", "export const soundLog = [];\nexport const options = {", 1)
    server.write_text(s, encoding="utf-8")
    main = work / "main.js"
    main.write_text(main.read_text(encoding="utf-8") + "\nglobalThis.__bvp = { reload() { activePlayers.clear(); "
                    "globalSelectedVideoId = LISTED_VIDEOS[0]?.id ?? null; selectionLoaded = false; } };\n",
                    encoding="utf-8")
    driver = work / "drive_main.mjs"
    t = driver.read_text(encoding="utf-8")
    t = t.replace("board, commands,", "soundLog, board, commands,", 1)
    t = t.replace("    else if (step.forgetTicking) forgetTicking();",
                  "    else if (step.forgetTicking) forgetTicking();\n    else if (step.reload) globalThis.__bvp.reload();\n"
                  "    else if (step.costs) options.costs = step.costs;", 1)
    t = t.replace("snaps, worlds, marks,", "soundLog, snaps, worlds, marks,", 1)
    driver.write_text(t, encoding="utf-8")


def placed(out):
    return {tuple(b[:3]): b[3] for b in out["allBlocks"]}


@pytest.fixture(scope="module")
def clips(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("clips")
    a = tmp / "a.js"
    run_convert(frames(tmp / "fa", 30, moving), a)
    # 大きい動画 (切り替えで、小さい動画の外に前の動画のマスが残るか)。最後のコマは模様がある
    big = tmp / "big.js"
    data_big = run_convert(frames(tmp / "fb", 20, lambda i, w, h: np.stack(
        [np.full((h, w), i * 12 % 256, np.uint8), np.tile(np.arange(w, dtype=np.uint8) * 5, (h, 1)),
         np.full((h, w), 90, np.uint8)], -1), w=48, h=32), big, "--width", "48", "--height", "32")
    # 10〜29 コマが止まった映像 (省略フレームへのシーク)
    static = tmp / "static.js"
    data_static = run_convert(frames(tmp / "fs", 30, lambda i, w, h: moving(min(i, 10), w, h)), static)
    return {"a": a, "big": big, "static": static, "data_big": data_big, "data_static": data_static}


def test_setup_again_stops_the_old_screen(clips, tmp_path):
    """再生中に別の場所で setup し直すと、前のスクリーンの再生が止まり、前の画面は空気に戻る
    (以前は裏で置き続けていた。その後は止まるだけで、前の画面が残っていた: 実機 2026-11-13)。"""
    steps = [{"event": "badapple:setup"}, {"event": "badapple:start"}, {"tick": 20}, {"world": "before"},
             {"x": 500, "z": 500}, {"event": "badapple:setup"}, {"tick": 60}, {"world": "after"}]
    out = drive(tmp_path / "w", {"a": clips["a"]}, steps)
    assert out["errors"] == []
    before, after = ({tuple(b[:3]): b[3] for b in w["blocks"] if b[0] < 100} for w in out["worlds"])
    drawn = [pos for pos, block in before.items() if "air" not in block]
    assert drawn, "前の画面が描かれていない"
    # 前の画面に描いたマスは、すべて空気に戻る (動画の色が残らない)
    assert all("air" in after.get(pos, "minecraft:air") for pos in drawn), {after.get(pos) for pos in drawn}


def test_setup_again_far_away_clears_the_old_screen(clips, tmp_path):
    """前の画面から遠く (読み込みの外) で setup し直しても、前の画面は空気に戻る。常時読み込みの区画を先に新しい画面へ
    付け替えると、前の画面のチャンクが読み込まれなくなって片付けられなかった (実機 2026-11-13)。"""
    steps = [{"tickingOnly": True}, {"event": "badapple:setup"}, {"event": "badapple:start"}, {"tick": 20},
             {"world": "before"}, {"x": 500, "z": 500}, {"event": "badapple:setup"}, {"tick": 60}, {"world": "after"}]
    out = drive(tmp_path / "w", {"a": clips["a"]}, steps)
    assert out["errors"] == []
    before, after = ({tuple(b[:3]): b[3] for b in w["blocks"] if b[0] < 100} for w in out["worlds"])
    drawn = [pos for pos, block in before.items() if "air" not in block]
    assert drawn, "前の画面が描かれていない"
    assert all("air" in after.get(pos, "minecraft:air") for pos in drawn), {after.get(pos) for pos in drawn}
    # 片付けの後で、常時読み込みは新しい画面に付け替わっている
    assert any(c.startswith("tickingarea add 5") for c in out["commands"]), out["commands"][-3:]


def test_switching_video_clears_the_old_cells(clips, tmp_path):
    """大きい動画の後に小さい動画を再生すると、大きい動画だけにあったマスは片付く (以前は残って、停止でも消えなかった)。"""
    steps = [{"event": "badapple:setup"}, {"event": "badapple:play", "message": "big"}, {"until": "bvp.playback_finished"},
             {"settle": 3}, {"event": "badapple:play", "message": "a"}, {"until": "bvp.playback_finished"}, {"settle": 3}]
    out = drive(tmp_path / "w", {"big": clips["big"], "a": clips["a"]}, steps)
    assert out["errors"] == []
    small = drive(tmp_path / "s", {"a": clips["a"]},
                  [{"event": "badapple:setup"}, {"event": "badapple:play", "message": "a"},
                   {"until": "bvp.playback_finished"}, {"settle": 3}])
    small_cells = placed(small)
    leftovers = {pos: block for pos, block in placed(out).items() if pos not in small_cells}
    # 残ったのは片付けの 1 種類のブロックだけ (最後のコマの模様ではない)
    assert len(set(leftovers.values())) == 1


def test_seek_to_a_skipped_frame_after_stop_draws_it(clips, tmp_path):
    """止めて片付けた後に、省略フレーム (前のコマと同じ) へシークしても、そのコマを描く (以前は何も置かなかった)。"""
    expected = reference_frames(clips["data_static"])[20]
    steps = [{"event": "badapple:setup"}, {"event": "badapple:start"}, {"tick": 45}, {"ui": [{"selection": 0}]},
             {"event": "badapple:stop"}, {"settle": 3},
             {"ui": [{"selection": 5}, {"formValues": [2]}]}, {"until": "bvp.seek_done"}, {"settle": 3}, {"snap": "seek"}]
    out = drive(tmp_path / "w", {"static": clips["static"]}, steps)
    assert out["errors"] == []
    assert out["snaps"][0]["board"] == expected


def test_stop_and_volume_work_after_reopening_the_world(clips, tmp_path):
    steps = [{"event": "badapple:setup"}, {"event": "badapple:play", "message": "a"}, {"tick": 20}, {"reload": True},
             {"event": "badapple:stop"}, {"settle": 3},
             {"ui": [{"selection": 4}, {"formValues": [30]}]},
             {"event": "badapple:start"}, {"tick": 10}]
    out = drive(tmp_path / "w", {"a": clips["a"]}, steps, prepare=hooks)
    assert out["errors"] == []
    assert any("bvp.clearing" in m for m in out["messages"])
    assert any("bvp.volume_set" in m and "30%" in m for m in out["messages"])
    assert out["soundLog"][-1] == pytest.approx(0.3)


def test_volume_is_kept_when_switching_videos(clips, tmp_path):
    steps = [{"event": "badapple:setup"}, {"event": "badapple:play", "message": "a"}, {"tick": 5},
             {"ui": [{"selection": 4}, {"formValues": [40]}]}, {"ui": [{"selection": 2}]}, {"tick": 10}]
    out = drive(tmp_path / "w", {"a": clips["a"], "static": clips["static"]}, steps, prepare=hooks,
                listing_extra={"a": {"audio_chunks": 1}, "static": {"audio_chunks": 1}})
    assert out["errors"] == []
    assert out["soundLog"][-1] == pytest.approx(0.4)


def test_list_mode_places_what_it_can_when_part_of_a_batch_fails(tmp_path):
    """list の 1 まとまりの一部が置けないとき (読み込まれていないチャンクなど)、残りは置く (以前はまとまり全部が
    置かれなかった)。"""
    rng = np.random.default_rng(0)
    js = tmp_path / "noise.js"
    data = run_convert(frames(tmp_path / "f", 10, lambda i, w, h: rng.integers(0, 256, (h, w, 3), dtype=np.uint8),
                              w=32, h=32), js, "--width", "32", "--height", "32", "--palette", "concrete")
    steps = [{"event": "badapple:setup"}, {"failZ": 84}, {"event": "badapple:render", "message": "list"},
             {"event": "badapple:start"}, {"until": "bvp.playback_finished"}, {"settle": 3}, {"snap": "end"}]
    out = drive(tmp_path / "w", {"noise": js}, steps)
    assert out["errors"] == []
    snap = out["snaps"][0]
    expected = reference_frames(data)[-1]
    z0 = snap["anchor"]["z"]
    reachable = [p for p in range(32 * 32) if z0 + p // 32 < 84]
    assert reachable and all(snap["board"][p] == expected[p] for p in reachable)


def test_falling_behind_skips_frames_and_keeps_time(tmp_path):
    """置くのが遅い環境で遅れたら、途中のコマを飛ばして時間どおりに終わる (以前は全部のコマを置いて遅れ続けた)。"""
    js = tmp_path / "fast.js"
    data = run_convert(frames(tmp_path / "f", 40, lambda i, w, h: np.full((h, w, 3), (i * 37) % 256, np.uint8),
                              w=32, h=32), js, "--width", "32", "--height", "32", "--fps", "20", "--palette", "full")
    steps = [{"event": "badapple:setup"}, {"event": "badapple:render", "message": "block"},
             {"costs": {"setBlock": 60}}, {"event": "badapple:start"}, {"until": "bvp.playback_finished"},
             {"settle": 3}, {"snap": "end"}]
    out = drive(tmp_path / "w", {"fast": js}, steps, prepare=hooks)
    assert out["errors"] == []
    # 40 コマ・20 fps は 40 tick。読み込みの待ちを足しても、全部を置くより十分早く終わる
    # 40 コマ・20 fps は 40 tick (読み込みの待ちを足して 50 前後)。全部のコマを置くと 1 コマ 2 tick 以上かかる (約 100)
    assert out["ticks"] < 75
    assert out["snaps"][0]["board"] == reference_frames(data)[-1]
