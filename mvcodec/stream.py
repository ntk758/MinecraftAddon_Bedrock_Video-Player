"""フレーム列を varint_rle_v5 のデータ (GOP ごとのチャンクとフレームインデックス) にまとめる。

2D スクリーンでも 3D 立体でも同じ仕組みを使う。3D は (奥行き×高さ) 行 × 幅 列の 2D 画像として渡す。
"""
from dataclasses import dataclass, field

import numpy as np

from mvcodec.encode import bytearray_to_utf16_str, encode_frame_v5, encode_varint
from mvcodec.motion import MV_BLOCK, MV_RANGE, apply_motion, estimate_motion, vector_limit


@dataclass
class StreamStats:
    total_frames: int = 0
    skipped_frames: int = 0
    keyframes: int = 0
    motion_frames: int = 0


@dataclass
class StreamEncoder:
    width: int
    rows: int
    gop_boundaries: list
    keyframe_interval: int = 30
    adaptive_fps: bool = True
    scene_threshold: float = 0.015
    motion: bool = True
    mv_range: int = MV_RANGE
    # キーフレームを符号化するときの「何も無い」状態 (2D は -1 = 未設置、3D は空気)
    empty_value: int = -1
    stats: StreamStats = field(default_factory=StreamStats)

    def __post_init__(self):
        self.gop_starts = set(self.gop_boundaries[:-1])
        self.mv_limit = vector_limit(self.width, self.rows, self.mv_range)
        self._gop_strings = []
        self._gop_binary = bytearray()
        self._index_entries = []  # (gop_id, byte_offset_in_gop, byte_length, is_keyframe)
        self._prev = None
        self._current_gop = 0
        self._gop_start = 0

    def add(self, frame_no, gop_id, frame):
        """1 フレーム (パレット番号の 1 次元配列、rows*width 個) を追加する。"""
        flat = np.asarray(frame, dtype=np.int32).reshape(-1)
        if gop_id != self._current_gop:
            self._gop_strings.append(bytearray_to_utf16_str(self._gop_binary))
            self._gop_binary = bytearray()
            self._current_gop = gop_id
        if frame_no in self.gop_starts:
            self._gop_start = frame_no
        self.stats.total_frames += 1

        # GOP 先頭は必ずキーフレーム: パレットが変わってもシーク・遅延デコードが GOP 単体で完結する
        is_keyframe = frame_no in self.gop_starts or (
            self.keyframe_interval > 0 and (frame_no - self._gop_start) % self.keyframe_interval == 0
        )
        offset = len(self._gop_binary)
        if not is_keyframe and self.adaptive_fps and np.mean(flat != self._prev) < self.scene_threshold:
            self.stats.skipped_frames += 1
            self._index_entries.append((gop_id, offset, 0, False))
            return

        if is_keyframe:
            self.stats.keyframes += 1
            encoded = encode_frame_v5(frame_no, True, flat, np.full_like(flat, self.empty_value), self.width, self.rows)
        else:
            encoded = self._encode_delta(frame_no, flat)
        self._gop_binary.extend(encoded)
        self._index_entries.append((gop_id, offset, len(encoded), is_keyframe))
        self._prev = flat

    def _encode_delta(self, frame_no, flat):
        encoded = encode_frame_v5(frame_no, False, flat, self._prev, self.width, self.rows)
        if self.motion:
            # 動き補償版も作り、小さくなる場合だけ採用する (悪化はしない)
            vectors = estimate_motion(self._prev, flat, self.width, self.rows, MV_BLOCK, self.mv_range)
            if vectors:
                predicted = apply_motion(self._prev, vectors, self.width, self.rows, MV_BLOCK)
                with_motion = encode_frame_v5(frame_no, False, flat, predicted, self.width, self.rows,
                                              vectors, self.mv_limit)
                if len(with_motion) < len(encoded):
                    self.stats.motion_frames += 1
                    return with_motion
        return encoded

    def finish(self):
        """(GOP ごとのチャンク文字列のリスト, フレームインデックスの文字列) を返す。"""
        self._gop_strings.append(bytearray_to_utf16_str(self._gop_binary))
        index_data = bytearray()
        for gop_id, offset, length, is_keyframe in self._index_entries:
            encode_varint(gop_id, index_data)
            encode_varint((offset << 1) | (1 if is_keyframe else 0), index_data)
            encode_varint(length, index_data)
        return self._gop_strings, bytearray_to_utf16_str(index_data)
