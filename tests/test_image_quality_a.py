"""画像モードの画質の改善 (暗くしない設定・縮小の area・シートの詰め方・PNG の最適化) のテスト。"""
import io
import json
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import convert_image_mode as cim  # noqa: E402
from image_mode import atlas, budget, rp  # noqa: E402

FFMPEG_OUTPUT = """Input #0, mov,mp4,m4a,3gp,3g2,mj2, from 'a.mp4':
  Duration: 00:00:15.00, start: 0.000000, bitrate: 322 kb/s
  Stream #0:0[0x1](und): Video: h264 (High) (avc1 / 0x31637661), yuv420p(progressive), 1920x1080 [SAR 1:1 DAR 16:9], 30 fps
  Stream #0:1[0x2](und): Audio: aac (LC) (mp4a / 0x6134706D), 44100 Hz, stereo, fltp, 128 kb/s
"""


def video(**extra):
    base = {"id": "v", "width": 512, "height": 288, "fps": 24, "frame_count": 30, "cols": 3, "rows": 7,
            "cells_per_sheet": 21, "sheets": 2, "sheet_width": 1538, "sheet_height": 2022, "gutter": 1,
            "texture_paths": ["t/0", "t/1"]}
    base.update(extra)
    return base


# --- 明るさ ---

def test_bright_sets_ignore_lighting_and_world_or_missing_keeps_the_old_json():
    old = rp.render_controller_json(video(), "a", "ns")
    assert rp.render_controller_json(video(lighting="world"), "a", "ns") == old
    controller = next(iter(rp.render_controller_json(video(lighting="bright"), "a", "ns")["render_controllers"].values()))
    assert controller["ignore_lighting"] is True
    assert "ignore_lighting" not in json.dumps(old)


def frames(n=3, w=64, h=36):
    rng = np.random.default_rng(0)
    for _ in range(n):
        yield rng.integers(0, 256, (h, w, 3), dtype=np.uint8)


def build(**kw):
    files, report = cim.build_files([{"id": "v", "title": "v", "fps": 10, "frames": frames()}], width=64, height=36,
                                    methods=["a"], namespace="ns", pack_name="p", log=lambda _t: None, **kw)
    return files, report


def test_build_files_is_bright_by_default_and_world_on_request():
    files, report = build()
    assert report["videos"][0]["lighting"] == "bright"
    assert b"ignore_lighting" in files["RP/render_controllers/screen_v_a.render_controllers.json"]
    files, report = build(lighting="world")
    assert report["videos"][0]["lighting"] == "world"
    assert b"ignore_lighting" not in files["RP/render_controllers/screen_v_a.render_controllers.json"]
    with pytest.raises(cim.ConvertError):
        build(lighting="dark")


def test_cli_lighting_and_snap_options():
    args = cim.parse_args(["a.mp4"])
    assert args.lighting == "bright" and not args.snap and not args.png_optimize
    args = cim.parse_args(["a.mp4", "--lighting", "world", "--snap", "--png-optimize"])
    assert args.lighting == "world" and args.snap and args.png_optimize


# --- 縮小のフィルタ ---

def test_area_flag_only_when_downscaling():
    assert "flags=area" in atlas.build_video_filter(512, 288, 24, "pad", (1920, 1080))
    assert "flags=area" in atlas.build_video_filter(512, 288, 24, "cover", (1920, 1080))
    assert "flags" not in atlas.build_video_filter(512, 288, 24, "pad", (320, 180))
    assert atlas.build_video_filter(512, 288, 24, "pad") == atlas.build_video_filter(512, 288, 24, "pad", None)
    assert "flags" not in atlas.build_video_filter(512, 288, 24)
    # cover は長い方を合わせるので、片方だけ大きい元の動画は拡大になる
    assert not atlas.is_downscale(512, 288, (600, 200), "cover")
    assert atlas.is_downscale(512, 288, (600, 200), "pad")


def test_parse_video_size_reads_the_first_video_stream():
    assert atlas.parse_video_size(FFMPEG_OUTPUT) == (1920, 1080)
    assert atlas.parse_video_size("Stream #0:0: Audio: aac, 44100 Hz") is None


# --- シートの詰め方 ---

@pytest.mark.parametrize("size, snapped, cells", [((512, 288), (510, 286), 28), ((256, 144), (254, 142), 112)])
def test_snap_size_fits_one_more_column(size, snapped, cells):
    assert budget.snap_size(*size) == snapped
    cols, rows = atlas.grid_size(*snapped, 2048, 1)
    assert cols * rows == cells
    assert cols * (snapped[0] + 1) - 1 <= 2048


@pytest.mark.parametrize("size", [(384, 216), (448, 252), (320, 180), (128, 72)])
def test_snap_size_keeps_sizes_that_gain_nothing(size):
    assert budget.snap_size(*size) == size
    assert budget.snap_size(512, 288, gutter=0) == (512, 288)


def test_plan_snaps_when_asked_and_not_by_default():
    assert budget.plan([5], 512, 288, 24, "quality")["width"] == 512
    assert budget.plan([5], 512, 288, 24, "quality", snap=True)["width"] == 510
    # 同じ長さなら、詰めた方がメモリが少ない
    assert budget.video_bytes(15, 510, 286, 24) < budget.video_bytes(15, 512, 288, 24)


# --- PNG ---

def test_png_optimize_gives_the_same_pixels():
    img = Image.fromarray(next(frames(1, 256, 144)))
    plain, small = cim.png_bytes(img), cim.png_bytes(img, optimize=True)
    assert np.array_equal(np.asarray(Image.open(io.BytesIO(plain))), np.asarray(Image.open(io.BytesIO(small))))
    assert len(small) <= len(plain)
