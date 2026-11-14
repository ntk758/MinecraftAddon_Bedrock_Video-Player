"""画像モード: テクスチャの予算に収まるよう、何を優先するかで大きさ・fps・長さを決める。

画像モードのテクスチャは「コマの画素数 × コマ数 × 4 バイト」に近い (atlas.memory_report と同じ数え方)。長い動画ほど
増えるので、予算を超えるときは次のどれかを犠牲にする。どれを守るかをユーザーが選ぶ:

    quality  画質 (コマの大きさ) を守る。fps を下げ、それでも入らなければ長さを切る
    smooth   なめらかさ (fps) を守る。コマを小さくし、それでも入らなければ長さを切る
    length   長さ (最後まで入れる) を守る。fps とコマの大きさを交互に下げる。下限まで下げても入らなければ長さを切る

ここは数えるだけで、動画は読まない (Windows 版・コマンドライン・ブラウザ版で同じ計算を使う)。
"""
from __future__ import annotations

import math

from image_mode.atlas import MAX_CELLS, MIB, TIER_BUDGETS_MB, grid_size

PRIORITIES = ("quality", "smooth", "length")
# fps を下げるときの段 (この中で、元の fps より小さいものを上から使う)
FPS_STEPS = (30, 24, 20, 15, 12, 10)
MIN_FPS = 10
# コマを小さくするときの比 (1 段ごと) と、横の下限
SCALE_STEP = 0.85
MIN_WIDTH = 64


def video_bytes(seconds: float, width: int, height: int, fps: float, max_sheet: int = 2048, gutter: int = 1,
                max_cells: int = MAX_CELLS) -> int:
    """1 本の動画のテクスチャのバイト数 (atlas.pack_sheets と同じ並べ方: 2 枚以上なら全シートが最大の格子)。"""
    frames = max(1, math.ceil(seconds * fps))
    cols, rows = grid_size(width, height, max_sheet, gutter, max_cells)
    full = cols * rows
    if frames < full:
        cols_used = min(cols, frames)
        rows_used = math.ceil(frames / cols_used)
        texels = (cols_used * (width + gutter) - gutter) * (rows_used * (height + gutter) - gutter)
    else:
        sheets = math.ceil(frames / full)
        texels = sheets * (cols * (width + gutter) - gutter) * (rows * (height + gutter) - gutter)
    return texels * 4


# snap_size: 1 枚のマスがこの割合以上増えるときだけ縮める (小さなコマは max_cells が先に効き、縮めても数 % しか増えない)
SNAP_MIN_GAIN = 1.1


def snap_size(width: int, height: int, max_sheet: int = 2048, gutter: int = 1,
              max_cells: int = MAX_CELLS) -> tuple[int, int]:
    """ガターのせいでシートに 1 列少なくしか入らない大きさなら、縦横比を保って少し縮め、その列を入れる
    (512×288 → 510×286 で 1 枚 21 → 28 マス、シートの枚数が 3/4 に)。縮めてもマスが 1 割以上増えないなら (max_cells が先に効くなど)
    そのまま返す。"""
    if gutter <= 0 or width < 2 or height < 2:
        return width, height
    cols_now = (max_sheet + gutter) // (width + gutter)
    cols_ideal = max_sheet // width
    if cols_ideal <= cols_now:
        return width, height
    new_w = max(2, int((max_sheet + gutter) / cols_ideal - gutter) // 2 * 2)
    new_h = even(height * new_w / width)
    try:
        before = math.prod(grid_size(width, height, max_sheet, gutter, max_cells))
        after = math.prod(grid_size(new_w, new_h, max_sheet, gutter, max_cells))
    except ValueError:
        return width, height
    return (new_w, new_h) if after >= before * SNAP_MIN_GAIN else (width, height)


def total_bytes(durations: list[float], width: int, height: int, fps: float, **layout) -> int:
    return sum(video_bytes(seconds, width, height, fps, **layout) for seconds in durations)


def even(value: float) -> int:
    """偶数に丸める (ffmpeg の縮小で縦横比がずれにくい)。2 より小さくしない。"""
    return max(2, int(round(value / 2)) * 2)


def scaled(width: int, height: int, factor: float) -> tuple[int, int]:
    return even(width * factor), even(height * factor)


def plan(durations: list[float], width: int, height: int, fps: float, priority: str, target_tier: int = 2,
         max_sheet: int = 2048, gutter: int = 1, max_cells: int = MAX_CELLS, snap: bool = False) -> dict:
    """予算 (Tier target_tier) に収まる設定。snap なら、最初と大きさを下げるたびに snap_size を通す。戻り値:

    {"width", "height", "fps", "seconds": [動画ごとの長さ], "bytes", "budget_bytes", "changed": [変えたもの], "fits"}
    changed は "fps"・"size"・"length" のうち変えたもの。どうしても入らない (1 コマも入らない) ときは fits が False。
    """
    if priority not in PRIORITIES:
        raise ValueError(f"priority は {', '.join(PRIORITIES)} のどれか: {priority}")
    if target_tier not in TIER_BUDGETS_MB:
        raise ValueError(f"target_tier は {min(TIER_BUDGETS_MB)}〜{max(TIER_BUDGETS_MB)}: {target_tier}")
    layout = {"max_sheet": max_sheet, "gutter": gutter, "max_cells": max_cells}
    budget = TIER_BUDGETS_MB[target_tier] * MIB
    seconds = [max(0.0, float(s)) for s in durations]
    w, h, f = int(width), int(height), float(fps)
    if snap:
        w, h = snap_size(w, h, max_sheet, gutter, max_cells)
    changed: list[str] = []

    def fits(w=None, h=None, f=None, s=None) -> bool:
        return total_bytes(s if s is not None else seconds, w, h, f, **layout) <= budget

    def lower_fps(f: float) -> float | None:
        for step in FPS_STEPS:
            if step < f and step >= MIN_FPS:
                return float(step)
        return None

    def smaller(w: int, h: int) -> tuple[int, int] | None:
        if w <= MIN_WIDTH:
            return None
        nw, nh = scaled(w, h, SCALE_STEP)
        if nw < MIN_WIDTH:
            nw, nh = MIN_WIDTH, even(h * MIN_WIDTH / w)
        if snap:
            nw, nh = snap_size(nw, nh, max_sheet, gutter, max_cells)
        return (nw, nh) if nw < w else None

    if not fits(w, h, f):
        if priority == "quality":
            while not fits(w, h, f) and (nf := lower_fps(f)) is not None:
                f = nf
                _mark(changed, "fps")
        elif priority == "smooth":
            while not fits(w, h, f) and (size := smaller(w, h)) is not None:
                w, h = size
                _mark(changed, "size")
        else:
            turn = 0
            while not fits(w, h, f):
                nf = lower_fps(f)
                size = smaller(w, h)
                if nf is None and size is None:
                    break
                if (turn % 2 == 0 and nf is not None) or size is None:
                    f = nf
                    _mark(changed, "fps")
                else:
                    w, h = size
                    _mark(changed, "size")
                turn += 1

    if not fits(w, h, f):
        # 長さを切る: どの動画も同じ割合で短くする (二分探索)
        lo, hi = 0.0, 1.0
        for _ in range(40):
            mid = (lo + hi) / 2
            if fits(w, h, f, [s * mid for s in seconds]):
                lo = mid
            else:
                hi = mid
        seconds = [math.floor(s * lo * f) / f for s in seconds]
        _mark(changed, "length")

    nbytes = total_bytes(seconds, w, h, f, **layout)
    return {"width": w, "height": h, "fps": _num(f), "seconds": seconds, "bytes": nbytes, "budget_bytes": budget,
            "target_tier": target_tier, "priority": priority, "changed": changed,
            "fits": nbytes <= budget and all(s > 0 for s in seconds)}


def _mark(changed: list[str], item: str) -> None:
    if item not in changed:
        changed.append(item)


def _num(value: float):
    return int(value) if float(value).is_integer() else value
