"""convert.py が出力する FRAME_DATA (varint_rle_v4) の Python 参照デコーダ。

main.js と同じ規則で盤面を再構築する。ベンチマーク (画質評価) とテストで使う。
"""
import json
from pathlib import Path

import numpy as np

from mvcodec.color import BLOCK_RGB
from mvcodec.encode import UTF16_BITS_PER_CHAR, UTF16_CHAR_OFFSET
from mvcodec.entropy import unpack_gop
from mvcodec.motion import apply_motion, apply_motion_3d, unpack_vector, unpack_vector_3d
from mvcodec.planes import LAYOUT as HEIGHTFIELD, planes_to_volume
from mvcodec.tiles import TileDictionary

FORMAT_V4 = "varint_rle_v4"
FORMAT_V5 = "varint_rle_v5"
# v5 のフレームを GOP ごとにエントロピー符号化した入れ物 (mvcodec/entropy.py)。開けば v5 と同じ
FORMAT_V6 = "varint_rle_v6"
V5_FAMILY = (FORMAT_V5, FORMAT_V6)


def gop_bytes_of(data, gop_id):
    """GOP の v5 のバイト列 (v6 は入れ物を開く)。"""
    raw = utf16_str_to_bytes(data["chunks"][gop_id])
    return unpack_gop(raw) if data.get("format") == FORMAT_V6 else raw


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


def is_heightfield(data):
    """2.5D の立体 (色の平面 + 値の平面で送る。mvcodec/planes.py) か。"""
    return data.get("layout") == HEIGHTFIELD


def empty_value(data):
    """キーフレームの描き始めの状態: 立体は空気、平面スクリーンと 2.5D の平面は未設置 (-1)。"""
    return data["air_level"] if data.get("layout") == "volume" else -1


def board_shape(data):
    """表示する盤面の形: 平面は (高さ, 幅)、立体 (2.5D を含む) は (奥行き, 高さ, 幅)。"""
    if data.get("layout") in ("volume", HEIGHTFIELD):
        return data["depth"], data["height"], data["width"]
    return data["height"], data["width"]


def stream_rows(data):
    """符号化された 2D 画像の行数: 立体は (奥行き × 高さ)、2.5D は (2 × 高さ)、平面は高さ。"""
    if data.get("layout") == "volume":
        return data["depth"] * data["height"]
    if is_heightfield(data):
        return 2 * data["height"]
    return data["height"]


def new_tile_dictionary(data):
    """データがタイル辞書を使っていれば、デコーダ側の辞書を作る (使っていなければ None)。"""
    tile = data.get("tile_size", 0)
    if not tile or data.get("format") not in V5_FAMILY:
        return None
    return TileDictionary(data["width"], stream_rows(data), tile, data.get("tile_dict_max", 32768), index_contents=False)


def decode_frame(buf, offset, length, board, data, frame_no=None, tiles=None):
    """1 フレームを復元して新しい盤面 (1 次元, パレット番号, 未設置 -1) と、キーフレームかどうかを返す。

    board は直前の盤面。codec.js の decodeFrameInto と同じ規則:
    キーフレームは全画素を未設置から描き直し、動きベクトルは board から予測し (立体は 3D のブリック単位)、
    最後に差分ランを上書きする。
    """
    width = data["width"]
    volume = data.get("layout") == "volume"
    height = stream_rows(data)
    is_v5 = data.get("format") in V5_FAMILY
    pos, end = offset, offset + length
    header, pos = read_varint(buf, pos)
    shift = 3 if is_v5 else 1
    if frame_no is not None and (header >> shift) != frame_no:
        raise ValueError(f"フレーム番号の不一致: index={frame_no}, data={header >> shift}")
    is_keyframe = (header & 1) == 1
    has_motion = is_v5 and (header >> 1) & 1 == 1
    has_tiles = is_v5 and (header >> 2) & 1 == 1

    out = np.full_like(board, empty_value(data)) if is_keyframe else board.copy()
    if has_motion:
        count, pos = read_varint(buf, pos)
        vectors = {}
        mb = -1
        for _ in range(count):
            gap, pos = read_varint(buf, pos)
            packed, pos = read_varint(buf, pos)
            mb += gap + 1
            vectors[mb] = (unpack_vector_3d if volume else unpack_vector)(packed, data["mv_range"])
        if volume:
            out = apply_motion_3d(board, vectors, width, data["height"], data["depth"], data["mv_block"])
        else:
            out = apply_motion(board, vectors, width, height, data["mv_block"])
    if has_tiles:
        count, pos = read_varint(buf, pos)
        ops = []
        tile_index = -1
        for _ in range(count):
            gap, pos = read_varint(buf, pos)
            recency, pos = read_varint(buf, pos)
            tile_index += gap + 1
            ops.append((tile_index, recency))
        tiles.apply_ops(out, ops)
    _chunk_count, pos = read_varint(buf, pos)
    previous_end = 0
    while pos < end:
        val, pos = read_varint(buf, pos)
        # v5 は直前のランの終わりからの距離、v4 は絶対位置
        start = previous_end + (val >> 13) if is_v5 else val >> 13
        run = ((val >> 7) & 0x3f) + 1
        out[start:start + run] = val & 0x7f
        previous_end = start + run
    return out, is_keyframe


def iter_decoded_frames(data):
    """各フレーム表示後の盤面を (index_board, rgb_board) として順に返す。

    index_board は最後に設置したパレット番号 (未設置は -1、立体の空気は air_level)、rgb_board は実際に見える
    ブロック色 (空気は黒)。形は平面なら (高さ, 幅)、立体なら (奥行き, 高さ, 幅)。
    ゲーム内と同じく、パレット番号が変わった画素だけをそのフレームの GOP のパレットで塗り直す。
    スキップフレームは前フレームと同じ盤面を返す。
    """
    if data.get("format") not in (FORMAT_V4, *V5_FAMILY):
        raise ValueError(f"未対応のフォーマットです: {data.get('format')}")
    shape = board_shape(data)
    pixels = int(np.prod(shape))
    entries = parse_index(data)
    palettes = gop_palettes(data)
    if data.get("layout") in ("volume", HEIGHTFIELD):
        # 空気の番号まで引けるよう、パレット表を 128 色に広げる (空気は黒)
        palettes = [np.vstack([table, np.zeros((128 - len(table), 3), np.uint8)]) for table in palettes]
    gop_bytes = {}
    tiles = new_tile_dictionary(data)
    heightfield = is_heightfield(data)

    index_board = np.full(pixels, -1, dtype=np.int32)
    rgb_board = np.zeros((pixels, 3), dtype=np.uint8)
    # 2.5D は復元するのが平面 (2H 行) で、表示する盤面はそれを組み立てた立体
    stream = np.full(stream_rows(data) * data["width"], -1, dtype=np.int32) if heightfield else index_board

    for frame_no, (gop_id, offset, length, _is_kf) in enumerate(entries):
        if length > 0:
            if gop_id not in gop_bytes:
                gop_bytes[gop_id] = gop_bytes_of(data, gop_id)
            new_stream, is_keyframe = decode_frame(gop_bytes[gop_id], offset, length, stream, data, frame_no, tiles)
            if tiles is not None:
                tiles.update(stream, new_stream, is_keyframe)
            stream = new_stream
            if heightfield:
                planes = new_stream.reshape(2 * data["height"], data["width"])
                new_board = planes_to_volume(planes, data["depth"], data["solid_rule"]).reshape(-1).astype(np.int32)
            else:
                new_board = new_stream
            changed = np.ones(len(new_board), dtype=bool) if is_keyframe else new_board != index_board
            changed &= new_board >= 0
            rgb_board[changed] = palettes[gop_id][new_board[changed]]
            index_board = new_board
        yield index_board.reshape(shape).copy(), rgb_board.reshape(*shape, 3).copy()
