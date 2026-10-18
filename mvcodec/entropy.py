"""Phase 8.4: GOP ごとのエントロピー符号化 (varint_rle_v6)。

v5 のフレームのバイト列はそのままに、GOP 単位の「入れ物」で圧縮する。入れ物の中では、フレームのバイト列を
意味ごとのストリームに分け (ヘッダ・動きベクトルの間隔・ベクトル・タイルの間隔・参照・ランの間隔・長さ・色)、
ストリームごとに静的な正準ハフマン符号 (符号長は最大 MAX_CODE_LENGTH) で符号化する。値の分布が似たものだけを
同じ表で符号化するので、バイト列をそのままハフマン符号化するより縮む (Smacker 型)。
復号は入れ物を開いて元の v5 のバイト列に戻すだけなので、フレームの位置の表 (index) と、その後の復元
(動き補償・タイル辞書・差分ラン) は v5 と同じ。

入れ物 (バイト列):
  1 バイト: 0 = 生 (以降は v5 のバイト列そのまま) / 1 = ハフマン
  ハフマンのとき: varint フレーム数、varint 元のバイト数 (復号側が出力を一度で確保する)、続いて 8 個のストリームそれぞれについて
    varint 記号の数 (0 なら以降なし) / varint 記号の種類 A / 符号長 (4bit × A、上位から詰める) /
    varint 符号のバイト数 / 符号 (MSB から詰める)
ハフマンにしても小さくならない GOP (ごく短いもの) は生のまま入れる。
"""
import heapq

import numpy as np

from mvcodec.encode import encode_varint

MAX_CODE_LENGTH = 12
MODE_RAW = 0
MODE_HUFFMAN = 1
# ストリームの番号と、値の範囲 (記号の種類の上限)
S_HEADER, S_MV_GAP, S_MV_VEC, S_TILE_GAP, S_TILE_REF, S_RUN_GAP, S_RUN_LEN, S_RUN_LEVEL = range(8)
STREAM_COUNT = 8


def _read_varint(buf, pos):
    val = 0
    shift = 0
    while True:
        b = buf[pos]
        pos += 1
        val |= (b & 0x7F) << shift
        if b < 0x80:
            return val, pos
        shift += 7


def _varint_list(value, out):
    while value >= 0x80:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value)


def split_frames(gop_bytes, frames):
    """v5 のフレーム (gop_bytes の (offset, length) の並び) を 8 個のストリームの記号の列に分ける。"""
    streams = [[] for _ in range(STREAM_COUNT)]
    head = streams[S_HEADER]
    for offset, length in frames:
        pos, end = offset, offset + length
        start = pos
        header, pos = _read_varint(gop_bytes, pos)
        head.extend(gop_bytes[start:pos])
        for flag, gap_stream, value_stream in ((2, S_MV_GAP, S_MV_VEC), (4, S_TILE_GAP, S_TILE_REF)):
            if header & flag:
                start = pos
                count, pos = _read_varint(gop_bytes, pos)
                head.extend(gop_bytes[start:pos])
                for _ in range(count):
                    start = pos
                    _gap, pos = _read_varint(gop_bytes, pos)
                    streams[gap_stream].extend(gop_bytes[start:pos])
                    start = pos
                    _value, pos = _read_varint(gop_bytes, pos)
                    streams[value_stream].extend(gop_bytes[start:pos])
        start = pos
        runs, pos = _read_varint(gop_bytes, pos)
        head.extend(gop_bytes[start:pos])
        parsed = 0
        while pos < end:
            before = pos
            val, pos = _read_varint(gop_bytes, pos)
            # ランの値は作り直すので、元が最短の varint でないと同じバイト列に戻らない
            check = []
            _varint_list(val, check)
            if len(check) != pos - before:
                raise ValueError("最短でない varint のランは入れ物に入れられません")
            parsed += 1
            _varint_list(val >> 13, streams[S_RUN_GAP])
            streams[S_RUN_LEN].append((val >> 7) & 0x3F)
            streams[S_RUN_LEVEL].append(val & 0x7F)
        if parsed != runs:
            raise ValueError(f"ランの数が合いません: ヘッダ {runs}、実際 {parsed}")
    return streams


def join_frames(streams, frame_count):
    """split_frames の逆: ストリームから v5 のフレームのバイト列を作り直す (frame_count 個を続けて並べたもの)。"""
    out = bytearray()
    cursors = [0] * STREAM_COUNT

    def take_varint(stream):
        data = streams[stream]
        pos = cursors[stream]
        value, end = _read_varint(data, pos)
        cursors[stream] = end
        out.extend(data[pos:end])
        return value

    def read_varint_only(stream):
        value, end = _read_varint(streams[stream], cursors[stream])
        cursors[stream] = end
        return value

    for _ in range(frame_count):
        header = take_varint(S_HEADER)
        for flag, gap_stream, value_stream in ((2, S_MV_GAP, S_MV_VEC), (4, S_TILE_GAP, S_TILE_REF)):
            if header & flag:
                count = take_varint(S_HEADER)
                for _ in range(count):
                    take_varint(gap_stream)
                    take_varint(value_stream)
        runs = take_varint(S_HEADER)
        for _ in range(runs):
            gap = read_varint_only(S_RUN_GAP)
            length = streams[S_RUN_LEN][cursors[S_RUN_LEN]]
            level = streams[S_RUN_LEVEL][cursors[S_RUN_LEVEL]]
            cursors[S_RUN_LEN] += 1
            cursors[S_RUN_LEVEL] += 1
            encode_varint((gap << 13) | (length << 7) | level, out)
    return bytes(out)


# ---------------------------------------------------------------------------
# 正準ハフマン符号
# ---------------------------------------------------------------------------

def code_lengths(counts, max_length=MAX_CODE_LENGTH):
    """記号ごとの出現回数から、最大 max_length の符号長を作る (長すぎるときは回数を半分にして作り直す)。"""
    counts = np.asarray(counts, dtype=np.int64)
    used = np.nonzero(counts)[0]
    lengths = np.zeros(len(counts), dtype=np.int64)
    if len(used) == 0:
        return lengths
    if len(used) == 1:
        lengths[used[0]] = 1
        return lengths
    weights = counts.copy()
    while True:
        heap = [(int(weights[s]), i, (int(s),)) for i, s in enumerate(used)]
        heapq.heapify(heap)
        depth = {int(s): 0 for s in used}
        tie = len(heap)
        while len(heap) > 1:
            w1, _, a = heapq.heappop(heap)
            w2, _, b = heapq.heappop(heap)
            for s in a + b:
                depth[s] += 1
            heapq.heappush(heap, (w1 + w2, tie, a + b))
            tie += 1
        if max(depth.values()) <= max_length:
            for s, d in depth.items():
                lengths[s] = d
            return lengths
        weights = np.where(weights > 0, np.maximum(1, weights >> 1), 0)


def canonical_codes(lengths):
    """符号長から正準ハフマン符号 (記号 → 符号の値) を作る。符号長の短い順、同じ長さなら記号の小さい順。"""
    codes = np.zeros(len(lengths), dtype=np.int64)
    code = 0
    previous = 0
    for length, symbol in sorted((int(l), s) for s, l in enumerate(lengths) if l > 0):
        code <<= length - previous
        codes[symbol] = code
        code += 1
        previous = length
    return codes


def _pack_bits(symbols, lengths, codes):
    symbols = np.asarray(symbols, dtype=np.int64)
    sym_len = lengths[symbols]
    sym_code = codes[symbols]
    width = int(sym_len.max())
    shifts = np.arange(width - 1, -1, -1)
    # 各記号の符号を width ビットで左詰めにし、使うビット (先頭 sym_len 個) だけ取り出す
    padded = sym_code << (width - sym_len)
    bits = ((padded[:, None] >> shifts) & 1).astype(np.uint8)
    mask = np.arange(width)[None, :] < sym_len[:, None]
    return np.packbits(bits[mask]).tobytes()


def encode_stream(symbols, alphabet, out):
    """1 個のストリームを out に書く。"""
    encode_varint(len(symbols), out)
    if not symbols:
        return
    counts = np.bincount(np.asarray(symbols, dtype=np.intp), minlength=alphabet)  # intp: 32bit の環境 (Pyodide) でも動く
    lengths = code_lengths(counts)
    used_alphabet = int(np.nonzero(lengths)[0].max()) + 1
    encode_varint(used_alphabet, out)
    table = lengths[:used_alphabet]
    if used_alphabet % 2:
        table = np.append(table, 0)
    out.extend(((table[0::2] << 4) | table[1::2]).astype(np.uint8).tobytes())
    payload = _pack_bits(symbols, lengths, canonical_codes(lengths))
    encode_varint(len(payload), out)
    out.extend(payload)


def decode_stream(buf, pos):
    """encode_stream の逆。(記号のリスト, 次の位置)。参照実装なので速さより分かりやすさを優先する。"""
    count, pos = _read_varint(buf, pos)
    if count == 0:
        return [], pos
    used_alphabet, pos = _read_varint(buf, pos)
    nibbles = (used_alphabet + 1) // 2
    packed = buf[pos:pos + nibbles]
    pos += nibbles
    lengths = []
    for b in packed:
        lengths += [b >> 4, b & 0x0F]
    lengths = lengths[:used_alphabet]
    size, pos = _read_varint(buf, pos)
    data = buf[pos:pos + size]
    pos += size
    # 正準符号は「長さ L の符号の最初の値」と「その長さの記号の並び」で引ける
    order = sorted((l, s) for s, l in enumerate(lengths) if l > 0)
    first_code, first_index, counts = {}, {}, {}
    code = 0
    previous = 0
    for index, (length, _symbol) in enumerate(order):
        code <<= length - previous
        if length not in first_code:
            first_code[length] = code
            first_index[length] = index
            counts[length] = 0
        counts[length] += 1
        code += 1
        previous = length
    symbols = []
    bit_pos = 0
    total_bits = len(data) * 8
    for _ in range(count):
        code = 0
        length = 0
        while True:
            if bit_pos >= total_bits:
                raise ValueError("ハフマン符号のデータが足りません")
            code = (code << 1) | ((data[bit_pos >> 3] >> (7 - (bit_pos & 7))) & 1)
            bit_pos += 1
            length += 1
            if length in first_code and code - first_code[length] < counts[length]:
                symbols.append(order[first_index[length] + code - first_code[length]][1])
                break
    return symbols, pos


ALPHABETS = [256, 256, 256, 256, 256, 256, 64, 128]


def pack_gop(gop_bytes, frames):
    """GOP の v5 のバイト列を入れ物にする。frames は (offset, length) の並び (長さ 0 のフレームは含めない)。
    フレームは gop_bytes の中で隙間なく並んでいること (StreamEncoder が作る形)。"""
    gop_bytes = bytes(gop_bytes)
    raw = bytes([MODE_RAW]) + gop_bytes
    if not frames:
        return raw
    try:
        streams = split_frames(gop_bytes, frames)
    except (ValueError, IndexError):
        return raw  # 想定外の形のフレームは生のまま入れる (開けば必ず同じバイト列)
    out = bytearray([MODE_HUFFMAN])
    encode_varint(len(frames), out)
    encode_varint(len(gop_bytes), out)
    for stream, alphabet in zip(streams, ALPHABETS):
        encode_stream(stream, alphabet, out)
    return bytes(out) if len(out) < len(raw) else raw


def unpack_gop(container):
    """pack_gop の逆: v5 のバイト列に戻す。"""
    container = bytes(container)
    if not container:
        return b""
    if container[0] == MODE_RAW:
        return container[1:]
    if container[0] != MODE_HUFFMAN:
        raise ValueError(f"未対応の入れ物の種類: {container[0]}")
    frame_count, pos = _read_varint(container, 1)
    total, pos = _read_varint(container, pos)
    streams = []
    for _ in range(STREAM_COUNT):
        symbols, pos = decode_stream(container, pos)
        streams.append(symbols)
    joined = join_frames(streams, frame_count)
    if len(joined) != total:
        raise ValueError(f"入れ物を開いた大きさが違います: {len(joined)} != {total}")
    return joined
