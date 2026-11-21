"""置く前の確認 (setup の枠とフォーム)、天井に貼る (setup ceiling)、手順書の本 (最初の setup と guide) のテスト。

setup は既定で、置く範囲の枠を粒で描き、フォームで「ここに置く」「90 度回す」「やめる」を選ばせる。確定するまで
ブロックは触らない。setup now は以前の版と同じくすぐ置く。main.js をモックの Minecraft で動かして確かめる。
"""
import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_codec_v4 import make_scene_frames, run_convert  # noqa: E402
from test_playback_control import drive, reference_frames  # noqa: E402
from test_setup_facing import FACINGS, START, placed  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node が必要")

CONFIRM, ROTATE, CANCEL = 0, 1, 2


@pytest.fixture(scope="module")
def flat(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("preview")
    make_scene_frames(tmp / "frames")
    path = tmp / "flat.js"
    run_convert(tmp / "frames", path, "--keyframe-interval", "1000")
    return path


def run(tmp_path, flat, steps):
    out = drive(tmp_path / "addon", {"clip": flat}, [dict(START), {"view": FACINGS["north"][0]}, *steps])
    assert out["errors"] == []
    return out


def preview(message, *answers):
    return {"event": "badapple:setup", "message": message, "preview": True, "ui": [{"selection": a} for a in answers]}


def test_preview_shows_the_outline_and_places_nothing_until_confirmed(flat, tmp_path):
    """確認のフォームが出ている間は枠の粒だけ。やめるを選ぶとブロックも常時読み込みも触らない。"""
    out = run(tmp_path, flat, [preview("wall", CANCEL), {"tick": 30}])
    assert out["forms"][0]["kind"] == "action"
    assert out["forms"][0]["title"] == {"translate": "bvp.preview_title"}
    assert [b["translate"] for b in out["forms"][0]["buttons"]] == [
        "bvp.preview_confirm", "bvp.preview_rotate", "bvp.preview_cancel"]
    assert out["particles"] > 0
    assert out["allBlocks"] == []
    assert not any(c.startswith("tickingarea add") for c in out["commands"])
    assert any("bvp.preview_canceled" in m for m in out["messages"])
    assert not any("bvp.setup_done" in m for m in out["messages"])
    # 枠を描き直すタイマーは止める
    assert out["stats"].get("clearedRuns", 0) >= 1


def test_confirm_places_the_same_screen_as_setup_now(flat, tmp_path):
    """「ここに置く」を選ぶと、確認しない setup now と同じブロックを置く。枠はその画面を囲む。"""
    play = [{"event": "badapple:start"}, {"until": "bvp.playback_finished"}, {"tick": 20}]
    confirmed = run(tmp_path / "confirmed", flat, [preview("wall", CONFIRM), *play])
    now = run(tmp_path / "now", flat, [{"event": "badapple:setup", "message": "wall"}, *play])
    assert placed(confirmed) == placed(now) and len(placed(now)) > 50
    xs = [x for x, _y, _z, _b in now["allBlocks"]]
    ys = [y for _x, y, _z, _b in now["allBlocks"]]
    (px0, px1), (py0, py1), _ = confirmed["particleBox"]
    assert px0 == min(xs) and px1 == max(xs) + 1
    assert py0 == min(ys) and py1 == max(ys) + 1


def test_rotate_turns_the_screen_before_placing(flat, tmp_path):
    """「90 度回す」で北から東へ回して描き直し、もう一度聞く。確定すると東向きで置く。"""
    out = run(tmp_path, flat, [preview("wall", ROTATE, CONFIRM), {"dp": "after"}])
    assert len(out["forms"]) == 2
    assert any("bvp.facing_north" in json.dumps(f["body"]) for f in out["forms"][:1])
    assert "bvp.facing_east" in json.dumps(out["forms"][1]["body"])
    assert any("bvp.setup_wall" in m and "bvp.facing_east" in m for m in out["messages"])


def test_ceiling_screen_is_mirrored_so_it_reads_from_below(flat, tmp_path):
    """setup ceiling: 頭の上の最初のブロックの高さに、頭の真上へ中央をそろえて貼る。下から見上げて読めるよう
    行の並びを床と逆にする (左右はそのまま)。"""
    from mvcodec.decode import load_frame_data

    data = load_frame_data(flat)
    final = reference_frames(data)[-1]
    width, height = data["width"], data["height"]
    roof_y = START["y"] + 7
    steps = [{"setBlock": {"x": 0, "y": roof_y, "z": 100, "id": "minecraft:stone"}},
             {"event": "badapple:setup", "message": "ceiling"}, {"event": "badapple:start"},
             {"until": "bvp.playback_finished"}, {"tick": 20}]
    out = run(tmp_path, flat, steps)
    expected = {(0, roof_y, 0): "minecraft:stone"}
    for p, block in enumerate(final):
        row, col = divmod(p, width)
        expected[(col - width // 2, roof_y, (height - 1 - row) - height // 2)] = block
    got = placed(out)
    assert got == expected
    assert any("bvp.setup_ceiling" in m for m in out["messages"])
    assert any("bvp.setup_view_ceiling" in m for m in out["messages"])


def test_ceiling_without_a_roof_goes_a_few_blocks_up(flat, tmp_path):
    """頭の上にブロックが無ければ、足元から 4 ブロック上に貼る。"""
    out = run(tmp_path, flat, [{"event": "badapple:setup", "message": "ceiling"}, {"event": "badapple:start"},
                               {"until": "bvp.playback_finished"}])
    assert {y for _x, y, _z, _b in out["allBlocks"]} == {START["y"] + 4}


def test_first_setup_gives_the_guide_book_and_a_remote_once(flat, tmp_path):
    """最初の setup で、手順を書いた本 (4 ページ) とリモコンのコンパスを渡す。2 回目の setup では渡さない。"""
    out = run(tmp_path, flat, [{"event": "badapple:setup"}, {"event": "badapple:setup", "message": "wall"}])
    kinds = [item["typeId"] for item in out["inventory"]]
    assert kinds == ["minecraft:writable_book", "minecraft:compass"]
    book = out["inventory"][0]
    assert len(book["pages"]) == 4
    assert all(len(json.dumps(page)) <= 256 for page in book["pages"])
    assert "bvp.guide_page_start" in json.dumps(book["pages"][0])
    assert "/badapple:" in json.dumps(book["pages"][0])
    assert book["signed"] is not None
    assert sum("bvp.guide_given" in m for m in out["messages"]) == 1


def test_guide_command_gives_another_book(flat, tmp_path):
    """guide でいつでももう 1 冊もらえる (コンパスを持っていればコンパスは渡さない)。"""
    out = run(tmp_path, flat, [{"event": "badapple:setup"}, {"event": "badapple:guide"}])
    kinds = [item["typeId"] for item in out["inventory"]]
    assert kinds == ["minecraft:writable_book", "minecraft:compass", "minecraft:writable_book"]


def test_guide_is_dropped_when_the_inventory_is_full(flat, tmp_path):
    out = run(tmp_path, flat, [{"inventoryFull": True}, {"event": "badapple:guide"}])
    assert [d["typeId"] for d in out["droppedItems"]] == ["minecraft:writable_book", "minecraft:compass"]
    assert any("bvp.guide_dropped" in m for m in out["messages"])


@pytest.mark.parametrize("lang", ["en", "ja", "de", "es", "fr", "ko", "pt-BR", "ru", "zh-CN", "zh-TW"])
def test_new_texts_exist_in_every_language(lang):
    data = json.loads((ROOT / "locales" / f"{lang}.json").read_text(encoding="utf-8"))
    for key in ("preview_title", "preview_replace", "setup_ceiling", "guide_title", "guide_page_start", "guide_page_more"):
        assert data.get(f"addon.{key}"), (lang, key)
    assert "%1setup" in data["addon.guide_page_start"]
