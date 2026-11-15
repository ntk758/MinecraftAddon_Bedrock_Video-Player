"""画像モード: 同じ絵のコマを 1 マスにまとめる (TODO.md の P1-4)。

画像モードの長さはテクスチャのメモリで決まる。アニメの 2 コマ打ち・止め絵・同じ場面の繰り返しでは、同じ絵のコマが続くので、
1 マスにまとめるとシートの枚数が減る。コマ (時間) からマス (絵) を引く表は、クライアントの Molang で引く:

    区間 (start, slot, repeat): start コマ目から、repeat コマごとに 1 つずつ次のマスへ進む (repeat が 0 なら slot のまま)。
    マス = slot + floor((frame - start) / repeat)

区間の列は三項演算子の二分探索の式にする (lookup_expression)。2 コマ打ちが続く場面は 1 つの区間になる。
長い式は重いので、区間が MAX_INTERVALS を超えるとき・省けるマスが MIN_SAVING 未満のときはまとめない (should_use)。

mode:
  exact: まったく同じ画素のコマ (ハッシュで、離れたコマとも比べる)
  near: すぐ前に残したコマとの差 (画素ごとの差の平均、0〜255) が near_threshold 以下なら同じとみなす (動きの無い場面の
        わずかなノイズもまとめる。見た目がわずかに変わる)
"""
import hashlib
from typing import Iterable, Iterator

import numpy as np

MODES = ("off", "exact", "near")
# 区間の上限 (Molang の式の長さ)。区間 1395 個の式が PC で正しく動くことを確かめた (v6.37.0 の記録)
MAX_INTERVALS = 2048
# 省けるマスがこの割合に満たなければ、まとめない (式を長くするだけの効果が無い)
MIN_SAVING = 0.03
DEFAULT_NEAR_THRESHOLD = 1.0


class FrameDeduper:
    """コマの列から、まとめた後のコマ (マス) を順に返す。slots[i] は i コマ目のマスの番号。"""

    def __init__(self, mode: str = "exact", near_threshold: float = DEFAULT_NEAR_THRESHOLD):
        if mode not in ("exact", "near"):
            raise ValueError(f"mode は exact か near にしてください: {mode}")
        self.mode = mode
        self.near_threshold = float(near_threshold)
        self.slots: list[int] = []
        self.cells = 0

    def __call__(self, frames: Iterable[np.ndarray]) -> Iterator[np.ndarray]:
        seen: dict[bytes, int] = {}
        previous = None
        for frame in frames:
            frame = np.asarray(frame)
            if self.mode == "exact":
                key = hashlib.blake2b(frame.tobytes(), digest_size=16).digest()
                slot = seen.get(key)
                if slot is None:
                    slot = seen[key] = self.cells
                    self.cells += 1
                    yield frame
            else:
                if previous is not None and np.abs(frame.astype(np.int16) - previous).mean() <= self.near_threshold:
                    slot = self.cells - 1
                else:
                    slot = self.cells
                    self.cells += 1
                    previous = frame.astype(np.int16)
                    yield frame
            self.slots.append(slot)


def slot_intervals(slots: list[int]) -> list[tuple[int, int, int]]:
    """slots (コマ → マス) を区間 (start, slot, repeat) の列に縮める。repeat が 0 の区間はマスが変わらない。"""
    intervals: list[tuple[int, int, int]] = []
    n = len(slots)
    f = 0
    while f < n:
        slot = slots[f]
        run = 1
        while f + run < n and slots[f + run] == slot:
            run += 1
        # 次のマスが slot + 1 なら、run コマごとに 1 つ進む区間。そうでなければ、変わらない区間 (1 コマなら repeat 1)
        repeat = run if f + run < n and slots[f + run] == slot + 1 else (0 if run > 1 else 1)
        g = f
        while g < n and slots[g] == (slot + (g - f) // repeat if repeat else slot):
            g += 1
        intervals.append((f, slot, repeat))
        f = g
    return intervals


def slot_of(intervals: list[tuple[int, int, int]], frame: int) -> int:
    """区間の列から、frame コマ目のマス (lookup_expression と同じ計算。テスト用)。"""
    start, slot, repeat = max((iv for iv in intervals if iv[0] <= frame), key=lambda iv: iv[0])
    return slot + (frame - start) // repeat if repeat else slot


def lookup_expression(intervals: list[tuple[int, int, int]], frame: str = "v.frame") -> str:
    """区間の列から、コマ (frame の Molang の式) のマスを返す Molang の式 (三項演算子の二分探索)。"""
    def leaf(start, slot, repeat):
        if repeat == 0:
            return str(slot)
        if repeat == 1:
            return f"({slot} + {frame} - {start})" if start != slot else frame
        return f"({slot} + math.floor(({frame} - {start}) / {repeat}))"

    def build(lo: int, hi: int) -> str:
        if hi - lo == 1:
            return leaf(*intervals[lo])
        mid = (lo + hi) // 2
        return f"({frame} < {intervals[mid][0]} ? {build(lo, mid)} : {build(mid, hi)})"

    if not intervals:
        raise ValueError("区間が 1 つもありません")
    return build(0, len(intervals))


def should_use(frame_count: int, cells: int, intervals: list) -> bool:
    """まとめる価値があるか: 区間が MAX_INTERVALS 以下で、省けるマスが MIN_SAVING 以上。"""
    return len(intervals) <= MAX_INTERVALS and frame_count > 0 and (frame_count - cells) / frame_count >= MIN_SAVING
