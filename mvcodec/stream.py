"""フレーム列を varint_rle_v5 のデータ (GOP ごとのチャンクとフレームインデックス) にまとめる。

2D スクリーンでも 3D 立体でも同じ仕組みを使う。3D は (奥行き×高さ) 行 × 幅 列の 2D 画像として渡し、
depth に奥行きを指定する (動き補償だけは 3 次元のブリック単位で探す)。
"""
from dataclasses import dataclass, field

import numpy as np

from mvcodec.encode import bytearray_to_utf16_str, encode_frame_v5, encode_varint
from mvcodec.entropy import pack_gop
from mvcodec.motion import (
    MV_BLOCK,
    MV_RANGE,
    MV_RANGE_3D,
    apply_motion,
    apply_motion_3d,
    estimate_motion,
    estimate_motion_3d,
    vector_limit,
)
from mvcodec.tiles import TILE_SIZE, TileDictionary


@dataclass
class StreamStats:
    total_frames: int = 0
    skipped_frames: int = 0
    keyframes: int = 0
    motion_frames: int = 0
    tile_frames: int = 0
    tile_refs: int = 0


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
    # タイル辞書のタイルの大きさ (0 で無効)
    tile_size: int = TILE_SIZE
    # 同じフレーム列を複数の設定で符号化するとき、動き探索の結果を共有する {frame_no: vectors}
    motion_cache: dict = None
    # 立体の奥行き (1 なら平面)。2 以上なら rows = depth * 高さ で、動き補償を 3D で行う
    depth: int = 1
    # フレームを省略するかの判定に使う変化率 (前の盤面, 新しい盤面) -> 0〜1。None なら _change_ratio の既定。
    # 2.5D の平面は、組み立てた立体で測った方が立体 (voxels) と同じ判定になる (mvcodec/planes.py の volume_change_ratio)
    change_ratio_fn: object = None
    # GOP ごとのエントロピー符号化 (varint_rle_v6、mvcodec/entropy.py)。フレームの位置 (index) は v5 のバイト列のまま
    entropy: bool = False
    stats: StreamStats = field(default_factory=StreamStats)

    def __post_init__(self):
        self.gop_starts = set(self.gop_boundaries[:-1])
        self.mv_limit = MV_RANGE_3D if self.depth > 1 else vector_limit(self.width, self.rows, self.mv_range)
        self._gop_strings = []
        self._gop_binary = bytearray()
        self._gop_frames = []  # 今の GOP の (offset, length) (長さ 0 の省略フレームは含めない)
        self._index_entries = []  # (gop_id, byte_offset_in_gop, byte_length, is_keyframe)
        self._prev = None
        self._current_gop = 0
        self._gop_start = 0
        self.tiles = TileDictionary(self.width, self.rows, self.tile_size) if self.tile_size else None

    def add(self, frame_no, gop_id, frame):
        """1 フレーム (パレット番号の 1 次元配列、rows*width 個) を追加する。"""
        flat = np.asarray(frame, dtype=np.int32).reshape(-1)
        if gop_id != self._current_gop:
            self._close_gop()
            self._current_gop = gop_id
        if frame_no in self.gop_starts:
            self._gop_start = frame_no
        self.stats.total_frames += 1

        # GOP 先頭は必ずキーフレーム: パレットが変わってもシーク・遅延デコードが GOP 単体で完結する
        is_keyframe = frame_no in self.gop_starts or (
            self.keyframe_interval > 0 and (frame_no - self._gop_start) % self.keyframe_interval == 0
        )
        offset = len(self._gop_binary)
        if not is_keyframe and self.adaptive_fps and self._change_ratio(flat) < self.scene_threshold:
            self.stats.skipped_frames += 1
            self._index_entries.append((gop_id, offset, 0, False))
            return

        if is_keyframe:
            self.stats.keyframes += 1
            encoded = encode_frame_v5(frame_no, True, flat, np.full_like(flat, self.empty_value), self.width, self.rows)
        else:
            encoded = self._encode_delta(frame_no, flat)
        self._gop_binary.extend(encoded)
        self._gop_frames.append((offset, len(encoded)))
        self._index_entries.append((gop_id, offset, len(encoded), is_keyframe))
        if self.tiles is not None:
            self.tiles.update(self._prev, flat, is_keyframe)
        self._prev = flat

    @property
    def reference(self):
        """最後に符号化した (アドオンが表示している) 盤面の 1 次元配列。まだ無ければ None。"""
        return self._prev

    def _change_ratio(self, flat):
        """前フレームから変わった割合。立体 (空気が基準) では、空気以外のマスに対する割合にする
        (ほとんどが空気の立体では全体に対する割合が小さく出て、動いているのに省略されてしまうため)。"""
        if self.change_ratio_fn is not None:
            return self.change_ratio_fn(self._prev, flat)
        changed = int(np.count_nonzero(flat != self._prev))
        if self.empty_value >= 0:
            solid = int(np.count_nonzero((flat != self.empty_value) | (self._prev != self.empty_value)))
            return changed / max(1, solid)
        return changed / len(flat)

    def _encode_delta(self, frame_no, flat):
        """前フレームとの差分を、動き補償・タイル参照の有無の組み合わせで作り、一番小さいものを返す (悪化はしない)。"""
        predictions = [({}, self._prev)]
        if self.motion:
            if self.motion_cache is not None and frame_no in self.motion_cache:
                vectors = self.motion_cache[frame_no]
            else:
                vectors = self._estimate_motion(flat)
                if self.motion_cache is not None:
                    self.motion_cache[frame_no] = vectors
            if vectors:
                predictions.append((vectors, self._apply_motion(vectors)))

        best, best_kind = None, (False, False)
        for vectors, predicted in predictions:
            variants = [([], predicted)]
            if self.tiles is not None and len(self.tiles):
                ops, with_tiles = self.tiles.find_ops(flat, predicted)
                if ops:
                    variants.append((ops, with_tiles))
            for ops, reference in variants:
                encoded = encode_frame_v5(frame_no, False, flat, reference, self.width, self.rows,
                                          vectors, self.mv_limit, ops)
                if best is None or len(encoded) < len(best):
                    best, best_kind, best_ops = encoded, (bool(vectors), bool(ops)), ops
        if best_kind[0]:
            self.stats.motion_frames += 1
        if best_kind[1]:
            self.stats.tile_frames += 1
            self.stats.tile_refs += len(best_ops)
        return best

    def _estimate_motion(self, flat):
        if self.depth > 1:
            return estimate_motion_3d(self._prev, flat, self.width, self.rows // self.depth, self.depth,
                                      MV_BLOCK, self.mv_limit)
        return estimate_motion(self._prev, flat, self.width, self.rows, MV_BLOCK, self.mv_range)

    def _apply_motion(self, vectors):
        if self.depth > 1:
            return apply_motion_3d(self._prev, vectors, self.width, self.rows // self.depth, self.depth, MV_BLOCK)
        return apply_motion(self._prev, vectors, self.width, self.rows, MV_BLOCK)

    def payload_size(self):
        """ここまでのデータの大きさ (UTF-16 文字に詰める前のバイト数の目安)。"""
        return sum(len(s) for s in self._gop_strings) * 2 + len(self._gop_binary)

    def _close_gop(self):
        data = pack_gop(self._gop_binary, self._gop_frames) if self.entropy else self._gop_binary
        self._gop_strings.append(bytearray_to_utf16_str(data))
        self._gop_binary = bytearray()
        self._gop_frames = []

    def close_open_gop(self):
        """作りかけの GOP を閉じる (EncoderSet.best が、入れ物に入れた後の大きさで比べるため)。この後は add しないこと。"""
        if self._gop_binary or self._gop_frames:
            self._close_gop()
            self._closed_last = True

    def finish(self):
        """(GOP ごとのチャンク文字列のリスト, フレームインデックスの文字列) を返す。"""
        if not getattr(self, "_closed_last", False):
            self._close_gop()
        index_data = bytearray()
        for gop_id, offset, length, is_keyframe in self._index_entries:
            encode_varint(gop_id, index_data)
            encode_varint((offset << 1) | (1 if is_keyframe else 0), index_data)
            encode_varint(length, index_data)
        return self._gop_strings, bytearray_to_utf16_str(index_data)
