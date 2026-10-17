"""ブロックモードのレート歪み最適化 (Phase 8.5)。

減色したフレーム (パレット番号) を 8×8 のブロックに分け、ブロックごとに次の候補から
「色の誤差 + strength × (ゲーム内で置く費用 + データ量)」が一番小さいものを選ぶ (Smacker・RoQ 型のモード選択):

    exact       減色した結果 (表示中の色が元の色に十分近いマスは表示中のまま = 条件付き更新)
    skip        前のフレームのまま (置き直さない)
    motion      前のフレームを動き補償した予測 (データは動きベクトルだけ)
    solid_mean  1 色で塗る (ブロックの元の平均色に一番近いブロック)
    solid_mode  1 色で塗る (ブロック内で一番多い番号)
    two         ブロック内で多い 2 つの番号だけで描く (各画素は元の色に近い方)

exact より費用が減らない候補は選ばない (strength は「色の誤差と引き換えに費用を減らす」量だけを決める)。
選んだ結果は普通のフレームとして varint_rle_v5 / v6 で符号化するので、データ形式とアドオンは変わらない
(エンコーダが「少しの色の誤差で、置くブロック・呼び出し・データを減らす」判断をするだけ)。
結果は benchmark/rdo_bench.py と docs/research/phase7x.md の 12 章。

色の誤差は OkLab (×100) の二乗誤差で、3×3 でぼかした画像どうしの差に、画素ごとの差を少し (PIXEL_WEIGHT) 足す。
ぼかすのはディザの粒を「平均の色」として評価するため (画素ごとの差だけだと、ディザより単色の方が良く見えてしまう)。

置く費用はアドオンの費用のモデル (main.js の DEFAULT_COST_MODEL、Windows 版の実測) で見積もる:
    rect (同じ色の箱を fillBlocks): 箱 1 個 15 µs + 1 ブロック 0.55 µs
    list (座標リストを fillBlocks): 1 ブロック 1.42 µs
アドオンはフレームごとに速い方を選ぶので、ブロックごとにも安い方の値を使う。箱の数は「左にも上にも同じ色の変更が
続いていないマス」の数で近似する。データ量は、動き補償した予測との差のランの数 (同じ色で横に続く変更) × RUN_BYTES
で見積もる。どれも 8×8 の中だけで数える近似 (ブロックの境目をまたぐ箱・ランは考えない)。
"""
import numpy as np

from mvcodec.motion import MV_RANGE, apply_motion, estimate_motion

RDO_BLOCK = 8
# main.js の DEFAULT_COST_MODEL (µs)
FILL_CALL_US = 15.0
FILL_BLOCK_US = 0.55
LIST_BLOCK_US = 1.42
# ラン 1 個のデータ量 (エントロピー符号化後のおよそのバイト数) と、1 バイトを何 µs と同じ重さにするか
RUN_BYTES = 1.5
BYTE_WEIGHT_US = 2.0
# ぼかした差に足す、画素ごとの差の重み
PIXEL_WEIGHT = 0.1
MODES = ("exact", "skip", "motion", "solid_mean", "solid_mode", "two")

_M1 = np.array([[0.4122214708, 0.5363325363, 0.0514459929], [0.2119034982, 0.6806995451, 0.1073969566],
                [0.0883024619, 0.2817188376, 0.6299787005]])
_M2 = np.array([[0.2104542553, 0.7936177850, -0.0040720468], [1.9779984951, -2.4285922050, 0.4505937099],
                [0.0259040371, 0.7827717662, -0.8086757660]])


def _srgb_to_linear(c):
    c = np.asarray(c, dtype=np.float64) / 255.0
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4).astype(np.float32)


# 0〜255 の整数の sRGB → 線形の表 (フレームは整数なので表で引く)
_LINEAR_LUT = _srgb_to_linear(np.arange(256))


def rgb_to_oklab(rgb):
    """(..., 3) の sRGB (0〜255) を OkLab × 100 にする。"""
    rgb = np.asarray(rgb)
    lin = _LINEAR_LUT[rgb] if rgb.dtype == np.uint8 else _srgb_to_linear(rgb)
    return (np.cbrt(lin @ _M1.T.astype(np.float32)) @ (_M2.T * 100.0).astype(np.float32))


def _box_blur(img):
    """(H, W, C) の 3×3 の平均 (端は端の値を伸ばす)。"""
    h, w = img.shape[:2]
    padded = np.pad(img, ((1, 1), (1, 1), (0, 0)), mode="edge")
    rows = padded[:-2] + padded[1:-1] + padded[2:]
    return (rows[:, :-2] + rows[:, 1:-1] + rows[:, 2:]) / 9.0


def _block_sum(values, block):
    """(H, W) をブロックごとに足して (ブロックの行数, 列数) にする (端の半端なブロックも中の画素だけで足す)。"""
    h, w = values.shape
    bh, bw = -(-h // block), -(-w // block)
    if (bh * block, bw * block) != (h, w):
        values = np.pad(values, ((0, bh * block - h), (0, bw * block - w)))
    return values.reshape(bh, block, bw, block).sum(axis=(1, 3))


def _expand(block_values, shape, block):
    """ブロックごとの値を画素ごとに広げる。"""
    h, w = shape
    return np.repeat(np.repeat(block_values, block, axis=0), block, axis=1)[:h, :w]


class BlockRDO:
    """フレームの列に順番にかける (前のフレームを覚えるのは呼び出し側: reference に符号化済みの盤面を渡す)。"""

    def __init__(self, strength, block=RDO_BLOCK, modes=MODES, hysteresis=24.0, mv_range=MV_RANGE):
        self.strength = float(strength)
        self.mv_range = mv_range
        # exact の画素のうち、表示中の色が元の色から (RGB の距離で) これ以内しか悪くならないものは表示中のままにする
        # (convert.py の条件付き更新 --temporal-hysteresis と同じ。減色は自分の前の結果と比べて条件付き更新するが、
        # このクラスがブロックを置き替えた後は、アドオンの盤面と比べないと変わらないはずのマスまで置き直してしまう)
        self.hysteresis = float(hysteresis)
        self.block = block
        self.modes = tuple(modes)
        self._palette_key = None
        self.stats = {mode: 0 for mode in MODES}

    def _palette_lab(self, palette_rgb):
        key = id(palette_rgb)
        if key != self._palette_key:
            self._palette_key = key
            self._pal_lab = rgb_to_oklab(np.asarray(palette_rgb, dtype=np.float32))
        return self._pal_lab

    def __call__(self, indices, source_rgb, palette_rgb, reference=None):
        """indices (H, W) のパレット番号を、ブロックごとに選んだ候補に置き替えて返す。

        source_rgb は減色前の (H, W, 3)、reference はアドオンが今表示している盤面 (同じパレットの番号、未設置は -1)。
        GOP の先頭などで前の盤面が使えないときは None (skip を使わない)。
        """
        target = np.asarray(indices, dtype=np.int32)
        if self.strength <= 0:
            return target
        h, w = target.shape
        b = self.block
        pal_lab = self._palette_lab(palette_rgb)
        src_lab = rgb_to_oklab(source_rgb).reshape(h, w, 3)
        src_blur = _box_blur(src_lab)
        if reference is not None:
            reference = np.asarray(reference, dtype=np.int32).reshape(h, w)

        if reference is not None and self.hysteresis > 0:
            target = self._keep_reference(target, reference, source_rgb, palette_rgb)
        candidates = {"exact": target}
        # データはエンコーダと同じく動き補償した予測との差で見積もる (置く費用は表示中の盤面との差)
        predicted = reference
        if reference is not None and "skip" in self.modes:
            candidates["skip"] = reference
        if reference is not None and "motion" in self.modes:
            vectors = estimate_motion(reference, target, w, h, search_range=self.mv_range)
            if vectors:
                predicted = apply_motion(reference, vectors, w, h).reshape(h, w)
                candidates["motion"] = predicted
        bh, bw = -(-h // b), -(-w // b)
        block_id = (np.arange(h)[:, None] // b) * bw + (np.arange(w)[None, :] // b)
        if "solid_mean" in self.modes:
            counts = _block_sum(np.ones((h, w), np.float32), b)
            mean_lab = np.stack([_block_sum(src_lab[..., k], b) for k in range(3)], -1) / counts[..., None]
            nearest = ((mean_lab[..., None, :] - pal_lab) ** 2).sum(-1).argmin(-1)
            candidates["solid_mean"] = _expand(nearest, (h, w), b).astype(np.int32)
        if "solid_mode" in self.modes or "two" in self.modes:
            n = len(pal_lab)
            hist = np.bincount((block_id * n + target).ravel(), minlength=bh * bw * n).reshape(bh * bw, n)
            first = hist.argmax(1)
            if "solid_mode" in self.modes:
                candidates["solid_mode"] = first[block_id].astype(np.int32)
            if "two" in self.modes:
                hist[np.arange(len(first)), first] = -1
                second = np.where(hist.max(1) > 0, hist.argmax(1), first)
                a, c = first[block_id], second[block_id]
                closer_a = ((src_lab - pal_lab[a]) ** 2).sum(-1) <= ((src_lab - pal_lab[c]) ** 2).sum(-1)
                candidates["two"] = np.where(closer_a, a, c).astype(np.int32)

        names = list(candidates)
        costs = np.empty((len(names), bh, bw), np.float64)
        exact_place = exact_rate = None
        for k, name in enumerate(names):
            place, data = self._rate(candidates[name], reference, predicted)
            rate = place + data
            costs[k] = self._distortion(candidates[name], pal_lab, src_lab, src_blur) + self.strength * rate
            if name == "exact":
                exact_place, exact_rate = place, rate
            else:
                # 減色の結果より費用が減らない候補と、置く費用が増える候補は使わない (強さは「色の誤差と引き換えに
                # 費用を減らす」量だけを決める。ぼかした誤差だけ小さくて費用が増える候補や、データは減るが置くブロックが
                # 増える候補 (パンで動き補償の予測を選ぶと、条件付き更新で残せたマスまで置き直す) を選ぶと、
                # ゲーム内の描画がかえって重くなるため)
                costs[k][(rate >= exact_rate) | (place > exact_place)] = np.inf
        # 未設置 (-1) のマスを含む skip / motion は使えない (前の盤面に描かれていない)
        for name in ("skip", "motion"):
            if name in candidates:
                costs[names.index(name)][_block_sum((candidates[name] < 0).astype(np.int32), b) > 0] = np.inf
        choice = costs.argmin(0)
        for k, name in enumerate(names):
            self.stats[name] += int((choice == k).sum())
        stacked = np.stack([candidates[name] for name in names])
        return np.take_along_axis(stacked, _expand(choice, (h, w), b)[None], 0)[0]

    def _keep_reference(self, target, reference, source_rgb, palette_rgb):
        palette = np.asarray(palette_rgb, dtype=np.float32)
        source = np.asarray(source_rgb, dtype=np.float32).reshape(*target.shape, 3)
        valid = reference >= 0
        ref = np.where(valid, reference, 0)
        new_error = ((palette[target] - source) ** 2).sum(-1)
        old_error = ((palette[ref] - source) ** 2).sum(-1)
        return np.where(valid & (old_error <= new_error + self.hysteresis ** 2), ref, target).astype(np.int32)

    def _distortion(self, recon, pal_lab, src_lab, src_blur):
        lab = pal_lab[recon]
        blur_err = ((_box_blur(lab) - src_blur) ** 2).sum(-1)
        pixel_err = ((lab - src_lab) ** 2).sum(-1)
        return _block_sum(blur_err + PIXEL_WEIGHT * pixel_err, self.block)

    def _rate(self, recon, reference, predicted):
        """ブロックごとの (置く費用 (µs、表示中の盤面との差), データ量 (µs 換算、動き補償した予測との差))。"""
        b = self.block
        changed = np.ones(recon.shape, bool) if reference is None else (recon != reference)
        run_starts = _run_starts(recon, changed)
        up_same = np.zeros_like(changed)
        up_same[1:] = changed[1:] & changed[:-1] & (recon[1:] == recon[:-1])
        box_starts = run_starts & ~up_same
        n_changed = _block_sum(changed.astype(np.float32), b)
        boxes = _block_sum(box_starts.astype(np.float32), b)
        place = np.minimum(FILL_CALL_US * boxes + FILL_BLOCK_US * n_changed, LIST_BLOCK_US * n_changed)
        if predicted is not reference:
            run_starts = _run_starts(recon, recon != predicted)
        runs = _block_sum(run_starts.astype(np.float32), b)
        return place, BYTE_WEIGHT_US * RUN_BYTES * runs


def _run_starts(recon, changed):
    """差分ランの先頭のマス (左のマスが同じ色の変更なら同じランの続き)。"""
    left_same = np.zeros_like(changed)
    left_same[:, 1:] = changed[:, 1:] & changed[:, :-1] & (recon[:, 1:] == recon[:, :-1])
    return changed & ~left_same
