"""Phase 10.2: GOP の入れ物の「適応的な算術符号」版 (入れ物の種類 2)。

ハフマン版 (mvcodec/entropy.py) と同じく v5 のフレームのバイト列を包むだけで、開けば同じバイト列に戻る。
違いは、記号ごとの確率を符号化しながら学習すること (LZMA と同じ 2 値の区間符号 + ビット木) と、
文脈を使うこと:
  - ランの色: 直前のランの色ごとに別の確率 (同じ色・近い色が続きやすい)
  - ランの間隔: LZMA の距離と同じく「ビット長 + 次の 1 ビット」の区分 (6 ビットの木) と、残りのビット
    (上位 2 ビットは区分ごとの確率、それより下は確率 1/2)
  - ランの長さ: 6 ビットの木
  - ヘッダ・動きベクトル・タイル: v5 のバイト (varint) を種類ごとの 8 ビットの木で
表を送らないので、短い GOP でも表の分だけ損をしない。符号化する順番は v5 のバイト列の順 (join_frames と同じ)
なので、復号はバイト列を前から作っていくだけ。

入れ物 (バイト列):
  1 バイト: 2
  varint フレーム数、varint 元のバイト数
  区間符号のバイト列 (最後まで)
"""
from mvcodec.encode import encode_varint

MODE_RANGE = 2
PROB_BITS = 11
PROB_ONE = 1 << PROB_BITS
PROB_INIT = PROB_ONE // 2
MOVE_BITS = 5  # 確率の更新の速さ (LZMA と同じ)
LEVEL_MOVE_BITS = 4  # 色は文脈が 128 個に分かれて 1 つあたりの記号が少ないので、速く学習する
TOP = 1 << 24
# 8 ビットの木を使うバイトの種類 (ヘッダ、動きベクトルの間隔・値、タイルの間隔・参照)
M_HEADER, M_MV_GAP, M_MV_VEC, M_TILE_GAP, M_TILE_REF = range(5)
BYTE_MODELS = 5
GAP_SLOT_BITS = 6
GAP_MODELED_EXTRA = 2  # 残りのビットのうち、確率を学習する上位のビット数
LEN_BITS = 6
LEVEL_BITS = 7


def gap_slot(gap):
    """間隔の区分: 4 未満はそのまま、それ以上は 2 × (ビット長 - 1) + 2 番目のビット (LZMA の距離の区分)。"""
    if gap < 4:
        return gap
    nb = gap.bit_length()
    return 2 * (nb - 1) + ((gap >> (nb - 2)) & 1)


class _Models:
    def __init__(self):
        self.bytes = [[PROB_INIT] * 256 for _ in range(BYTE_MODELS)]
        self.slot = [PROB_INIT] * (1 << GAP_SLOT_BITS)
        # 区分ごとの、残りのビットの上位 GAP_MODELED_EXTRA ビットの木
        self.extra = [[PROB_INIT] * (1 << GAP_MODELED_EXTRA) for _ in range(1 << GAP_SLOT_BITS)]
        self.length = [PROB_INIT] * (1 << LEN_BITS)
        self.level = [[PROB_INIT] * (1 << LEVEL_BITS) for _ in range(1 << LEVEL_BITS)]


class _Encoder:
    def __init__(self):
        self.out = bytearray()
        self.low = 0
        self.range = 0xFFFFFFFF
        self.cache = 0
        self.cache_size = 1

    def _shift_low(self):
        low = self.low
        if low < 0xFF000000 or low >= 1 << 32:
            carry = low >> 32
            temp = self.cache
            out = self.out
            while True:
                out.append((temp + carry) & 0xFF)
                temp = 0xFF
                self.cache_size -= 1
                if self.cache_size == 0:
                    break
            self.cache = (low >> 24) & 0xFF
        self.cache_size += 1
        self.low = (low & 0x00FFFFFF) << 8

    def tree(self, probs, value, nbits, move=MOVE_BITS):
        """value の nbits ビットを上位から、ビット木 (probs[1..2^nbits-1]) で符号化する。

        いちばん多く呼ばれる所 (ブラウザ版の Pyodide で変換時間の約 1/4) なので、low と range をローカルに持ち、
        桁上げを出すときだけ self に戻す。"""
        node = 1
        rng = self.range
        low = self.low
        for k in range(nbits - 1, -1, -1):
            p = probs[node]
            bound = (rng >> 11) * p  # PROB_BITS
            if (value >> k) & 1:
                low += bound
                rng -= bound
                probs[node] = p - (p >> move)
                node = (node << 1) | 1
            else:
                rng = bound
                probs[node] = p + ((2048 - p) >> move)  # PROB_ONE
                node <<= 1
            while rng < 0x1000000:  # TOP
                rng = (rng << 8) & 0xFFFFFFFF
                self.low = low
                self._shift_low()
                low = self.low
        self.range = rng
        self.low = low

    def direct(self, value, nbits):
        """確率 1/2 のビットを上位から nbits 個。"""
        for k in range(nbits - 1, -1, -1):
            self.range >>= 1
            if (value >> k) & 1:
                self.low += self.range
            while self.range < TOP:
                self.range = (self.range << 8) & 0xFFFFFFFF
                self._shift_low()

    def finish(self):
        for _ in range(5):
            self._shift_low()
        return bytes(self.out)


class _Decoder:
    def __init__(self, buf, pos):
        self.buf = buf
        self.pos = pos + 5
        self.range = 0xFFFFFFFF
        self.code = int.from_bytes(buf[pos + 1:pos + 5], "big")  # 先頭の 1 バイトは常に 0

    def _next_byte(self):
        b = self.buf[self.pos] if self.pos < len(self.buf) else 0
        self.pos += 1
        return b

    def tree(self, probs, nbits, move=MOVE_BITS):
        node = 1
        for _ in range(nbits):
            p = probs[node]
            bound = (self.range >> PROB_BITS) * p
            if self.code < bound:
                self.range = bound
                probs[node] = p + ((PROB_ONE - p) >> move)
                node <<= 1
            else:
                self.code -= bound
                self.range -= bound
                probs[node] = p - (p >> move)
                node = (node << 1) | 1
            if self.range < TOP:
                self.range = (self.range << 8) & 0xFFFFFFFF
                self.code = ((self.code << 8) | self._next_byte()) & 0xFFFFFFFF
        return node - (1 << nbits)

    def direct(self, nbits):
        value = 0
        for _ in range(nbits):
            self.range >>= 1
            bit = 1 if self.code >= self.range else 0
            if bit:
                self.code -= self.range
            value = (value << 1) | bit
            if self.range < TOP:
                self.range = (self.range << 8) & 0xFFFFFFFF
                self.code = ((self.code << 8) | self._next_byte()) & 0xFFFFFFFF
        return value


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


def _encode_gap(enc, models, gap):
    slot = gap_slot(gap)
    enc.tree(models.slot, slot, GAP_SLOT_BITS)
    if slot < 4:
        return
    extra_bits = (slot >> 1) - 1
    rest = gap & ((1 << extra_bits) - 1)
    modeled = min(extra_bits, GAP_MODELED_EXTRA)
    enc.tree(models.extra[slot], rest >> (extra_bits - modeled), modeled)
    if extra_bits > modeled:
        enc.direct(rest & ((1 << (extra_bits - modeled)) - 1), extra_bits - modeled)


def _decode_gap(dec, models):
    slot = dec.tree(models.slot, GAP_SLOT_BITS)
    if slot < 4:
        return slot
    extra_bits = (slot >> 1) - 1
    modeled = min(extra_bits, GAP_MODELED_EXTRA)
    rest = dec.tree(models.extra[slot], modeled) << (extra_bits - modeled)
    if extra_bits > modeled:
        rest |= dec.direct(extra_bits - modeled)
    return ((2 | (slot & 1)) << extra_bits) | rest


def pack_gop_range(gop_bytes, frames):
    """v5 の GOP のバイト列を入れ物 (種類 2) にする。想定外の形のフレームは ValueError / IndexError。"""
    enc = _Encoder()
    models = _Models()
    out = bytearray([MODE_RANGE])
    encode_varint(len(frames), out)
    encode_varint(len(gop_bytes), out)
    tree = enc.tree
    byte_models = models.bytes
    previous_level = 0

    def copy_varint(model, pos):
        probs = byte_models[model]
        value = 0
        shift = 0
        while True:
            b = gop_bytes[pos]
            pos += 1
            tree(probs, b, 8)
            value |= (b & 0x7F) << shift
            if b < 0x80:
                return value, pos
            shift += 7

    for offset, length in frames:
        pos, end = offset, offset + length
        header, pos = copy_varint(M_HEADER, pos)
        for flag, gap_model, value_model in ((2, M_MV_GAP, M_MV_VEC), (4, M_TILE_GAP, M_TILE_REF)):
            if header & flag:
                count, pos = copy_varint(M_HEADER, pos)
                for _ in range(count):
                    _gap, pos = copy_varint(gap_model, pos)
                    _value, pos = copy_varint(value_model, pos)
        runs, pos = copy_varint(M_HEADER, pos)
        parsed = 0
        while pos < end:
            before = pos
            val, pos = _read_varint(gop_bytes, pos)
            # 最短の varint は、2 バイト以上なら最後のバイトが 0 でない (書き直して比べるより速い)
            if pos - before > 1 and gop_bytes[pos - 1] == 0:
                raise ValueError("最短でない varint のランは入れ物に入れられません")
            parsed += 1
            level = val & 0x7F
            _encode_gap(enc, models, val >> 13)
            tree(models.length, (val >> 7) & 0x3F, LEN_BITS)
            tree(models.level[previous_level], level, LEVEL_BITS, LEVEL_MOVE_BITS)
            previous_level = level
        if parsed != runs or pos != end:
            raise ValueError(f"ランの数が合いません: ヘッダ {runs}、実際 {parsed}")
    out.extend(enc.finish())
    return bytes(out)


def unpack_gop_range(container):
    """pack_gop_range の逆: v5 のバイト列に戻す。"""
    frame_count, pos = _read_varint(container, 1)
    total, pos = _read_varint(container, pos)
    dec = _Decoder(container, pos)
    models = _Models()
    out = bytearray()
    previous_level = 0

    def copy_varint(model):
        probs = models.bytes[model]
        value = 0
        shift = 0
        while True:
            b = dec.tree(probs, 8)
            out.append(b)
            value |= (b & 0x7F) << shift
            if b < 0x80:
                return value
            shift += 7

    for _ in range(frame_count):
        header = copy_varint(M_HEADER)
        for flag, gap_model, value_model in ((2, M_MV_GAP, M_MV_VEC), (4, M_TILE_GAP, M_TILE_REF)):
            if header & flag:
                for _ in range(copy_varint(M_HEADER)):
                    copy_varint(gap_model)
                    copy_varint(value_model)
        for _ in range(copy_varint(M_HEADER)):
            gap = _decode_gap(dec, models)
            length = dec.tree(models.length, LEN_BITS)
            level = dec.tree(models.level[previous_level], LEVEL_BITS, LEVEL_MOVE_BITS)
            previous_level = level
            encode_varint((gap << 13) | (length << 7) | level, out)
    if len(out) != total:
        raise ValueError(f"入れ物を開いた大きさが違います: {len(out)} != {total}")
    return bytes(out)
