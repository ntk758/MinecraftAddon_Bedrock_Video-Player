"""複数人で遊ぶとき (TODO.md の P1-2 のうち、モックで確かめられる分)。ブロックモード (main.js) を 2 人で動かす。

確かめること: 2 人とも音が聞こえる・後から入った人・2 人がほぼ同時にリモコンの再生/一時停止を押す・片方が抜ける・
画面から離れた人の音量。実機 (LAN か BDS で 2 人) は未確認。
"""
import shutil

import pytest

from test_long_playback import many_gops  # noqa: F401 (12 秒の動画のフィクスチャ)
from test_playback_control import drive

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node が必要")

AUDIO = {"demo": {"audio_chunks": 2}}   # 12 秒の動画: 10 秒ごとのチャンクが 2 つ
CHUNK0, CHUNK1 = "badapple.demo.chunk_0", "badapple.demo.chunk_1"


def play(many_gops, tmp_path, steps):
    out = drive(tmp_path / "addon", {"demo": many_gops}, [{"event": "badapple:setup"}, *steps], listing_extra=AUDIO)
    assert out["errors"] == []
    return out


def test_both_players_hear_every_chunk(many_gops, tmp_path):
    out = play(many_gops, tmp_path, [{"secondPlayer": "join"}, {"event": "badapple:start"}, {"tick": 260}])
    assert [s for s in out["sounds"] if not s.startswith("p2:")] == [CHUNK0, CHUNK1]
    assert [s for s in out["sounds"] if s.startswith("p2:")] == [f"p2:{CHUNK0}", f"p2:{CHUNK1}"]


@pytest.mark.parametrize("join_tick,hears_current", [(10, True), (60, False)], ids=["0.5s", "3s"])
def test_a_late_joiner_hears_the_current_chunk_only_near_its_start(many_gops, tmp_path, join_tick, hears_current):
    """途中で入った人には、チャンクが始まって 1 秒以内なら今のチャンクを鳴らす。それより後は次のチャンクから。"""
    out = play(many_gops, tmp_path, [{"event": "badapple:start"}, {"tick": join_tick}, {"secondPlayer": "join"},
                                     {"tick": 260}])
    second = [s for s in out["sounds"] if s.startswith("p2:")]
    assert second == ([f"p2:{CHUNK0}"] if hears_current else []) + [f"p2:{CHUNK1}"]


def test_two_presses_of_play_pause_in_the_same_tick_cancel_out(many_gops, tmp_path):
    """リモコンの再生/一時停止は切り替えなので、2 人が同じ tick に押すと、一時停止してすぐ再開する (今の仕様)。
    壊れた状態にはならず、最後まで再生する。"""
    out = play(many_gops, tmp_path, [{"secondPlayer": "join"}, {"event": "badapple:start"}, {"tick": 40},
                                     {"ui": [{"selection": 0}]}, {"ui": [{"selection": 0}]}, {"tick": 260}])
    assert any("bvp.playback_finished" in m for m in out["messages"])


def test_playback_goes_on_when_one_player_leaves(many_gops, tmp_path):
    out = play(many_gops, tmp_path, [{"secondPlayer": "join"}, {"event": "badapple:start"}, {"tick": 40},
                                     {"secondPlayer": "leave"}, {"tick": 260}])
    assert any("bvp.playback_finished" in m for m in out["messages"])
    # 抜けた後のチャンクは、残った人にだけ鳴る
    assert out["sounds"].count(CHUNK1) == 1 and f"p2:{CHUNK1}" not in out["sounds"]


def test_a_player_far_from_the_screen_hears_the_same_volume(many_gops, tmp_path):
    """音は各プレイヤーの位置で鳴らすので、画面から離れていても同じ大きさで聞こえる (今の仕様。距離で小さくはしない)。"""
    def record_volume(work):
        mock = work / "node_modules" / "@minecraft" / "server" / "index.js"
        text = mock.read_text(encoding="utf-8")
        old = "  playSound(id) {\n    sounds.push(`p2:${id}`);\n  },"
        assert text.count(old) == 1
        text = text.replace(old, "  playSound(id, options) {\n    sounds.push(`p2:${id}@${options?.volume}`);\n  },")
        mock.write_text(text, encoding="utf-8")

    out = drive(tmp_path / "addon", {"demo": many_gops},
                [{"event": "badapple:setup"}, {"secondPlayer": "join"}, {"event": "badapple:start"}, {"tick": 40}],
                prepare=record_volume, listing_extra=AUDIO)
    assert out["errors"] == []
    assert f"p2:{CHUNK0}@1" in out["sounds"]
