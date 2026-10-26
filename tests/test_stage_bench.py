"""だまし絵のベンチマーク (benchmark/stage_bench.py) の smoke テスト。

--quick (mmd40 を 20 フレームだけ、1 回) を同じ版どうし (--baseline worktree) で動かし、決まった項目が results.json に出ること、
出力が同じはずの比較で盤面の食い違いが 0 で最後の盤面の SHA-1 が同じことを確かめる。時間の値は見ない (CI は揺れが大きい)。
"""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BENCH = ROOT / "benchmark" / "stage_bench.py"

pytestmark = pytest.mark.skipif(shutil.which("node") is None or shutil.which("ffmpeg") is None, reason="node と ffmpeg が必要")


def run_bench(*args, timeout=120):
    return subprocess.run([sys.executable, str(BENCH), *args], capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, env={**os.environ, "PYTHONIOENCODING": "utf-8"})


def test_quick_run_writes_results(tmp_path):
    pytest.importorskip("tkinter")  # 変換の引数は GUI と同じ build_converter_args で組み立てる (video_player_gui)
    out = tmp_path / "out"
    started = time.perf_counter()
    result = run_bench("--out", str(out), "--quick", "--baseline", "worktree")
    elapsed = time.perf_counter() - started
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
    assert elapsed < 60

    results = json.loads((out / "results.json").read_text(encoding="utf-8"))
    assert results["schema"] == 1
    for key in ("baseline", "worktree", "date", "node", "repeats", "frames", "cost_us", "game_ratio"):
        assert key in results["meta"]
    assert list(results["fixtures"]) == ["mmd40"]
    fixture = results["fixtures"]["mmd40"]
    assert fixture["info"]["layout"] == "volume" and fixture["info"]["stageWidth"] > 0

    micro = fixture["micro"]["summary"]
    for key in ("projection_ms", "redraw_ms", "decode_ms"):
        assert micro[key]["baseline"]["median"] > 0 and micro[key]["worktree"]["median"] > 0
        assert set(micro[key]) == {"baseline", "worktree", "delta_pct"}
    # 同じ codec.js どうしなので、盤面は 1 マスも違わない
    assert micro["diff_cells"] == {"projection": 0, "redraw": 0, "decode": 0, "drawn_mismatch": 0}

    for kind in ("still", "walk"):
        summary = fixture[kind]["summary"]
        for key in ("avg_frame_ms", "tick_mean_ms", "tick_p95_ms", "tick_max_ms", "late_frames", "max_late_ticks", "ticks"):
            assert key in summary
        assert summary["sha1_equal"] is True
        run = fixture[kind]["runs"]["worktree"][0]
        assert run["finished"] and run["stats"]["frames"] > 0 and run["board"]["blocks"] > 0
    # 目の動かし方が違っても、最後の盤面 (最後のフレームを正面から見た絵) は同じ
    assert fixture["still"]["summary"]["sha1"] == fixture["walk"]["summary"]["sha1"]
    assert "ゲーム内の見込み" in (out / "results.md").read_text(encoding="utf-8")


def test_out_inside_the_repository_is_refused():
    target = ROOT / "bench_out_refused"
    result = run_bench("--out", str(target), "--quick", timeout=60)
    assert result.returncode == 2
    assert "リポジトリの外" in result.stderr
    assert not target.exists()


def test_comparison_helpers():
    sys.path.insert(0, str(ROOT / "benchmark"))
    import stage_bench

    entry = stage_bench.compare([10.0, 10.0, 12.0], [11.0, 11.0, 11.0])
    assert entry["baseline"]["median"] == 10.0 and entry["worktree"]["median"] == 11.0
    assert entry["delta_pct"] == pytest.approx(10.0)
    assert stage_bench.compare([0.0], [1.0])["delta_pct"] is None


def test_person_bench_block_statistics():
    """person_bench (ローカル専用) のマスクの安定性の数え方: 1 フレームだけ点滅するマス・変わるマス・空のフレーム・場面の切り替わり。"""
    import numpy as np

    sys.path.insert(0, str(ROOT / "benchmark"))
    import person_bench

    height, width = 90, 160  # 80x45 のブロックは 2x2 画素
    masks = []
    for t in range(8):
        mask = np.zeros((height, width), np.float32)
        mask[20:60, 40:100] = 1.0           # 動かない人物 (ブロック 30x20 = 600)
        if t == 3:
            mask[0:2, 0:2] = 1.0            # 1 フレームだけ現れるブロック (点滅)
        if t == 6:
            mask[:] = 0.0                   # 空のフレーム
        masks.append(mask)
    cut = np.zeros(8, bool)
    stats = person_bench.block_statistics(masks, cut)
    assert stats["frames"] == 8 and stats["empty_frames_blocks"] == 1 and stats["empty_frames_area"] == 1
    # 点滅は t = 3 の前後 (t = 2 の次に現れて t = 4 で消える) の 1 組と、空のフレームを挟む t = 5〜7 の 600 マス
    assert stats["blink_per_frame"] == pytest.approx((1 + 600) / 6)
    assert stats["changed_per_frame"] == pytest.approx((1 + 1 + 600 + 600) / 7)
    # 場面の切り替わりをまたぐ変化は含めない
    cut[6] = cut[7] = True
    assert person_bench.block_statistics(masks, cut)["changed_per_frame"] == pytest.approx(2 / 5)

    # 場面の切り替わり: 明るさの並びが似ていない 2 枚の間だけ
    rng = np.random.default_rng(0)
    scene_a = (rng.random((90, 160, 3)) * 255).astype(np.uint8)
    scene_b = (rng.random((90, 160, 3)) * 255).astype(np.uint8)
    cuts = person_bench.scene_cuts([scene_a, scene_a, scene_b, scene_b])
    assert cuts.tolist() == [False, False, True, False]
