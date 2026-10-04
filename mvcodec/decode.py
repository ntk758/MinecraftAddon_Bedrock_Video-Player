"""convert.py が出力する FRAME_DATA (varint_rle_v4) の Python 参照デコーダ。

main.js と同じ規則で盤面を再構築する。ベンチマーク (画質評価) とテストで使う。
"""
import json
from pathlib import Path

import numpy as np

from mvcodec.color import BLOCK_RGB
from mvcodec.encode import UTF16_BITS_PER_CHAR, UTF16_CHAR_OFFSET

FORMAT_V4 = "varint_rle_v4"


def load_frame_data(js_path):
    """`export const FRAME_DATA = {...};` 形式のファイルを dict として読み込む。"""
    text = Path(js_path).read_text(encoding="utf-8")
    start = text.index("{")
    end = text.rindex("}")
    return json.loads(text[start:end + 1])


def utf16_str_to_bytes(text):
    """bytearray_to_utf16_str の逆変換。"""
    if not text:
        return b""
    codes = np.frombuffer(text.encode("utf-16-le"), dtype="<u2").astype(np.int64) - UTF16_CHAR_OFFSET
    pad_len = int(codes[0])
    vals = codes[1:]
    shifts = np.arange(UTF16_BITS_PER_CHAR - 1, -1, -1)
    bits = ((vals[:, None] >> shifts) & 1).astype(np.uint8).ravel()
    bit_count = len(bits) - pad_len
    byte_len = bit_count // 8
    return np.packbits(bits[:byte_len * 8]).tobytes()


def read_varint(buf, pos):
    val = 0
    shift = 0
    while pos < len(buf):
        b = buf[pos]
        pos += 1
        val |= (b & 0x7f) << shift
        if (b & 0x80) == 0:
            break
        shift += 7
    return val, pos


def parse_index(data):
    """フレームインデックスを [(gop_id, offset, length, is_keyframe), ...] へ展開する。"""
    buf = utf16_str_to_bytes(data["index"])
    entries = []
    pos = 0
    while pos < len(buf):
        gop_id, pos = read_varint(buf, pos)
        val1, pos = read_varint(buf, pos)
        length, pos = read_varint(buf, pos)
        entries.append((gop_id, val1 >> 1, length, (val1 & 1) == 1))
    return entries


def palette_rgb_table(level_blocks):
    """level_blocks (ブロックID列) から RGB テーブル (N, 3) を作る。"""
    return np.array([BLOCK_RGB.get(spec["block"], (0, 0, 0)) for spec in level_blocks], dtype=np.uint8)


def gop_palettes(data):
    """GOP ごとの RGB パレット表を返す (固定パレット時は全GOPで同じ表)。"""
    num_gops = len(data["chunks"])
    if data.get("adaptive_palette"):
        return [palette_rgb_table(blocks) for blocks in data["level_blocks"]]
    table = palette_rgb_table(data["level_blocks"])
    return [table] * max(1, num_gops)


def iter_decoded_frames(data):
    """各フレーム表示後の盤面を (index_board, rgb_board) として順に返す。

    index_board は最後に設置したパレット番号 (未設置は -1)、rgb_board は実際に見えるブロック色。
    スキップフレームは前フレームと同じ盤面を返す (ゲーム内でも盤面は更新されない)。
    """
    if data.get("format") != FORMAT_V4:
        raise ValueError(f"未対応のフォーマットです: {data.get('format')}")
    width, height = data["width"], data["height"]
    entries = parse_index(data)
    palettes = gop_palettes(data)
    gop_bytes = {}

    index_board = np.full(width * height, -1, dtype=np.int32)
    rgb_board = np.zeros((width * height, 3), dtype=np.uint8)

    for frame_no, (gop_id, offset, length, _is_kf) in enumerate(entries):
        if length > 0:
            if gop_id not in gop_bytes:
                gop_bytes[gop_id] = utf16_str_to_bytes(data["chunks"][gop_id])
            buf = gop_bytes[gop_id]
            palette = palettes[gop_id]
            pos, end = offset, offset + length
            encoded_frame_no, pos = read_varint(buf, pos)
            if (encoded_frame_no >> 1) != frame_no:
                raise ValueError(f"フレーム番号の不一致: index={frame_no}, data={encoded_frame_no >> 1}")
            _chunk_count, pos = read_varint(buf, pos)
            while pos < end:
                val, pos = read_varint(buf, pos)
                start = val >> 13
                run = ((val >> 7) & 0x3f) + 1
                level = val & 0x7f
                index_board[start:start + run] = level
                rgb_board[start:start + run] = palette[level]
        yield index_board.reshape(height, width).copy(), rgb_board.reshape(height, width, 3).copy()
