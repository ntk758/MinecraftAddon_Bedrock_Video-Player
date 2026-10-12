"""2.5D パラメトリック立体 (Phase 8.3)。relief / extrude / inflate の立体を「色の平面 + 高さ (厚み) の平面」で表す。

この 3 つのモードの立体は、どの列 (奥行き方向に並ぶボクセル) も「1 色で、連続した 1 区間だけが埋まっている」。
その区間は列ごとの 1 つの値で決まるので、立体の代わりに 2 枚の 2D 画像を送れば足りる:

  色の平面   (H, W)  列の色 (パレット番号)
  値の平面   (H, W)  solid_rule が
                       "relief": 表面の段 (0〜D-1)。埋まる区間は [min(段, 上下左右の段の最小 + 1), 段]
                                 (段差の側面を埋める規則は volume.relief_volume と同じ。画像の外は自分と同じ段)
                       "span":   埋まる段数 n (0〜D)。区間は奥行きの中央にそろえた [(D-n)//2, (D-n)//2 + n - 1]
                                 (extrude と inflate。n = 0 は空の列)

符号化では 2 枚を縦に重ねた (2H) 行 × W 列の 2D 画像 (上が色、下が値) として、2D と同じ動き補償・タイル辞書・
差分ランをそのまま使う。アドオン (codec.js の expandPlanes) は、自分か上下左右の値が変わった列だけを立体に組み立て直す。
"""
import numpy as np

from mvcodec.volume import AIR_LEVEL

LAYOUT = "heightfield"
SOLID_RULES = ("relief", "span")
# パレット番号と同じ 7bit に収まる値だけを送れる
MAX_PLANE_VALUE = 127


def solid_rule_for(mode):
    """立体モードに対応する列の規則 (relief 以外は中央にそろえた区間)。"""
    return "relief" if mode == "relief" else "span"


def planes_supported(mode, depth, hollow=False):
    """そのモード・奥行きの立体を 2 枚の平面で正確に表せるか。

    内部を空洞にした立体 (hollow) は列が 1 区間にならないので表せない。span の段数は 0〜D なので D は 127 まで。
    """
    if mode not in ("relief", "extrude", "inflate") or hollow:
        return False
    limit = MAX_PLANE_VALUE if solid_rule_for(mode) == "span" else MAX_PLANE_VALUE + 1
    return depth <= limit


def column_ranges(values, depth, rule):
    """値の平面 (H, W) から、各列の埋まる区間 [low, high] を返す (high < low なら空の列)。"""
    values = np.asarray(values, dtype=np.int32)
    if rule == "relief":
        padded = np.pad(values, 1, mode="edge")
        neighbor_min = np.minimum.reduce([padded[:-2, 1:-1], padded[2:, 1:-1], padded[1:-1, :-2], padded[1:-1, 2:]])
        return np.minimum(values, neighbor_min + 1), values
    if rule == "span":
        low = (depth - values) // 2
        return low, low + values - 1
    raise ValueError(f"未対応の列の規則: {rule}")


def planes_to_volume(planes, depth, rule):
    """(2H, W) の平面から (D, H, W) の立体 (空気は AIR_LEVEL) を組み立てる。"""
    planes = np.asarray(planes)
    height = planes.shape[0] // 2
    colour, values = planes[:height], planes[height:]
    low, high = column_ranges(values, depth, rule)
    k = np.arange(depth)[:, None, None]
    solid = (k >= low[None]) & (k <= high[None])
    return np.where(solid, colour[None], AIR_LEVEL).astype(np.int16)


def volume_to_planes(volume, rule, previous=None):
    """(D, H, W) の立体を (2H, W) の平面にする。立体は relief / extrude / inflate の lifter が作ったものであること。

    空の列の色は使われないので、previous (直前のフレームの平面) の色のままにする (変わったマスを増やさない)。
    """
    volume = np.asarray(volume)
    depth, height, width = volume.shape
    solid = volume != AIR_LEVEL
    filled = solid.any(axis=0)
    first = np.argmax(solid, axis=0)
    colour = np.take_along_axis(volume, first[None], axis=0)[0].astype(np.int32)
    if previous is not None:
        colour = np.where(filled, colour, np.asarray(previous)[:height])
    else:
        colour = np.where(filled, colour, 0)
    if rule == "relief":
        values = depth - 1 - np.argmax(solid[::-1], axis=0)
    else:
        values = solid.sum(axis=0)
    return np.concatenate([colour, values.astype(np.int32)], axis=0)


def volume_change_ratio(width, height, depth, rule):
    """StreamEncoder.change_ratio_fn 用: 平面を立体に組み立てて、立体 (voxels) と同じ規則で変化率を測る関数を返す。

    空気以外のマス (どちらかのフレームで埋まっているマス) に対する、変わったマスの割合。
    """
    def ratio(previous, current):
        before = planes_to_volume(np.asarray(previous).reshape(2 * height, width), depth, rule)
        after = planes_to_volume(np.asarray(current).reshape(2 * height, width), depth, rule)
        changed = int(np.count_nonzero(before != after))
        solid = int(np.count_nonzero((before != AIR_LEVEL) | (after != AIR_LEVEL)))
        return changed / max(1, solid)
    return ratio
