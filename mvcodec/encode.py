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
    diff_mask = (frame != prev_frame).reshape(height, width)
    frame_2d = frame.reshape(height, width)
    chunks = []
    for y in range(height):
        row_mask = diff_mask[y]
        if not row_mask.any():
            continue
        row_colors = frame_2d[y]
        # Find boundaries of changed regions
        padded = np.concatenate([[False], row_mask, [False]])
        edges = np.diff(padded.astype(np.int8))
        starts = np.where(edges == 1)[0]
        ends = np.where(edges == -1)[0]
        # Within each changed region, split by color changes
        for s, e in zip(starts, ends):
            seg_colors = row_colors[s:e]
            # Find color change points within segment
            if len(seg_colors) == 1:
                chunks.append((int(s), y, 1, int(seg_colors[0])))
                continue
            color_changes = np.where(np.diff(seg_colors) != 0)[0] + 1
            split_points = np.concatenate([[0], color_changes, [len(seg_colors)]])
            for i in range(len(split_points) - 1):
                run_start = s + split_points[i]
                run_len = split_points[i+1] - split_points[i]
                color = int(seg_colors[split_points[i]])
                while run_len > RUN_LENGTH_MAX:
                    chunks.append((int(run_start), y, RUN_LENGTH_MAX, color))
                    run_start += RUN_LENGTH_MAX
                    run_len -= RUN_LENGTH_MAX
                chunks.append((int(run_start), y, int(run_len), color))
    return chunks
