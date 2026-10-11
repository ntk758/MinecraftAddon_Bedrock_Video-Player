import numpy as np

# 1ランの最大長 (6bit で length-1 を表現)
RUN_LENGTH_MAX = 64
# パレットインデックスの上限 (7bit)
MAX_PALETTE_COLORS = 128
# 1文字あたり 15bit を詰める (0x1000 オフセットでサロゲート領域・制御文字を回避)
UTF16_BITS_PER_CHAR = 15
UTF16_CHAR_OFFSET = 0x1000

_BIT_WEIGHTS = (1 << np.arange(UTF16_BITS_PER_CHAR - 1, -1, -1)).astype(np.uint32)


def encode_varint(val, byte_arr):
    while val >= 0x80:
        byte_arr.append((val & 0x7f) | 0x80)
        val >>= 7
    byte_arr.append(val & 0x7f)


def pack_run(start_x, y, length, color, width):
    """1ランを main.js が解釈する 1 個の整数へ詰める: [位置 | (長さ-1):6bit | 色:7bit]"""
    delta = y * width + start_x
    return (delta << 13) | ((length - 1) << 7) | (color & 0x7f)


def encode_frame_v5(frame_no, is_keyframe, frame, reference, width, height, vectors=None, search_range=4,
                    tile_ops=None):
    """varint_rle_v5 の 1 フレーム分のバイト列。

    ヘッダ: varint(frame_no << 3 | タイル参照あり << 2 | 動きベクトルあり << 1 | キーフレーム)
    動きベクトルあり: varint(個数), 各ブロックについて varint(前のブロック番号との差 - 1), varint(packed ベクトル)
      (立体では 3 成分のブリック単位のベクトル。mvcodec/motion.py の pack_vector_3d)
    タイル参照あり: varint(個数), 各タイルについて varint(前のタイル番号との差 - 1), varint(辞書の新しい方からの番号)
    続けて varint(ラン数) と、reference (動きベクトル・タイル参照を反映した予測) との差分ラン。
    v5 のランの位置は「直前のランの終わりからの距離」(先頭のランは 0 から) で表す。位置の数値が小さくなり、
    特に立体 (位置が 16〜20bit になる) でデータが大きく減る。
    """
    from mvcodec.motion import pack_vector, pack_vector_3d

    vectors = vectors or {}
    tile_ops = tile_ops or []
    encoded = bytearray()
    flags = ((1 if tile_ops else 0) << 2) | ((1 if vectors else 0) << 1) | (1 if is_keyframe else 0)
    encode_varint((frame_no << 3) | flags, encoded)
    if vectors:
        encode_varint(len(vectors), encoded)
        last = -1
        for mb in sorted(vectors):
            vector = vectors[mb]
            encode_varint(mb - last - 1, encoded)
            encode_varint(pack_vector_3d(*vector, search_range) if len(vector) == 3
                          else pack_vector(*vector, search_range), encoded)
            last = mb
    if tile_ops:
        encode_varint(len(tile_ops), encoded)
        last = -1
        for tile_index, recency in tile_ops:
            encode_varint(tile_index - last - 1, encoded)
            encode_varint(recency, encoded)
            last = tile_index
    chunks = extract_rle_chunks_fast(frame, reference, width, height)
    encode_varint(len(chunks), encoded)
    previous_end = 0
    for start_x, y, length, color in chunks:
        position = y * width + start_x
        encode_varint(pack_run(position - previous_end, 0, length, color, width), encoded)
        previous_end = position + length
    return encoded


def bytearray_to_utf16_str(byte_arr):
    """バイト列を 15bit/文字 の文字列へ変換する。先頭文字にパディングビット数を格納する。"""
    if not byte_arr:
        return ""
    bits = np.unpackbits(np.frombuffer(bytes(byte_arr), dtype=np.uint8))
    pad_len = (-len(bits)) % UTF16_BITS_PER_CHAR
    if pad_len:
        bits = np.concatenate([bits, np.zeros(pad_len, dtype=np.uint8)])
    vals = bits.reshape(-1, UTF16_BITS_PER_CHAR).astype(np.uint32) @ _BIT_WEIGHTS

    codes = np.empty(len(vals) + 1, dtype="<u2")
    codes[0] = UTF16_CHAR_OFFSET + pad_len
    codes[1:] = vals + UTF16_CHAR_OFFSET
    return codes.tobytes().decode("utf-16-le")


def extract_rle_chunks_fast(frame, prev_frame, width, height):
    """prev_frame と違う画素を、同じ行・同じ色で続く区間 (最大 RUN_LENGTH_MAX) ごとの (x, y, 長さ, 色) にする。

    位置の順 (行優先、左から右) に並ぶ。フレーム全体をまとめて numpy で計算する (立体では行が数千になるため)。
    """
    frame = np.asarray(frame).reshape(-1)
    changed = np.flatnonzero(frame != np.asarray(prev_frame).reshape(-1))
    if len(changed) == 0:
        return []
    colors = frame[changed]
    # 直前の変化画素と続いていない・行の先頭・色が変わる ところでランを区切る
    new_run = np.ones(len(changed), dtype=bool)
    new_run[1:] = (np.diff(changed) != 1) | (changed[1:] % width == 0) | (colors[1:] != colors[:-1])
    starts = np.flatnonzero(new_run)
    lengths = np.diff(np.append(starts, len(changed)))
    positions = changed[starts]
    run_colors = colors[starts]
    # RUN_LENGTH_MAX を超えるランは先頭から RUN_LENGTH_MAX ずつに分ける
    pieces = (lengths + RUN_LENGTH_MAX - 1) // RUN_LENGTH_MAX
    if pieces.max() > 1:
        run_index = np.repeat(np.arange(len(starts)), pieces)
        piece_no = np.arange(len(run_index)) - np.repeat(np.cumsum(pieces) - pieces, pieces)
        offsets = piece_no * RUN_LENGTH_MAX
        positions = positions[run_index] + offsets
        lengths = np.minimum(lengths[run_index] - offsets, RUN_LENGTH_MAX)
        run_colors = run_colors[run_index]
    ys, xs = np.divmod(positions, width)
    return list(zip(xs.tolist(), ys.tolist(), lengths.tolist(), run_colors.tolist()))
