"""画像モード: 同じ絵のコマを 1 マスにまとめる (image_mode/dedup.py、TODO.md の P1-4)。

- コマ → マスの表の縮め方 (区間) と、それを引く Molang の式が、どのコマでも元のマスを返す
- --dedup exact で作ったパックで、Molang の式が選ぶマスの絵が、元の動画の各コマと一致する (コマの順番が元どおり)
- 既定 (--dedup off) と、まとめる価値が無い動画は、今までと同じ出力
"""
import json
import math
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import convert_image_mode  # noqa: E402
from image_mode import rp  # noqa: E402
from image_mode.dedup import (MAX_INTERVALS, FrameDeduper, lookup_expression, should_use, slot_intervals,  # noqa: E402
                              slot_of)

FFMPEG = shutil.which("ffmpeg")


# --- Molang の式を Python で評価する (この式で使うものだけ: 数・v.frame・+ - /・<・三項・math.floor・括弧) ---

def molang(expression, frame):
    tokens = re.findall(r"\d+(?:\.\d+)?|v\.frame|math\.floor|[-+/<?:()]", expression)
    assert "".join(tokens) == expression.replace(" ", ""), "知らない記号がある"
    pos = 0

    def peek():
        return tokens[pos] if pos < len(tokens) else None

    def take(expected=None):
        nonlocal pos
        token = tokens[pos]
        assert expected is None or token == expected, (token, expected)
        pos += 1
        return token

    def conditional():
        left = additive()
        if peek() == "<":
            take()
            left = 1.0 if left < additive() else 0.0
        if peek() == "?":
            take()
            yes = conditional()
            take(":")
            no = conditional()
            return yes if left else no
        return left

    def additive():
        value = term()
        while peek() in ("+", "-"):
            op = take()
            value = value + term() if op == "+" else value - term()
        return value

    def term():
        value = factor()
        while peek() == "/":
            take()
            value = value / factor()
        return value

    def factor():
        token = take()
        if token == "(":
            value = conditional()
            take(")")
            return value
        if token == "v.frame":
            return float(frame)
        if token == "math.floor":
            take("(")
            value = conditional()
            take(")")
            return float(math.floor(value))
        return float(token)

    value = conditional()
    assert pos == len(tokens)
    return value


# --- 表と式 ---

def random_slots(rng, n):
    """2 コマ打ち・3 コマ打ち・止め・1 コマずつ・前の絵の繰り返しを混ぜたコマ → マスの表。"""
    slots, cells = [], 0
    while len(slots) < n:
        kind = rng.integers(0, 5)
        length = int(rng.integers(1, 12))
        if kind == 4 and cells:          # 前の絵をもう一度 (離れたコマの同じ絵)
            slots += [int(rng.integers(0, cells))] * length
            continue
        repeat = [1, 2, 3, 99, 1][kind]   # 4 は、まだ絵が無いときは 1 コマずつ
        for i in range(length):
            slots.append(cells + i // repeat)
        cells = max(slots) + 1
    return slots[:n]


@pytest.mark.parametrize("seed", range(20))
def test_intervals_and_the_molang_expression_give_back_every_slot(seed):
    rng = np.random.default_rng(seed)
    slots = random_slots(rng, int(rng.integers(1, 400)))
    intervals = slot_intervals(slots)
    expression = lookup_expression(intervals)
    for frame, slot in enumerate(slots):
        assert slot_of(intervals, frame) == slot
        assert molang(expression, frame) == slot, (frame, slot)


def test_two_frames_per_drawing_is_a_single_interval():
    slots = [i // 2 for i in range(600)]
    assert slot_intervals(slots) == [(0, 0, 2)]
    assert lookup_expression(slot_intervals(slots)) == "(0 + math.floor((v.frame - 0) / 2))"


def test_identical_frames_far_apart_share_a_cell():
    a, b, c = (np.full((2, 2, 3), v, np.uint8) for v in (10, 20, 30))
    deduper = FrameDeduper("exact")
    kept = list(deduper([a, b, a, c, b]))
    assert len(kept) == 3 and deduper.slots == [0, 1, 0, 2, 1]


def test_near_merges_only_tiny_differences_with_the_previous_kept_frame():
    base = np.full((4, 4, 3), 100, np.uint8)
    noisy = base.copy()
    noisy[0, 0, 0] = 110   # 平均の差 10/48 ≈ 0.2
    other = np.full((4, 4, 3), 140, np.uint8)
    deduper = FrameDeduper("near", near_threshold=1.0)
    assert len(list(deduper([base, noisy, other, other]))) == 2
    assert deduper.slots == [0, 0, 1, 1]


def test_should_use_needs_a_real_saving_and_a_short_expression():
    assert not should_use(100, 98, [(0, 0, 1)])                       # 省けるのは 2%
    assert should_use(100, 50, [(0, 0, 2)])
    assert not should_use(10000, 5000, [(i, i, 1) for i in range(MAX_INTERVALS + 1)])


# --- パックを作って確かめる ---

def two_on_two_video(path, seconds=3, fps=24):
    """アニメの 2 コマ打ち: 12 fps の絵を 24 fps で 2 回ずつ (止めの場面 1 秒つき)。"""
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s=160x90:r=12:d={seconds}",
                    "-f", "lavfi", "-i", "color=c=navy:s=160x90:r=12:d=1", "-filter_complex",
                    f"[0:v][1:v]concat=n=2:v=1:a=0,fps={fps}", "-pix_fmt", "yuv444p", "-c:v", "libx264", "-qp", "0",
                    str(path)], check=True)
    return path


def build(tmp_path, video, *extra, name="out.mcaddon"):
    output = tmp_path / name
    argv = [str(video), "--output", str(output), "--width", "64", "--height", "36", "--fps", "24", *map(str, extra)]
    assert convert_image_mode.main(argv) == 0
    archive = zipfile.ZipFile(output)
    text = archive.read("BP/scripts/image_videos.js").decode("utf-8")
    [info] = json.loads(text.split("export const IMAGE_VIDEOS = ", 1)[1].rsplit(";", 1)[0])
    return archive, info


def cell_image(archive, info, slot):
    sheet, index = divmod(slot, info["cells_per_sheet"])
    image = Image.open(archive.open(f"RP/textures/bvp_image/{info['id']}/sheet_{sheet}.png")).convert("RGB")
    step_x, step_y = info["width"] + info["gutter"], info["height"] + info["gutter"]
    x, y = (index % info["cols"]) * step_x, (index // info["cols"]) * step_y
    return np.asarray(image.crop((x, y, x + info["width"], y + info["height"])))


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg が必要")
def test_dedup_halves_the_cells_of_two_on_two_animation_and_keeps_the_order(tmp_path):
    video = two_on_two_video(tmp_path / "anime.mp4")
    plain, plain_info = build(tmp_path, video, name="plain.mcaddon")
    merged, info = build(tmp_path, video, "--dedup", "exact", name="merged.mcaddon")
    assert info["frame_count"] == plain_info["frame_count"] == 96
    # 2 コマ打ちの 3 秒 (36 枚) と止めの 1 秒 (1 枚) → 37 マス (−60%)
    assert info["cells"] == 37
    assert info["sheets"] <= plain_info["sheets"]
    # クライアントの Molang の式 (pre_animation) が選ぶマスの絵が、まとめないパックの同じコマの絵と一致する
    script = " ".join(rp.pre_animation(info, "bvpimg"))
    expression = re.search(r"v\.slot = (.*?);", script).group(1)
    for frame in range(info["frame_count"]):
        slot = int(molang(expression, frame))
        assert np.array_equal(cell_image(merged, info, slot), cell_image(plain, plain_info, frame)), frame


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg が必要")
def test_default_and_unhelpful_dedup_keep_the_output_unchanged(tmp_path):
    """既定 (off) はまとめない。exact でも、同じ絵がほとんど無い動画はまとめず、off と同じパックになる。"""
    video = tmp_path / "moving.mp4"
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=160x90:r=24:d=2", "-pix_fmt", "yuv420p",
                    str(video)], check=True)
    off, off_info = build(tmp_path, video, name="off.mcaddon")
    exact, exact_info = build(tmp_path, video, "--dedup", "exact", name="exact.mcaddon")
    assert "slot_map" not in off_info and "slot_map" not in exact_info
    for name in off.namelist():
        assert off.read(name) == exact.read(name), name
