"""ディザの見え方の物差し (benchmark/metrics/dither_view.py、benchmark/dither_view_bench.py) のテスト。

自作の合成画像だけを使う。閾値は試作の値に余裕を持たせたもの。
"""
import json
import math
import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("skimage")
from PIL import Image  # noqa: E402
from skimage.color import deltaE_ciede2000, rgb2lab  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "benchmark")]

from dither_view_bench import judge  # noqa: E402
from metrics.dither_view import (cells_per_degree, flicker, frame_metrics, gaussian_blur, gaussian_kernel,  # noqa: E402
                                 grain_bands, grain_removed, grain_visible, srgb_to_linear, static_mask, view_error, view_lab)

GRAY = ((120, 120, 120), (140, 140, 140))
HUE = ((170, 90, 90), (90, 150, 110))


def blue(h, w, seed=1):
    """簡単な Blue Noise (白色雑音の高域を取り、順位で一様にする)。"""
    noise = np.random.default_rng(seed).random((h, w))
    high = noise - gaussian_blur(noise, 1.5)
    out = np.empty(high.size)
    out[np.argsort(high.ravel())] = (np.arange(high.size) + 0.5) / high.size
    return out.reshape(h, w)


def dither(h, w, a, b, seed=1):
    mask = blue(h, w, seed) < 0.5
    out = np.empty((h, w, 3), np.uint8)
    out[mask], out[~mask] = a, b
    return out


def linear_mix(a, b):
    mixed = (srgb_to_linear(np.array(a, np.uint8)) + srgb_to_linear(np.array(b, np.uint8))) / 2
    return np.round(255 * np.where(mixed <= 0.0031308, 12.92 * mixed, 1.055 * mixed ** (1 / 2.4) - 0.055)).astype(np.uint8)


def flat_target(h, w, a, b):
    return np.full((h, w, 3), linear_mix(a, b), np.uint8)


def textured(h=72, w=128):
    yy, xx = np.mgrid[:h, :w]
    return np.stack([128 + 90 * np.sin(xx * 1.4) * np.sin(yy * 1.1), 128 + 60 * np.sin(xx * 0.9), 100 + 0 * xx], -1).astype(np.uint8)


def add_noise(img, sigma, seed=3):
    return np.clip(img + np.random.default_rng(seed).normal(0, sigma, img.shape), 0, 255).astype(np.uint8)


def wave(h, w, shift):
    xx = np.mgrid[:h, :w][1]
    return np.stack([128 + 100 * np.sin((xx + 5 * shift) * 0.3)] * 3, -1).astype(np.uint8)


# ---------------------------------------------------------------------------
# 核とぼかし
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("sigma", [1e-6, 0.1, 0.41, 2.0, 13.6])
def test_kernel_is_normalized_and_symmetric(sigma):
    kernel = gaussian_kernel(sigma)
    assert kernel.sum() == pytest.approx(1)
    assert np.allclose(kernel, kernel[::-1])


def test_kernel_tends_to_identity_and_stays_gaussian_below_half_a_cell():
    img = np.random.default_rng(0).random((16, 20, 3))
    assert np.allclose(gaussian_blur(img, 1e-6), img)
    assert np.allclose(gaussian_blur(img, 0), img)
    kernel = gaussian_kernel(0.1)
    assert kernel[len(kernel) // 2] > 0.999
    # 広いσの分散は σ² + 箱の 1/12
    wide = gaussian_kernel(3.0)
    x = np.arange(len(wide)) - len(wide) // 2
    assert (wide * x * x).sum() == pytest.approx(9 + 1 / 12, abs=0.01)


def test_blur_edges_behave_like_nearest():
    img = np.full((10, 10), 7.0)
    assert np.allclose(gaussian_blur(img, 3.0), 7.0)


def test_view_without_blur_matches_plain_lab():
    img = textured()
    assert np.abs(view_lab(img, 1e-9) - rgb2lab(img)).max() < 0.05


def test_gray_dither_barely_leaks_into_ab():
    """反対色の行列の符号を間違えると、灰色のディザが a, b へ漏れて誤差が 2 倍近くになる (試作の初回の実例)。

    今の行列でも白が反対色の 0 にならないので 21% ほど漏れる (L 3.63 に ab 0.76)。符号を 1 つ間違えると ab が L を超える (1.8 倍以上)。
    """
    h, w = 144, 256
    source, shown = flat_target(h, w, *GRAY), dither(h, w, *GRAY)
    grain = grain_visible(source, shown, cells_per_degree(h, 1))
    assert grain["L"] > 2
    assert grain["ab"] < 0.4 * grain["L"]
    assert view_error(source, shown, cells_per_degree(72, 1)) < 4.5


# ---------------------------------------------------------------------------
# 同じ画像・ディザの距離ごとの動き
# ---------------------------------------------------------------------------

def test_identical_images_score_zero():
    img = textured()
    cpd = cells_per_degree(72, 2)
    assert view_error(img, img, cpd) == pytest.approx(0, abs=1e-9)
    assert grain_visible(img, img, cpd) == pytest.approx({"all": 0, "L": 0, "ab": 0}, abs=1e-9)
    assert grain_removed(img, img, cpd) == pytest.approx(0, abs=1e-9)
    assert all(v is None or v == pytest.approx(0, abs=1e-9) for v in grain_bands(img, img, cpd)["bands"])
    result = flicker([img] * 8, [img] * 8, 72)
    assert result["flicker_rate"] == 0
    assert all(v == pytest.approx(0, abs=1e-9) for v in result["flicker_mag"].values())


def test_flat_dither_disappears_far_away_but_stays_as_removed_grain():
    h, w = 144, 256
    source, shown = flat_target(h, w, *GRAY), dither(h, w, *GRAY)
    pixel = float(deltaE_ciede2000(rgb2lab(source), rgb2lab(shown)).mean())
    cpd = cells_per_degree(h, 4)
    error = view_error(source, shown, cpd)
    # 明るさの細い山 (重み 0.9、半値全幅 0.05 度) は k=4 でも σ≈0.5 マスなので、半分ほどは残る (実測 0.48)
    assert error < 0.6 * pixel
    assert error < 0.6 * view_error(source, shown, cells_per_degree(h, 1))
    assert grain_removed(source, shown, cpd) > 1.2 * error  # 実測 1.39 倍


@pytest.mark.parametrize("h,w", [(72, 128), (144, 256)])
def test_stronger_blur_lowers_dither_error_and_visible_grain(h, w):
    source, shown = flat_target(h, w, *GRAY), dither(h, w, *GRAY)
    cpds = [cells_per_degree(h, k) for k in (1, 2, 4)]
    errors = [view_error(source, shown, c) for c in cpds]
    visible = [grain_visible(source, shown, c)["all"] for c in cpds]
    removed = [grain_removed(source, shown, c) for c in cpds]
    assert errors[0] > errors[1] > errors[2]
    assert visible[0] > visible[1] > visible[2]
    assert removed[0] < removed[1] < removed[2]


def test_hue_dither_follows_the_same_direction():
    h, w = 72, 128
    source, shown = flat_target(h, w, *HUE), dither(h, w, *HUE)
    cpds = [cells_per_degree(h, k) for k in (1, 2, 4)]
    errors = [view_error(source, shown, c) for c in cpds]
    visible = [grain_visible(source, shown, c)["all"] for c in cpds]
    assert errors[0] > errors[1] > errors[2]
    assert visible[0] > visible[1] > visible[2]


def test_only_height_times_distance_matters():
    small, big = (72, 128), (144, 256)
    a = flat_target(*small, *GRAY), dither(*small, *GRAY)
    b = flat_target(*big, *GRAY), dither(*big, *GRAY)
    cpd_a, cpd_b = cells_per_degree(72, 2), cells_per_degree(144, 1)
    assert cpd_a == pytest.approx(cpd_b)
    assert view_error(*a, cpd_a) == pytest.approx(view_error(*b, cpd_b), abs=0.02)
    assert grain_visible(*a, cpd_a)["all"] == pytest.approx(grain_visible(*b, cpd_b)["all"], abs=0.02)


def test_frame_metrics_agree_with_the_single_functions():
    source, shown = textured(), add_noise(textured(), 8)
    out = frame_metrics(source, shown, 72, (2,))[2]
    cpd = cells_per_degree(72, 2)
    assert out["view_error"] == pytest.approx(view_error(source, shown, cpd))
    assert out["grain_visible"] == pytest.approx(grain_visible(source, shown, cpd))
    assert out["grain_removed"] == pytest.approx(grain_removed(source, shown, cpd))


# ---------------------------------------------------------------------------
# 元の細部を粒に数えない
# ---------------------------------------------------------------------------

def test_lost_detail_and_global_shift_are_not_grain():
    source = textured()
    cpd = cells_per_degree(72, 2)
    smooth = np.clip(gaussian_blur(source.astype(np.float64), 3), 0, 255).astype(np.uint8)
    assert grain_visible(source, smooth, cpd)["all"] < 1e-9
    assert view_error(source, smooth, cpd) > 0.5
    shifted = np.clip(source.astype(int) + 10, 0, 255).astype(np.uint8)
    assert grain_visible(source, shifted, cpd)["all"] < 0.05
    assert view_error(source, shifted, cpd) > 0.5


def test_same_noise_scores_alike_on_flat_and_textured_sources():
    h, w = 72, 128
    cpd = cells_per_degree(h, 2)
    flat = np.full((h, w, 3), 128, np.uint8)
    on_flat = grain_visible(flat, add_noise(flat, 12), cpd)["all"]
    on_texture = grain_visible(textured(), add_noise(textured(), 12), cpd)["all"]
    assert 0.75 < on_texture / on_flat < 1.25


# ---------------------------------------------------------------------------
# ちらつき
# ---------------------------------------------------------------------------

def test_frozen_dither_has_grain_but_no_flicker():
    h, w, n = 72, 128, 12
    source = np.full((h, w, 3), linear_mix(*GRAY), np.uint8)
    shown = dither(h, w, *GRAY)
    result = flicker([source] * n, [shown] * n, h)
    assert result["flicker_rate"] == 0
    assert all(v == 0 for v in result["flicker_mag"].values())
    assert grain_visible(source, shown, cells_per_degree(h, 2))["all"] > 2


def test_regenerated_dither_and_temporal_noise_flicker():
    h, w, n = 72, 128, 12
    source = [np.full((h, w, 3), 128, np.uint8)] * n
    regenerated = flicker(source, [dither(h, w, *GRAY, seed=i) for i in range(n)], h)
    assert regenerated["flicker_rate"] == pytest.approx(0.5, abs=0.05)
    noisy = flicker(source, [add_noise(source[0], 6, seed=i) for i in range(n)], h)
    assert noisy["flicker_rate"] > 0.9
    # k=2 の値で確かめる。遠いほど打ち消されるので、k が増えると下がる
    assert noisy["flicker_mag"][2] > 3
    assert regenerated["flicker_mag"][1] > regenerated["flicker_mag"][4]
    assert regenerated["flicker_mag"][1] > 1


def test_moving_source_and_slow_fade_are_not_static():
    h, w, n = 72, 128, 12
    moving = [wave(h, w, i) for i in range(n)]
    fade = [np.full((h, w, 3), 100 + 3 * i, np.uint8) for i in range(n)]
    for frames in (moving, fade):
        result = flicker(frames, frames, h)
        assert result["static_coverage"] == 0
        assert result["flicker_rate"] is None
        assert all(v is None for v in result["flicker_mag"].values())


def test_only_the_static_half_is_counted():
    h, w, n = 72, 128, 12
    xx = np.mgrid[:h, :w][1][..., None]
    source = [np.where(xx < w // 2, 128, wave(h, w, i)).astype(np.uint8) for i in range(n)]
    # 右半分は表示も動くが数えない。左半分は固定のディザ
    fixed = dither(h, w, *GRAY)
    frozen = flicker(source, [np.where(xx < w // 2, fixed, frame) for frame in source], h)
    assert frozen["flicker_rate"] == 0
    assert 0.3 < frozen["static_coverage"] < 0.5
    regenerated = flicker(source, [np.where(xx < w // 2, dither(h, w, *GRAY, seed=i), frame) for i, frame in enumerate(source)], h)
    assert regenerated["flicker_rate"] == pytest.approx(0.5, abs=0.05)


def test_scene_cut_leaves_static_around_the_cut():
    h, w, n, cut = 72, 128, 12, 6
    frames = [np.full((h, w, 3), 60 if i < cut else 180, np.uint8) for i in range(n)]
    labs = np.stack([view_lab(f, cells_per_degree(h, 2)) for f in frames])
    mask = static_mask(labs)
    assert mask[:cut - 2].all() and mask[cut + 2:].all()
    assert not mask[cut - 2:cut + 2].any()
    # 切り替わりの変化そのものは数えない
    assert flicker(frames, frames, h)["flicker_rate"] == 0


def test_min_static_is_configurable_and_tau_moves_coverage():
    h, w, n = 36, 64, 8
    frames = [np.full((h, w, 3), 100 + i, np.uint8) for i in range(n)]
    strict, loose = flicker(frames, frames, h, tau=0.1), flicker(frames, frames, h, tau=50)
    assert strict["static_coverage"] < loose["static_coverage"]
    assert loose["flicker_rate"] is not None and strict["flicker_rate"] is None
    assert flicker(frames, frames, h, tau=50, min_static=1.01)["flicker_rate"] is None


# ---------------------------------------------------------------------------
# 明るさの帯
# ---------------------------------------------------------------------------

def test_noise_in_the_dark_lifts_only_the_dark_band():
    h, w = 72, 128
    source = np.repeat(np.linspace(0, 255, w)[None, :, None], h, 0).repeat(3, 2).astype(np.uint8)
    shown = source.copy()
    dark = np.mgrid[:h, :w][1] < w // 4
    shown[dark] = add_noise(source, 10)[dark]
    result = grain_bands(source, shown, cells_per_degree(h, 2))
    bands = result["bands"]
    assert bands[0] > 5 * bands[1]
    assert bands[2] < 0.1 and bands[3] < 0.1
    assert sum(result["fractions"]) == pytest.approx(1)


def test_band_under_one_percent_is_null():
    source = np.full((72, 128, 3), 128, np.uint8)
    result = grain_bands(source, add_noise(source, 6), cells_per_degree(72, 2))
    assert result["fractions"][2] > 0.99
    assert result["bands"][0] is None and result["bands"][1] is None and result["bands"][3] is None
    assert result["bands"][2] > 0


# ---------------------------------------------------------------------------
# 採用基準の判定
# ---------------------------------------------------------------------------

def bench_row(**values):
    row = {"view_error_k1": 6.0, "view_error_k2": 5.0, "view_error_k4": 4.0, "grain_visible_k1": 6.0, "grain_visible_k2": 5.0,
           "grain_visible_k4": 2.0, "flicker_rate": 0.002, "flicker_mag_k2": 1.0, "zip_bytes": 1000, "best_us": 500.0,
           "convert_s": 10.0}
    return {**row, **values}


def test_judge_passes_a_grain_improvement():
    verdict = judge({"a": bench_row()}, {"a": bench_row(grain_visible_k2=4.4, grain_visible_k1=5.9)})
    assert verdict["passed"] and verdict["improved"]


def test_judge_rejects_a_change_without_improvement_or_with_a_cost():
    assert not judge({"a": bench_row()}, {"a": bench_row()})["improved"]
    better = {"grain_visible_k2": 4.0}
    assert not judge({"a": bench_row()}, {"a": bench_row(**better, grain_visible_k4=2.2)})["improved"]  # k=4 の悪化
    assert not judge({"a": bench_row()}, {"a": bench_row(**better, view_error_k2=5.1)})["passed"]
    assert not judge({"a": bench_row()}, {"a": bench_row(**better, zip_bytes=1060)})["passed"]
    worst = judge({"a": bench_row(), "b": bench_row()},
                  {"a": bench_row(**better, best_us=400.0), "b": bench_row(**better, best_us=600.0)})
    assert not worst["passed"]  # 平均は下がっても 1 本で +20%


def test_judge_ignores_small_flicker_rate_and_skips_na():
    verdict = judge({"a": bench_row()}, {"a": bench_row(flicker_rate=0.0001)})
    rate = verdict["flicker"][0]
    assert rate["status"] == "skip" and rate["before"] == 0.002
    assert not verdict["improved"]
    assert judge({"a": bench_row(flicker_rate=0.2)}, {"a": bench_row(flicker_rate=0.1)})["improved"]
    assert judge({"a": bench_row(flicker_rate=None)}, {"a": bench_row(flicker_rate=None)})["flicker"][0]["clips"] == 0


# ---------------------------------------------------------------------------
# CLI の smoke
# ---------------------------------------------------------------------------

needs_tools = pytest.mark.skipif(shutil.which("node") is None or shutil.which("ffmpeg") is None, reason="node と ffmpeg が必要")


def run_bench(*args):
    result = subprocess.run([sys.executable, str(ROOT / "benchmark" / "dither_view_bench.py"), *args], capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=300, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
    return result


EXPECTED_KEYS = {"clip", "variant", "size", "frames", "static_coverage", "flicker_rate", "deltaE2000", "deltaE2000_blur", "zip_bytes",
                 "best_us", "grain_band_fraction", *(f"{name}_k{k}" for k in (1, 2, 4) for name in (
                     "view_error", "grain_visible", "grain_visible_L", "grain_visible_ab", "grain_removed", "grain_band", "flicker_mag"))}


@needs_tools
def test_clip_run_writes_expected_keys(tmp_path):
    out = tmp_path / "out"
    run_bench("--out", str(out), "--clips", "anime", "--frames", "12", "--size", "64x36", "--workers", "1")
    results = json.loads((out / "results.json").read_text(encoding="utf-8"))
    assert results["schema"] == 1
    row = results["rows"][0]
    assert EXPECTED_KEYS <= set(row)
    assert row["clip"] == "anime" and row["variant"] == "base" and row["frames"] == 12
    assert results["summary"][0]["clips"] == 1
    assert (out / "results.md").read_text(encoding="utf-8").startswith("# ")


@needs_tools
def test_frames_root_hides_the_source_names_and_from_js_measures_a_converted_file(tmp_path):
    root = tmp_path / "SOURCE_SECRET_ROOT"
    folder = root / "SOURCE_SECRET_CLIP_64x36"
    folder.mkdir(parents=True)
    for i in range(8):
        Image.fromarray(np.full((36, 64, 3), 60 + 10 * (i // 4), np.uint8)).save(folder / f"frame_{i:04d}.png")
    out = tmp_path / "out"
    run_bench("--out", str(out), "--frames-root", str(root), "--set-label", "ジャンル", "--workers", "1", "--variant", "plain=--dither-method none")
    for name in ("results.json", "results.md"):
        text = (out / name).read_text(encoding="utf-8")
        assert "SECRET" not in text
        assert "ジャンル-01" in text
    assert not [p for p in out.rglob("*") if "SECRET" in p.name]

    js = next((out / "js").glob("*.js"))
    again = tmp_path / "again"
    run_bench("--out", str(again), "--from-js", f"direct={js}", "--frames-dir", str(folder), "--set-label", "one")
    results = json.loads((again / "results.json").read_text(encoding="utf-8"))
    assert results["rows"][0]["clip"] == "one" and results["rows"][0]["variant"] == "direct"
    assert "SECRET" not in (again / "results.md").read_text(encoding="utf-8")
    assert math.isfinite(results["rows"][0]["view_error_k2"])
