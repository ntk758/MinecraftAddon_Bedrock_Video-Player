"""画像モードの atlas (コマのシート並べ・テクスチャのメモリ見積もり) のテスト。"""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from image_mode import atlas  # noqa: E402


def solid(width, height, value):
    """内部が全部 value (R=value, G=value+1, B=value+2) のコマ。"""
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    frame[..., 0], frame[..., 1], frame[..., 2] = value, value + 1, value + 2
    return frame


def gradient(width, height, seed):
    """画素ごとに違う値のコマ (端の繰り返しを確かめる)。"""
    rng = np.random.default_rng(seed)
    return rng.integers(0, 256, (height, width, 3), dtype=np.uint8)


def test_single_sheet_shrinks_to_frame_count():
    frames = [solid(4, 3, i * 10) for i in range(5)]
    sheets, layout = atlas.pack_sheets(iter(frames), 4, 3, max_sheet=64, gutter=1)
    # 64 四方に 12 列 x 16 行入るが、5 コマなので 5 列 x 1 行
    assert layout["cols"] == 5 and layout["rows"] == 1
    assert layout["sheets"] == 1 and layout["frame_count"] == 5
    assert layout["cells_per_sheet"] == 5
    assert (layout["sheet_width"], layout["sheet_height"]) == (5 * 5 - 1, 3)
    assert sheets[0].size == (layout["sheet_width"], layout["sheet_height"])


def test_exact_cell_placement():
    # 4 x 3 のコマ、max_sheet 12 (gutter 1): 列 (12+1)//5 = 2、行 (12+1)//4 = 3 → 最大 6 コマだが 4 コマなので 2 x 2
    frames = [solid(4, 3, 10 * i) for i in range(4)]
    sheets, layout = atlas.pack_sheets(frames, 4, 3, max_sheet=12, gutter=1)
    assert (layout["cols"], layout["rows"]) == (2, 2)
    assert layout["sheets"] == 1
    image = np.asarray(sheets[0])
    assert image.shape == (7, 9, 3)
    for i in range(4):
        x = (i % 2) * 5
        y = (i // 2) * 4
        assert (image[y:y + 3, x:x + 4] == frames[i]).all()


def test_gutter_repeats_edge_pixels():
    frames = [gradient(4, 3, i) for i in range(4)]
    sheets, layout = atlas.pack_sheets(frames, 4, 3, max_sheet=12, gutter=1)
    image = np.asarray(sheets[0])
    # マス 0 の右の gutter (x = 4) はマス 0 の右端の列、下の gutter (y = 3) は下端の行、角は右下の画素
    assert (image[0:3, 4] == frames[0][:, 3]).all()
    assert (image[3, 0:4] == frames[0][2]).all()
    assert (image[3, 4] == frames[0][2, 3]).all()
    # マス 3 (右下) はシートの端なので gutter が無い
    assert image.shape[:2] == (3 * 2 + 1, 4 * 2 + 1)
    assert (image[4:7, 5:9] == frames[3]).all()
    # マス 1 の下の gutter はマス 1 の下端の行 (マス 0 のものではない)
    assert (image[3, 5:9] == frames[1][2]).all()


def test_no_black_gutter_for_bright_frames():
    frames = [solid(4, 3, 200) for _ in range(4)]
    sheets, _ = atlas.pack_sheets(frames, 4, 3, max_sheet=12, gutter=1)
    image = np.asarray(sheets[0])
    assert image.min() >= 200


def test_gutter_zero():
    frames = [solid(4, 3, 10 * i) for i in range(4)]
    sheets, layout = atlas.pack_sheets(frames, 4, 3, max_sheet=8, gutter=0)
    assert (layout["cols"], layout["rows"]) == (2, 2)
    assert (layout["sheet_width"], layout["sheet_height"]) == (8, 6)
    image = np.asarray(sheets[0])
    assert (image[3:6, 4:8] == frames[3]).all()


def test_multiple_sheets_have_the_same_size():
    # 4 x 3、max_sheet 9 → 列 (9+1)//5 = 2、行 (9+1)//4 = 2 で 1 枚 4 コマ。10 コマで 3 枚 (最後は 2 コマだけだが同じ大きさ)
    frames = [solid(4, 3, i) for i in range(10)]
    sheets, layout = atlas.pack_sheets(iter(frames), 4, 3, max_sheet=9, gutter=1)
    assert layout["sheets"] == 3 and len(sheets) == 3
    assert layout["frame_count"] == 10 and layout["cells_per_sheet"] == 4
    assert len({im.size for im in sheets}) == 1
    assert sheets[0].size == (layout["sheet_width"], layout["sheet_height"]) == (9, 7)
    # コマ 9 は 3 枚目のマス 1
    assert (np.asarray(sheets[2])[0:3, 5:9] == frames[9]).all()
    # 使わないマスは黒のまま
    assert (np.asarray(sheets[2])[4:7, 0:4] == 0).all()


def test_exactly_full_sheet_makes_one_sheet():
    frames = [solid(4, 3, i) for i in range(4)]
    sheets, layout = atlas.pack_sheets(frames, 4, 3, max_sheet=9, gutter=1)
    assert layout["sheets"] == 1 and layout["frame_count"] == 4 and layout["cells_per_sheet"] == 4


def test_frame_too_large_for_sheet():
    with pytest.raises(ValueError):
        atlas.pack_sheets([solid(20, 3, 0)], 20, 3, max_sheet=16, gutter=1)
    with pytest.raises(ValueError):
        atlas.pack_sheets([solid(4, 20, 0)], 4, 20, max_sheet=16, gutter=1)


def test_wrong_frame_shape_and_empty_input():
    with pytest.raises(ValueError):
        atlas.pack_sheets([solid(5, 3, 0)], 4, 3, max_sheet=64)
    with pytest.raises(ValueError):
        atlas.pack_sheets([], 4, 3, max_sheet=64)


def layout_of(sheet_width, sheet_height, sheets):
    return {"sheet_width": sheet_width, "sheet_height": sheet_height, "sheets": sheets}


def test_memory_math():
    report = atlas.memory_report(layout_of(1024, 1024, 3))
    assert report["texels"] == 1024 * 1024 * 3
    assert report["bytes"] == 1024 * 1024 * 3 * 4
    assert report["handles"] == 3
    assert report["mib"] == 12.0
    assert report["warnings"] == [] and report["errors"] == []
    assert not any(b["over"] for b in report["budgets"].values())


def test_budget_values():
    budgets = atlas.memory_report(layout_of(16, 16, 1))["budgets"]
    assert {k: v["mib"] for k, v in budgets.items()} == {
        "tier1": 150, "tier2": 225, "tier3": 300, "tier4": 600, "tier5": 800}
    assert budgets["tier2"]["bytes"] == 225 * 1024 * 1024


def test_over_tier2_warns_but_is_not_an_error():
    # 2048 x 2048 x 4 バイト = 16 MiB。15 枚で 240 MiB (Tier 2 の 225 を超え、Tier 3 の 300 以内)
    report = atlas.memory_report(layout_of(2048, 2048, 15))
    assert report["mib"] == 240.0
    assert report["budgets"]["tier2"]["over"] and not report["budgets"]["tier3"]["over"]
    assert len(report["warnings"]) == 1 and "Tier 2" in report["warnings"][0]
    assert report["errors"] == []


def test_at_exactly_tier2_does_not_warn():
    # 225 MiB ちょうど: 2048 x 2048 x 4 = 16 MiB は割り切れないので 1 枚 15 MiB (1920 x 2048) x 15 枚
    report = atlas.memory_report(layout_of(1920, 2048, 15))
    assert report["bytes"] == 225 * 1024 * 1024
    assert report["warnings"] == []


def test_over_tier5_is_error_unless_allowed():
    layout = layout_of(2048, 2048, 51)  # 816 MiB
    report = atlas.memory_report(layout)
    assert report["errors"] and "Tier 5" in report["errors"][0]
    assert report["budgets"]["tier5"]["over"]
    allowed = atlas.memory_report(layout, allow_over_budget=True)
    assert allowed["errors"] == []
    assert any("Tier 5" in w for w in allowed["warnings"])


def test_handle_warnings():
    assert atlas.memory_report(layout_of(16, 16, 800))["warnings"] == []
    warned = atlas.memory_report(layout_of(16, 16, 801))
    assert any("801" in w for w in warned["warnings"]) and warned["errors"] == []
    over = atlas.memory_report(layout_of(16, 16, 4001))
    assert over["errors"] and "4000" in over["errors"][0]


def test_table_is_multiline_text():
    report = atlas.memory_report(layout_of(2048, 2048, 15))
    table = atlas.format_memory_report(report)
    assert table == report["table"]
    assert table.count("\n") >= 6
    assert "Tier 2" in table and "Tier 5" in table and "240.0 MiB" in table


def test_streaming_input_is_not_materialized():
    """ジェネレーターを 1 枚ずつ消費する (list にしなくてよい)。"""
    def generate():
        for i in range(10):
            yield solid(4, 3, i)
    sheets, layout = atlas.pack_sheets(generate(), 4, 3, max_sheet=9, gutter=1)
    assert layout["frame_count"] == 10 and len(sheets) == 3
