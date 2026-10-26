"""3D 立体 (ホログラム) 再生用のボクセル生成。numpy だけで動く。

立体は (奥行き D, 高さ H, 幅 W) の配列で、値はパレット番号、空気は AIR_LEVEL。
軸の向き: 高さ方向は画像と同じく 0 が一番上、奥行きは 0 が一番奥 (プレイヤーから遠い側)。
符号化では (D*H) 行 × W 列の 2D 画像とみなし、2D と同じ差分・動き補償の仕組みをそのまま使う。

入力の種類:
  relief   各画素の明るさ (または別に用意した深度動画) を奥行きにした立体レリーフ。表面と段差の側面だけを作る
  extrude  明るい (または暗い) 部分を前後に押し出した立体シルエット (Bad Apple のような白黒映像向け)
  inflate  明るい (または暗い) 部分を、縁が丸く中央が厚い風船のようにふくらませた立体
  vox      MagicaVoxel の .vox (複数モデル = 複数フレーム、またはフレームごとの .vox が並んだフォルダ)
"""
import struct
from pathlib import Path

import numpy as np

AIR_LEVEL = 127
VOLUME_MODES = ("relief", "extrude", "inflate", "vox")
# relief の奥行きのヒステリシス (段数)。連続値が前フレームの段からこれ未満しか動いていなければ段を変えない。
# 量子化の境目でちらつく画素が減り、置き直すブロックも減る (0 で無効)
RELIEF_HYSTERESIS = 1.0


def luminance01(rgb):
    """(…, 3) の RGB (0〜255) から 0〜1 の明るさ。"""
    rgb = np.asarray(rgb, dtype=np.float32)
    return (rgb[..., 0] * 0.299 + rgb[..., 1] * 0.587 + rgb[..., 2] * 0.114) / 255.0


def relief_levels(heights01, depth, previous=None, hysteresis=RELIEF_HYSTERESIS):
    """0〜1 の高さを奥行きの段 (0〜depth-1) にする。

    previous (前フレームの段) があれば、連続値が前の段から hysteresis 段未満しか離れていない画素は前の段のままにする。
    """
    continuous = np.clip(np.asarray(heights01, dtype=np.float32), 0.0, 1.0) * (depth - 1)
    level = np.rint(continuous).astype(np.int32)
    if previous is not None and hysteresis > 0:
        keep = np.abs(continuous - previous) < hysteresis
        level = np.where(keep, previous, level)
    return level


def relief_volume(indices, heights01, depth, level=None):
    """2D のパレット番号と 0〜1 の高さから立体レリーフを作る。

    高さ 1 が一番手前。各画素は奥行き round(h*(depth-1)) (level を渡せばその段) の位置に表面のボクセルを置き、
    隣の画素の方が低い (奥にある) ときは、その段差を埋める側面も置く (横から見ても穴が空かない)。
    """
    indices = np.asarray(indices)
    h, w = indices.shape
    if level is None:
        level = relief_levels(heights01, depth)
    padded = np.pad(level, 1, mode="edge")
    neighbor_min = np.minimum.reduce([padded[:-2, 1:-1], padded[2:, 1:-1], padded[1:-1, :-2], padded[1:-1, 2:]])
    low = np.minimum(level, neighbor_min + 1)

    k = np.arange(depth)[:, None, None]
    solid = (k >= low[None]) & (k <= level[None])
    volume = np.full((depth, h, w), AIR_LEVEL, dtype=np.int16)
    volume[solid] = np.broadcast_to(indices[None], (depth, h, w))[solid]
    return volume


def extrude_volume(indices, mask, depth, thickness=None):
    """mask が真の画素を、奥行き方向の中央に thickness 段ぶん押し出した立体 (既定は全段)。"""
    indices = np.asarray(indices)
    h, w = indices.shape
    thickness = depth if thickness is None else max(1, min(depth, thickness))
    start = (depth - thickness) // 2
    volume = np.full((depth, h, w), AIR_LEVEL, dtype=np.int16)
    volume[start:start + thickness][:, mask] = indices[mask]
    return volume


def distance_to_edge(mask, limit):
    """mask の各画素から、mask の外 (4 近傍で一番近い背景) までの距離。limit で打ち切る。

    縁の画素が 1、背景は 0。画像の外は mask の続きとみなす (画面の端で切れた形は端で丸めない)。
    numpy だけで、4 近傍の収縮を最大 limit 回繰り返して求める。
    """
    mask = np.asarray(mask, dtype=bool)
    dist = np.zeros(mask.shape, dtype=np.int32)
    current = mask.copy()
    for step in range(1, limit + 1):
        if not current.any():
            break
        dist[current] = step
        padded = np.pad(current, 1, mode="edge")
        current = current & padded[:-2, 1:-1] & padded[2:, 1:-1] & padded[1:-1, :-2] & padded[1:-1, 2:]
    return dist


def inflate_volume(indices, mask, depth):
    """mask が真の画素を、縁が丸く中央が厚い立体にふくらませる (奥行きの中央に前後対称)。

    縁からの距離 d の画素の厚みの半分は sqrt(d * depth / 2) (depth/2 で頭打ち)。
    縁から depth/2 画素ほど内側で奥行きいっぱいの厚さになる、丸い縁取りの立体になる。
    """
    indices = np.asarray(indices)
    h, w = indices.shape
    dist = distance_to_edge(mask, max(1, depth // 2 + 1))
    half = np.minimum(depth / 2.0, np.sqrt(dist * depth / 2.0))
    center = (depth - 1) / 2.0
    k = np.arange(depth, dtype=np.float32)[:, None, None]
    # 縁の画素でも中央の段は必ず埋まるよう、厚みの半分は 0.5 以上にする
    radius = np.where(dist > 0, np.maximum(half, 0.5), 0.0)[None]
    solid = np.abs(k - center) < radius
    volume = np.full((depth, h, w), AIR_LEVEL, dtype=np.int16)
    volume[solid] = np.broadcast_to(indices[None], (depth, h, w))[solid]
    return volume


def hollow(volume):
    """外から見えない内部のボクセルを空気にする (置くブロック数を減らす)。

    6 方向の隣がすべて埋まっているボクセルを内部とみなす。範囲外は空気扱いなので、外周は残る。
    """
    solid = volume != AIR_LEVEL
    padded = np.pad(solid, 1, constant_values=False)
    interior = solid.copy()
    for axis in range(3):
        for shift in (-1, 1):
            interior &= np.roll(padded, shift, axis=axis)[1:-1, 1:-1, 1:-1]
    out = volume.copy()
    out[interior] = AIR_LEVEL
    return out


def _spread_along_axis(outside, is_air, axis):
    """axis 方向に連なる空気 (ラン) のうち、1 つでも outside のランを、まるごと outside にした配列を返す。"""
    air = np.moveaxis(is_air, axis, -1)
    reach = np.moveaxis(outside, axis, -1)
    shape = air.shape
    flat_air = air.reshape(-1)
    flat_reach = reach.reshape(-1)
    # 空気かどうかが変わる所と線の先頭でランを区切り、ランごとに 1 つでも outside があるかを調べる
    change = np.empty(flat_air.size, dtype=bool)
    change[0] = True
    np.not_equal(flat_air[1:], flat_air[:-1], out=change[1:])
    change[::shape[-1]] = True
    starts = np.flatnonzero(change)
    touched = np.logical_or.reduceat(flat_reach, starts)
    touched &= flat_air[starts]  # 空気でないラン (outside は空気にしか無いので偽になる) は外にしない
    return np.moveaxis(np.repeat(touched, np.diff(starts, append=flat_air.size)).reshape(shape), -1, axis)


# numpy の処理でランをたどる回数 (軸ごとに 1 回) の上限。MMD の人物は 4〜12 回で終わる。ばらばらの点のような入り組んだ立体は
# 数十〜百回以上かかり、scipy の方がずっと速いので、この回数を超えたら scipy に任せる (scipy が無ければそのまま続ける)
NUMPY_MAX_SWEEPS = 24


def _enclosed_air_numpy(is_air, max_sweeps=None):
    """閉じた空洞 (縁から 6 方向の空気でつながらない空気) の真偽配列。numpy だけ。

    縁にある空気を「外」の種にして、x・y・z の順に、軸ごとに空気のランをまるごと外にする処理を、3 つの軸のどれでも
    増えなくなるまで繰り返す (1 マスずつ広げるより、折れ曲がる回数だけの繰り返しで済む)。
    max_sweeps 回で終わらなければ None を返す (None なら終わるまで続ける)。
    """
    outside = np.zeros_like(is_air)
    for axis in range(3):
        edge = [slice(None)] * 3
        for position in (0, -1):
            edge[axis] = position
            outside[tuple(edge)] = is_air[tuple(edge)]
    count = int(outside.sum())
    unchanged = 0
    axis = 0
    sweeps = 0
    while unchanged < 3:
        if max_sweeps is not None and sweeps >= max_sweeps:
            return None
        sweeps += 1
        outside = _spread_along_axis(outside, is_air, axis)
        grown = int(outside.sum())
        unchanged = unchanged + 1 if grown == count else 0
        count = grown
        axis = (axis + 1) % 3
    return is_air & ~outside


_NDIMAGE = False  # scipy.ndimage (False: まだ調べていない、None: 入っていない)


def _scipy_ndimage():
    """scipy.ndimage (入っていなければ None)。scipy は必須にしていない (ブラウザ版 (pyodide) と EXE には入っていない)。"""
    global _NDIMAGE
    if _NDIMAGE is False:
        try:
            from scipy import ndimage
        except ImportError:
            ndimage = None
        _NDIMAGE = ndimage
    return _NDIMAGE


def _enclosed_air_scipy(is_air, ndimage):
    """_enclosed_air_numpy と同じものを scipy.ndimage で求める (6 方向の隣、縁の空気は外)。"""
    return ndimage.binary_fill_holes(~is_air) & is_air


def enclosed_air(is_air):
    """閉じた空洞 (縁から 6 方向の空気でつながらない空気) の真偽配列。

    ふつうは numpy で求める (scipy は読み込みだけで 0.3〜0.5 秒かかり、MMD の人物では numpy の方が速い)。入り組んだ立体で
    numpy の処理が NUMPY_MAX_SWEEPS 回で終わらないときは、scipy.ndimage があればそれで求める。2 つの方法の結果は同じ
    (tests/test_volume3d.py が確かめる)。実体のある範囲の 1 マス外側までに絞って求める
    (その外はすべて外の空気。絞った範囲の縁の空気は、必ず外とつながっている)。
    """
    is_air = np.asarray(is_air, dtype=bool)
    solid = ~is_air
    cavity = np.zeros_like(is_air)
    if not solid.any():
        return cavity
    box = []
    for axis in range(3):
        used = np.flatnonzero(solid.any(axis=tuple(a for a in range(3) if a != axis)))
        box.append(slice(max(0, int(used[0]) - 1), min(is_air.shape[axis], int(used[-1]) + 2)))
    box = tuple(box)
    inner = is_air[box]
    found = _enclosed_air_numpy(inner, NUMPY_MAX_SWEEPS)
    if found is None:
        ndimage = _scipy_ndimage()
        found = _enclosed_air_numpy(inner) if ndimage is None else _enclosed_air_scipy(inner, ndimage)
    cavity[box] = found
    return cavity


def fill_enclosed_air(volume, air=AIR_LEVEL):
    """外から見えない閉じた空洞 (縁から 6 方向の空気でつながらない空気) を埋めた立体を返す。volume は書き換えない。

    空洞のマスは、同じ x の列 (幅方向) の左隣の値で埋める。空洞のランの左隣は必ず実体なので、空洞はその値のランに
    なる (符号化のランが長くなる)。縁のマスは空洞にならないので、x = 0 の右隣で埋める場合は無い。

    MMD のような表面だけの殻は中が空洞で、内側の面も「空気に面した面」として投影が調べるが、そこは外からの視線が
    届かず、1 マスも塗らない。埋めると調べる面が減って投影が速くなる。目が人物の殻の中に入ったときは、内側が見えずに何も
    描かれなくなるが、そこは見る場所ではないので問題ない。
    外から見える立体のブロックを置く方式 (--view blocks) では中のブロックまで置くことになるので、だまし絵でだけ使う。
    """
    volume = np.asarray(volume)
    out = volume.copy()
    depth, row, column = np.nonzero(enclosed_air(volume == air))
    if len(column):
        # 空洞のマスを走査の順に並べたので、同じ列で続くマスが 1 つのランになる。ランの値は先頭のマスの左隣の実体の値
        first = np.ones(len(column), dtype=bool)
        first[1:] = ((depth[1:] != depth[:-1]) | (row[1:] != row[:-1]) | (column[1:] != column[:-1] + 1))
        left = volume[depth[first], row[first], column[first] - 1]
        out[depth, row, column] = left[np.cumsum(first) - 1]
    return out


class VolumeLifter:
    """2D のパレット番号フレームを順に立体にする (relief / extrude / inflate)。

    relief の奥行きのヒステリシスのため、直前のフレームの段を覚えておく。フレームは再生順に渡すこと。
    heights01 (0〜1) は高さ (relief) と立体にする範囲 (extrude / inflate) に使う。convert.py は減色前の元の明るさ
    (または深度動画) を渡す。無ければ各画素のブロック色の明るさを使う。
    make_hollow は内部のブロックを空気にする。fillBlocks で箱ごとに置く場合は詰まっている方が
    呼び出しもデータも少ないので既定は False (1 個ずつ置く block 方式なら True の方が置く数が減る)。
    """

    def __init__(self, mode, depth, threshold=0.5, invert=False, thickness=None, make_hollow=False,
                 hysteresis=RELIEF_HYSTERESIS):
        if mode not in ("relief", "extrude", "inflate"):
            raise ValueError(f"未対応の立体モード: {mode}")
        self.mode = mode
        self.depth = depth
        self.threshold = threshold
        self.invert = invert
        self.thickness = thickness
        self.make_hollow = make_hollow
        self.hysteresis = hysteresis
        self._level = None

    def __call__(self, indices, palette_rgb, heights01=None):
        indices = np.asarray(indices)
        if heights01 is None:
            heights01 = luminance01(np.asarray(palette_rgb)[indices])
        if self.mode == "relief":
            self._level = relief_levels(heights01, self.depth, self._level, self.hysteresis)
            volume = relief_volume(indices, heights01, self.depth, self._level)
        else:
            mask = heights01 < self.threshold if self.invert else heights01 >= self.threshold
            if self.mode == "extrude":
                volume = extrude_volume(indices, mask, self.depth, self.thickness)
            else:
                volume = inflate_volume(indices, mask, self.depth)
        return hollow(volume) if self.make_hollow else volume


def lift_frame(mode, indices, palette_rgb, depth, heights01=None, threshold=0.5, invert=False,
               thickness=None, make_hollow=False):
    """1 枚だけを立体にする (前フレームを使わないので relief のヒステリシスは無し)。"""
    lifter = VolumeLifter(mode, depth, threshold, invert, thickness, make_hollow, hysteresis=0)
    return lifter(indices, palette_rgb, heights01)


# ---------------------------------------------------------------------------
# MagicaVoxel .vox
#   仕様: https://github.com/ephtracy/voxel-model (MagicaVoxel-file-format-vox.txt / -extension.txt)
#   シーングラフとアニメーションの解釈は opengametools の ogt_vox に合わせている
# ---------------------------------------------------------------------------

# RGBA チャンクが無いファイルで使う既定パレット (仕様書の default_palette。色番号で直接引く、0xAABBGGRR)
VOX_DEFAULT_PALETTE = (
    0x00000000, 0xffffffff, 0xffccffff, 0xff99ffff, 0xff66ffff, 0xff33ffff, 0xff00ffff, 0xffffccff,
    0xffccccff, 0xff99ccff, 0xff66ccff, 0xff33ccff, 0xff00ccff, 0xffff99ff, 0xffcc99ff, 0xff9999ff,
    0xff6699ff, 0xff3399ff, 0xff0099ff, 0xffff66ff, 0xffcc66ff, 0xff9966ff, 0xff6666ff, 0xff3366ff,
    0xff0066ff, 0xffff33ff, 0xffcc33ff, 0xff9933ff, 0xff6633ff, 0xff3333ff, 0xff0033ff, 0xffff00ff,
    0xffcc00ff, 0xff9900ff, 0xff6600ff, 0xff3300ff, 0xff0000ff, 0xffffffcc, 0xffccffcc, 0xff99ffcc,
    0xff66ffcc, 0xff33ffcc, 0xff00ffcc, 0xffffcccc, 0xffcccccc, 0xff99cccc, 0xff66cccc, 0xff33cccc,
    0xff00cccc, 0xffff99cc, 0xffcc99cc, 0xff9999cc, 0xff6699cc, 0xff3399cc, 0xff0099cc, 0xffff66cc,
    0xffcc66cc, 0xff9966cc, 0xff6666cc, 0xff3366cc, 0xff0066cc, 0xffff33cc, 0xffcc33cc, 0xff9933cc,
    0xff6633cc, 0xff3333cc, 0xff0033cc, 0xffff00cc, 0xffcc00cc, 0xff9900cc, 0xff6600cc, 0xff3300cc,
    0xff0000cc, 0xffffff99, 0xffccff99, 0xff99ff99, 0xff66ff99, 0xff33ff99, 0xff00ff99, 0xffffcc99,
    0xffcccc99, 0xff99cc99, 0xff66cc99, 0xff33cc99, 0xff00cc99, 0xffff9999, 0xffcc9999, 0xff999999,
    0xff669999, 0xff339999, 0xff009999, 0xffff6699, 0xffcc6699, 0xff996699, 0xff666699, 0xff336699,
    0xff006699, 0xffff3399, 0xffcc3399, 0xff993399, 0xff663399, 0xff333399, 0xff003399, 0xffff0099,
    0xffcc0099, 0xff990099, 0xff660099, 0xff330099, 0xff000099, 0xffffff66, 0xffccff66, 0xff99ff66,
    0xff66ff66, 0xff33ff66, 0xff00ff66, 0xffffcc66, 0xffcccc66, 0xff99cc66, 0xff66cc66, 0xff33cc66,
    0xff00cc66, 0xffff9966, 0xffcc9966, 0xff999966, 0xff669966, 0xff339966, 0xff009966, 0xffff6666,
    0xffcc6666, 0xff996666, 0xff666666, 0xff336666, 0xff006666, 0xffff3366, 0xffcc3366, 0xff993366,
    0xff663366, 0xff333366, 0xff003366, 0xffff0066, 0xffcc0066, 0xff990066, 0xff660066, 0xff330066,
    0xff000066, 0xffffff33, 0xffccff33, 0xff99ff33, 0xff66ff33, 0xff33ff33, 0xff00ff33, 0xffffcc33,
    0xffcccc33, 0xff99cc33, 0xff66cc33, 0xff33cc33, 0xff00cc33, 0xffff9933, 0xffcc9933, 0xff999933,
    0xff669933, 0xff339933, 0xff009933, 0xffff6633, 0xffcc6633, 0xff996633, 0xff666633, 0xff336633,
    0xff006633, 0xffff3333, 0xffcc3333, 0xff993333, 0xff663333, 0xff333333, 0xff003333, 0xffff0033,
    0xffcc0033, 0xff990033, 0xff660033, 0xff330033, 0xff000033, 0xffffff00, 0xffccff00, 0xff99ff00,
    0xff66ff00, 0xff33ff00, 0xff00ff00, 0xffffcc00, 0xffcccc00, 0xff99cc00, 0xff66cc00, 0xff33cc00,
    0xff00cc00, 0xffff9900, 0xffcc9900, 0xff999900, 0xff669900, 0xff339900, 0xff009900, 0xffff6600,
    0xffcc6600, 0xff996600, 0xff666600, 0xff336600, 0xff006600, 0xffff3300, 0xffcc3300, 0xff993300,
    0xff663300, 0xff333300, 0xff003300, 0xffff0000, 0xffcc0000, 0xff990000, 0xff660000, 0xff330000,
    0xff0000ee, 0xff0000dd, 0xff0000bb, 0xff0000aa, 0xff000088, 0xff000077, 0xff000055, 0xff000044,
    0xff000022, 0xff000011, 0xff00ee00, 0xff00dd00, 0xff00bb00, 0xff00aa00, 0xff008800, 0xff007700,
    0xff005500, 0xff004400, 0xff002200, 0xff001100, 0xffee0000, 0xffdd0000, 0xffbb0000, 0xffaa0000,
    0xff880000, 0xff770000, 0xff550000, 0xff440000, 0xff220000, 0xff110000, 0xffeeeeee, 0xffdddddd,
    0xffbbbbbb, 0xffaaaaaa, 0xff888888, 0xff777777, 0xff555555, 0xff444444, 0xff222222, 0xff111111,
)


def _default_palette_rgba():
    values = np.array(VOX_DEFAULT_PALETTE, dtype=np.uint32)
    rgba = np.stack([values & 0xFF, (values >> 8) & 0xFF, (values >> 16) & 0xFF, values >> 24], axis=1).astype(np.uint8)
    # parse_vox の戻り値は「色番号 i → palette[i - 1]」の形にそろえる
    return np.vstack([rgba[1:], rgba[:1]])


def _read_chunks(data, start, end):
    """[(id, content, children_bytes_range)] を順に返す。"""
    pos = start
    while pos + 12 <= end:
        chunk_id = data[pos:pos + 4].decode("ascii", errors="replace")
        content_size, children_size = struct.unpack_from("<ii", data, pos + 4)
        content_start = pos + 12
        children_start = content_start + content_size
        yield chunk_id, data[content_start:children_start], (children_start, children_start + children_size)
        pos = children_start + children_size


class _Reader:
    def __init__(self, content):
        self.content = content
        self.pos = 0

    def int32(self):
        value = struct.unpack_from("<i", self.content, self.pos)[0]
        self.pos += 4
        return value

    def string(self):
        length = self.int32()
        value = self.content[self.pos:self.pos + length].decode("utf-8", errors="replace")
        self.pos += length
        return value

    def dict(self):
        return {self.string(): self.string() for _ in range(self.int32())}


def _rotation_matrix(byte):
    """nTRN の _r (1 バイト) を 3x3 の回転行列にする。"""
    c0 = byte & 3
    c1 = (byte >> 2) & 3
    c2 = 3 - c0 - c1
    matrix = np.zeros((3, 3), dtype=np.int64)
    for row, col in enumerate((c0, c1, c2)):
        matrix[row, col] = -1 if (byte >> (4 + row)) & 1 else 1
    return matrix


def _keyed(frames, frame):
    """キーフレームのうち _f が frame 以下で一番新しいもの (無ければ最初のもの)。"""
    best = frames[0]
    for item in frames:
        if int(best.get("_f", 0)) <= int(item.get("_f", 0)) <= frame:
            best = item
    return best


def parse_vox(data):
    """.vox のバイト列から (frames, palette) を返す。

    frames: [(size_xyz, voxels)]  voxels は (N, 4) の uint8 配列 [x, y, z, 色番号(1〜255)]
      - シーングラフ (nTRN / nGRP / nSHP) があれば、各アニメーションフレームについて、全オブジェクトを
        回転・移動して 1 つにまとめた立体を返す (複数オブジェクトの静止シーンは 1 フレーム)
      - シーングラフが無い古い形式は、モデル 1 つを 1 フレームとみなす
    palette: (256, 4) の RGBA。色番号 i は palette[i - 1]。RGBA チャンクが無ければ仕様の既定パレット。
    """
    if data[:4] != b"VOX ":
        raise ValueError("MagicaVoxel の .vox ファイルではありません")
    main = next(_read_chunks(data, 8, len(data)), None)
    if main is None or main[0] != "MAIN":
        raise ValueError(".vox に MAIN チャンクがありません")
    models, palette = [], None
    nodes, hidden_layers, anim_range = {}, set(), None
    size = None
    for chunk_id, content, _children in _read_chunks(data, *main[2]):
        if chunk_id == "SIZE":
            size = struct.unpack_from("<iii", content, 0)
        elif chunk_id == "XYZI":
            count = struct.unpack_from("<i", content, 0)[0]
            voxels = np.frombuffer(content, dtype=np.uint8, count=count * 4, offset=4).reshape(count, 4)
            models.append((size, voxels.copy()))
        elif chunk_id == "RGBA":
            palette = np.frombuffer(content, dtype=np.uint8, count=256 * 4).reshape(256, 4).copy()
        elif chunk_id == "nTRN":
            r = _Reader(content)
            node_id, attrs, child = r.int32(), r.dict(), r.int32()
            r.int32()  # reserved
            layer = r.int32()
            frames = [r.dict() for _ in range(r.int32())] or [{}]
            nodes[node_id] = ("trn", attrs, child, layer, frames)
        elif chunk_id == "nGRP":
            r = _Reader(content)
            node_id, attrs = r.int32(), r.dict()
            nodes[node_id] = ("grp", attrs, [r.int32() for _ in range(r.int32())])
        elif chunk_id == "nSHP":
            r = _Reader(content)
            node_id, attrs = r.int32(), r.dict()
            shapes = []
            for _ in range(r.int32()):
                model_id = r.int32()
                shapes.append({"model": model_id, **r.dict()})
            nodes[node_id] = ("shp", attrs, shapes)
        elif chunk_id == "LAYR":
            r = _Reader(content)
            layer_id, attrs = r.int32(), r.dict()
            if attrs.get("_hidden") == "1":
                hidden_layers.add(layer_id)
        elif chunk_id == "META":
            value = _Reader(content).dict().get("_anim_range")
            if value:
                anim_range = tuple(int(v) for v in value.split()[:2])
    if palette is None:
        palette = _default_palette_rgba()
    if not models:
        raise ValueError(".vox にモデルがありません")
    if 0 not in nodes:
        return models, palette
    return _evaluate_scene(models, nodes, hidden_layers, anim_range), palette


def _evaluate_scene(models, nodes, hidden_layers, anim_range):
    """シーングラフを各フレームについて評価し、ワールド座標のボクセルを 1 つの立体にまとめる。"""
    keys = set()
    for node in nodes.values():
        items = node[4] if node[0] == "trn" else node[2] if node[0] == "shp" else []
        keys.update(int(item.get("_f", 0)) for item in items)
    if anim_range is not None:
        frame_numbers = list(range(anim_range[0], anim_range[1] + 1))
    else:
        frame_numbers = list(range(0, max(keys) + 1)) if keys else [0]

    def collect(node_id, frame, rotation, translation, out):
        node = nodes.get(node_id)
        if node is None or node[1].get("_hidden") == "1":
            return
        if node[0] == "trn":
            _kind, _attrs, child, layer, frames = node
            if layer in hidden_layers:
                return
            key = _keyed(frames, frame)
            local_r = _rotation_matrix(int(key["_r"])) if "_r" in key else np.eye(3, dtype=np.int64)
            local_t = np.array([int(v) for v in key.get("_t", "0 0 0").split()], dtype=np.int64)
            collect(child, frame, rotation @ local_r, rotation @ local_t + translation, out)
        elif node[0] == "grp":
            for child in node[2]:
                collect(child, frame, rotation, translation, out)
        else:
            shape = _keyed(node[2], frame)
            size, voxels = models[shape["model"]]
            if not len(voxels):
                return
            pivot = np.array(size, dtype=np.float64) // 2
            local = voxels[:, :3].astype(np.float64) + 0.5 - pivot
            world = np.floor(local @ rotation.T.astype(np.float64)).astype(np.int64) + translation
            out.append(np.column_stack([world, voxels[:, 3].astype(np.int64)]))

    per_frame = []
    for frame in frame_numbers:
        parts = []
        collect(0, frame, np.eye(3, dtype=np.int64), np.zeros(3, dtype=np.int64), parts)
        per_frame.append(np.concatenate(parts) if parts else np.zeros((0, 4), dtype=np.int64))

    occupied = [p for p in per_frame if len(p)]
    if not occupied:
        return [((1, 1, 1), np.zeros((0, 4), np.uint8))] * len(per_frame)
    low = np.min([p[:, :3].min(axis=0) for p in occupied], axis=0)
    high = np.max([p[:, :3].max(axis=0) for p in occupied], axis=0)
    size = tuple(int(v) for v in np.minimum(high - low + 1, 256))
    frames = []
    for voxels in per_frame:
        if len(voxels):
            shifted = voxels.copy()
            shifted[:, :3] -= low
            keep = np.all(shifted[:, :3] < np.array(size), axis=1)
            frames.append((size, shifted[keep].astype(np.uint8)))
        else:
            frames.append((size, np.zeros((0, 4), np.uint8)))
    return frames


def write_vox(models, palette=None, scene=None):
    """テスト・サンプル作成用の .vox 書き出し。models は [(size_xyz, voxels)]。

    scene を渡すとシーングラフも書く: [{"translations": [(f, (x, y, z))], "models": [(f, model_id)]}, ...]
    (各要素が 1 つのオブジェクト。f はキーフレーム番号)
    """
    def chunk(chunk_id, content=b"", children=b""):
        return chunk_id.encode("ascii") + struct.pack("<ii", len(content), len(children)) + content + children

    def string(value):
        raw = value.encode("utf-8")
        return struct.pack("<i", len(raw)) + raw

    def dictionary(values):
        return struct.pack("<i", len(values)) + b"".join(string(k) + string(str(v)) for k, v in values.items())

    body = b""
    if len(models) > 1 and scene is None:
        body += chunk("PACK", struct.pack("<i", len(models)))
    for size, voxels in models:
        voxels = np.asarray(voxels, dtype=np.uint8).reshape(-1, 4)
        body += chunk("SIZE", struct.pack("<iii", *size))
        body += chunk("XYZI", struct.pack("<i", len(voxels)) + voxels.tobytes())
    if scene is not None:
        ids = iter(range(1, 10 ** 6))
        group_id = next(ids)
        object_chunks, child_ids = b"", []
        for obj in scene:
            trn_id, shp_id = next(ids), next(ids)
            child_ids.append(trn_id)
            frames = [{"_t": " ".join(map(str, t)), "_f": f} for f, t in obj["translations"]]
            object_chunks += chunk("nTRN", struct.pack("<i", trn_id) + dictionary({}) + struct.pack("<iii", shp_id, -1, 0)
                                   + struct.pack("<i", len(frames)) + b"".join(dictionary(fr) for fr in frames))
            shapes = obj["models"]
            object_chunks += chunk("nSHP", struct.pack("<i", shp_id) + dictionary({}) + struct.pack("<i", len(shapes))
                                   + b"".join(struct.pack("<i", m) + dictionary({"_f": f}) for f, m in shapes))
        body += chunk("nTRN", struct.pack("<i", 0) + dictionary({}) + struct.pack("<iii", group_id, -1, -1)
                      + struct.pack("<i", 1) + dictionary({}))
        body += chunk("nGRP", struct.pack("<i", group_id) + dictionary({}) + struct.pack("<i", len(child_ids))
                      + b"".join(struct.pack("<i", c) for c in child_ids))
        body += object_chunks
    if palette is not None:
        body += chunk("RGBA", np.asarray(palette, dtype=np.uint8).reshape(256, 4).tobytes())
    return b"VOX " + struct.pack("<i", 150) + chunk("MAIN", children=body)


def load_vox_frames_with_palettes(path):
    """.vox ファイルか、フレームごとの .vox が並んだフォルダを読み込み、フレームごとのパレットも返す。

    シーングラフのある .vox はアニメーションの各フレームが 1 フレーム (シーンのオブジェクトは 1 つの立体にまとめる)、
    シーングラフの無い古い形式は各モデルが 1 フレーム。フォルダはファイル名順で、ファイルごとに自分のパレットを使う。
    戻り値: (frames, palettes)  frames は [(size_xyz, voxels)]、palettes[i] は frames[i] のパレット。
    """
    path = Path(path)
    files = sorted(path.glob("*.vox")) if path.is_dir() else [path]
    if not files:
        raise ValueError(f".vox ファイルが見つかりません: {path}")
    frames, palettes = [], []
    for file in files:
        file_frames, file_palette = parse_vox(file.read_bytes())
        frames.extend(file_frames)
        palettes.extend([file_palette] * len(file_frames))
    return frames, palettes


def load_vox_frames(path):
    """load_vox_frames_with_palettes と同じだが、パレットは最初のフレームのものだけを返す (プレビュー用)。"""
    frames, palettes = load_vox_frames_with_palettes(path)
    return frames, palettes[0]


def vox_volume_shape(frames):
    """全フレームが入る (D, H, W)。vox の (x, y, z) は (幅, 奥行き, 高さ)。"""
    sx = max(size[0] for size, _ in frames)
    sy = max(size[1] for size, _ in frames)
    sz = max(size[2] for size, _ in frames)
    return sy, sz, sx


def vox_to_volume(size_and_voxels, shape, color_to_level):
    """1 フレームの vox を (D, H, W) のパレット番号の立体にする。

    MagicaVoxel も Minecraft も右手系なので、鏡像にならないよう回転として対応させる:
      vox の x → 幅 (東)、z → 高さ (配列の行は H-1-z)、y → 奥行き (配列は D-1-y。y が大きいほど北 = 奥)
    color_to_level は vox の色番号 (0〜255) → ブロックのパレット番号の配列。
    """
    depth, height, width = shape
    _size, voxels = size_and_voxels
    volume = np.full(shape, AIR_LEVEL, dtype=np.int16)
    if len(voxels):
        x = voxels[:, 0].astype(np.int64)
        y = voxels[:, 1].astype(np.int64)
        z = voxels[:, 2].astype(np.int64)
        keep = (x < width) & (y < depth) & (z < height)
        volume[depth - 1 - y[keep], height - 1 - z[keep], x[keep]] = color_to_level[voxels[keep, 3]]
    return volume


def nearest_levels(colors_rgb, palette_rgb):
    """各色に一番近いブロックのパレット番号 (知覚に近い重み付き RGB 距離)。"""
    colors = np.asarray(colors_rgb, dtype=np.float32)[:, None, :]
    palette = np.asarray(palette_rgb, dtype=np.float32)[None, :, :]
    weights = np.array([0.30, 0.59, 0.11], dtype=np.float32)
    distance = (((colors - palette) ** 2) * weights).sum(axis=2)
    return distance.argmin(axis=1).astype(np.int16)


def front_view(volume, palette_rgb, background=(0, 0, 0)):
    """立体を手前から見た画像 (H, W, 3)。各画素は一番手前にあるブロックの色 (サムネイル用)。"""
    volume = np.asarray(volume)
    depth, height, width = volume.shape
    solid = volume != AIR_LEVEL
    # 手前 (奥行きの大きい方) から最初に当たるボクセル
    front_index = depth - 1 - np.argmax(solid[::-1], axis=0)
    hit = solid.any(axis=0)
    levels = np.take_along_axis(volume, front_index[None], axis=0)[0]
    table = np.vstack([np.asarray(palette_rgb, dtype=np.uint8), np.zeros((128, 3), np.uint8)])
    image = np.empty((height, width, 3), np.uint8)
    image[:] = background
    image[hit] = table[levels[hit]]
    return image


def _iso_sprite(unit):
    """等角投影の立方体 1 個ぶんの画素ごとの面 (0: 無し, 1: 上面, 2: 正面 (南), 3: 側面 (東))。"""
    from PIL import Image, ImageDraw

    hw, hh, vh = unit, unit // 2, unit

    def project(x, y, z):
        return (x - z) * hw, (x + z) * hh - y * vh

    faces = {
        1: [(0, 1, 0), (1, 1, 0), (1, 1, 1), (0, 1, 1)],
        2: [(0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)],
        3: [(1, 0, 0), (1, 0, 1), (1, 1, 1), (1, 1, 0)],
    }
    points = {face: [project(*corner) for corner in corners] for face, corners in faces.items()}
    min_x = min(p[0] for pts in points.values() for p in pts)
    min_y = min(p[1] for pts in points.values() for p in pts)
    size = (2 * hw + 1, 2 * hh + vh + 1)
    label = Image.new("L", size, 0)
    draw = ImageDraw.Draw(label)
    for face in (2, 3, 1):
        draw.polygon([(x - min_x, y - min_y) for x, y in points[face]], fill=face)
    sprite = np.asarray(label)
    vy, vx = np.nonzero(sprite)
    return vx + min_x, vy + min_y, sprite[vy, vx], (hw, hh, vh)


def iso_view(volume, palette_rgb=None, unit=4, background=(24, 26, 32), rgb=None, side_rgb=None):
    """立体を南東の斜め上から見た等角投影の画像 (H, W, 3)。上面・正面 (南)・側面 (東) を明るさで塗り分ける。

    色はパレット番号を palette_rgb で引くか、各ボクセルの色 rgb (奥行き, 高さ, 幅, 3) をそのまま使う。
    side_rgb (rgb と同じ形) を渡すと、正面と側面はその色で塗る (原木などは上面と側面の見た目が違う)。

    ゲームの外で立体の出来を確かめるためのプレビュー用 (scripts/preview_volume.py、GUI のサムネイル)。
    手前のボクセルほど後から描く z バッファ方式で、numpy だけで計算する。
    """
    volume = np.asarray(volume)
    depth, height, width = volume.shape
    solid = volume != AIR_LEVEL
    # 東・上・南の 3 方向のどれかが空いているボクセルだけが見える
    padded = np.pad(solid, 1, constant_values=False)
    hidden = (padded[1:-1, 1:-1, 2:] & padded[1:-1, :-2, 1:-1] & padded[2:, 1:-1, 1:-1])
    d, row, col = np.nonzero(solid & ~hidden)
    sprite_x, sprite_y, sprite_face, (hw, hh, vh) = _iso_sprite(unit)

    # ワールド座標: 東 X = 列、上 Y = 高さ-1-行、南 Z = 奥行きの番号 (大きいほどプレイヤー側)
    wx, wy, wz = col, height - 1 - row, d
    ox = (wx - wz) * hw
    oy = (wx + wz) * hh - wy * vh
    shift_x = (depth - 1) * hw + hw
    shift_y = (height - 1) * vh + vh + 1
    canvas_w = (width + depth) * hw + 2 * hw + 2
    canvas_h = (width + depth) * hh + height * vh + vh + 2
    image = np.empty((canvas_h, canvas_w, 3), np.uint8)
    image[:] = background
    if len(d) == 0:
        return image

    if rgb is not None:
        voxel_rgb = np.asarray(rgb, dtype=np.float32)[d, row, col]
    else:
        table = np.vstack([np.asarray(palette_rgb, dtype=np.float32), np.zeros((128, 3), np.float32)])
        voxel_rgb = table[volume[d, row, col]]
    shade = np.array([0.0, 1.0, 0.82, 0.62], np.float32)
    px = (ox[:, None] + sprite_x[None] + shift_x).ravel()
    py = (oy[:, None] + sprite_y[None] + shift_y).ravel()
    nearness = np.repeat(wx + wy + wz, len(sprite_x))
    faces = np.tile(sprite_face, len(d))
    colors = np.repeat(voxel_rgb, len(sprite_x), axis=0)
    if side_rgb is not None:
        # 上面 (1) 以外の面は側面の色
        side_colors = np.repeat(np.asarray(side_rgb, dtype=np.float32)[d, row, col], len(sprite_x), axis=0)
        colors = np.where((faces != 1)[:, None], side_colors, colors)
    colors = colors * shade[faces][:, None]
    inside = (px >= 0) & (px < canvas_w) & (py >= 0) & (py < canvas_h)
    order = np.argsort(nearness[inside], kind="stable")
    image[py[inside][order], px[inside][order]] = np.clip(colors[inside][order], 0, 255).astype(np.uint8)
    return image
