"""動き補償 (Motion Vector Prediction)。

画面を MV_BLOCK×MV_BLOCK のマクロブロックに分け、各ブロックについて「前フレームのどこからずらしてコピーすると
一番よく一致するか」を探す。パンやスクロールのように画面全体がずれる場面で、差分データを大きく減らせる。

予測の規則 (codec.js / mvcodec/decode.py と一致させること):
    予測[y][x] = 前フレーム[clamp(y + dy)][clamp(x + dx)]   (ブロック内の全画素、範囲外は端の画素)
動きベクトルを持たないブロックは前フレームのまま。予測との違いは通常の RLE ランで送る。
"""
import numpy as np

MV_BLOCK = 8
MV_RANGE = 4
# 動きベクトルを使うのは、ゼロベクトル (そのまま) より一致画素がこれ以上多い場合だけ
MV_MIN_GAIN = 2
# 全体の動きを探す縮小画像の大きさの目安 (長辺の画素数) と、その縮小画像上での探索範囲
GLOBAL_SEARCH_SIZE = 96
GLOBAL_SEARCH_RANGE = 8


def mb_grid(width, height, block=MV_BLOCK):
    """(ブロックの列数, 行数)"""
    return -(-width // block), -(-height // block)


def downscale_factor(width, height):
    """全体の動きを探す縮小率 (1 なら縮小しない = 2 段階探索をしない)。"""
    return max(1, round(max(width, height) / GLOBAL_SEARCH_SIZE))


def vector_limit(width, height, search_range=MV_RANGE):
    """動きベクトルの成分が取りうる最大の絶対値 (FRAME_DATA の mv_range に書く値)。

    局所探索は ±search_range。縮小画像での全体探索は ±GLOBAL_SEARCH_RANGE×縮小率 まで届き、その周辺 ±1 も調べる。
    """
    factor = downscale_factor(width, height)
    return search_range if factor == 1 else max(search_range, GLOBAL_SEARCH_RANGE * factor + 1)


def candidate_vectors(search_range=MV_RANGE, global_vector=None, limit=None):
    """探索候補。同点のときにゼロベクトルを優先するため先頭に置く。

    局所 (±search_range) に加え、global_vector があればその周辺 ±1 も候補にする。
    """
    vectors = [(0, 0)]
    vectors += [(dx, dy) for dy in range(-search_range, search_range + 1)
                for dx in range(-search_range, search_range + 1) if (dx, dy) != (0, 0)]
    if global_vector is not None and global_vector != (0, 0):
        gx, gy = global_vector
        seen = set(vectors)
        for ey in (-1, 0, 1):
            for ex in (-1, 0, 1):
                candidate = (gx + ex, gy + ey)
                if limit is not None and max(abs(candidate[0]), abs(candidate[1])) > limit:
                    continue
                if candidate not in seen:
                    vectors.append(candidate)
                    seen.add(candidate)
    return vectors


class _Shifter:
    """前フレームを端の値で margin だけ広げておき、(dx, dy) ずらした画像をコピーせずに取り出す。

    shifted(dx, dy)[y][x] = prev[clamp(y + dy)][clamp(x + dx)]  (|dx|, |dy| <= margin)
    毎回 clip した番号で引く (fancy indexing) より速い。"""

    def __init__(self, prev2d, margin):
        self.margin = margin
        self.height, self.width = prev2d.shape
        self.padded = np.pad(prev2d, margin, mode="edge")

    def mismatch(self, cur2d, dx, dy, out=None):
        m = self.margin
        shifted = self.padded[m + dy:m + dy + self.height, m + dx:m + dx + self.width]
        return np.not_equal(shifted, cur2d, out=out)


def estimate_global_motion(prev2d, cur2d, search_range=GLOBAL_SEARCH_RANGE):
    """縮小画像で画面全体の動き (カメラのパンなど) を 1 本だけ探す。縮小しない解像度なら None。"""
    height, width = cur2d.shape
    factor = downscale_factor(width, height)
    if factor == 1:
        return None
    small_cur = cur2d[::factor, ::factor]
    shifter = _Shifter(prev2d[::factor, ::factor], search_range)
    mismatch = np.empty(small_cur.shape, dtype=bool)
    best, best_cost = (0, 0), None
    for dy in range(-search_range, search_range + 1):
        for dx in range(-search_range, search_range + 1):
            cost = int(np.count_nonzero(shifter.mismatch(small_cur, dx, dy, mismatch)))
            if best_cost is None or cost < best_cost:
                best, best_cost = (dx, dy), cost
    return best[0] * factor, best[1] * factor


def estimate_motion(prev, cur, width, height, block=MV_BLOCK, search_range=MV_RANGE, min_gain=MV_MIN_GAIN):
    """各マクロブロックの最良の動きベクトルを {ブロック番号: (dx, dy)} で返す (ゼロベクトルは含めない)。

    評価は「パレット番号が一致しない画素の数」。全候補を画面全体でまとめて計算する。
    高解像度では、縮小画像で見つけた全体の動きの周辺も候補に加える (2 段階探索)。
    ベクトルの成分は vector_limit(width, height, search_range) 以内。
    """
    prev2d = np.asarray(prev).reshape(height, width)
    cur2d = np.asarray(cur).reshape(height, width)
    if not np.any(prev2d != cur2d):
        return {}

    row_starts = np.arange(0, height, block)
    col_starts = np.arange(0, width, block)
    global_vector = estimate_global_motion(prev2d, cur2d)
    candidates = candidate_vectors(search_range, global_vector, vector_limit(width, height, search_range))

    costs = np.empty((len(candidates), len(row_starts), len(col_starts)), dtype=np.int32)
    shifter = _Shifter(prev2d, max(max(abs(dx), abs(dy)) for dx, dy in candidates))
    mismatch = np.empty(cur2d.shape, dtype=bool)
    for k, (dx, dy) in enumerate(candidates):
        shifter.mismatch(cur2d, dx, dy, mismatch)
        # 行方向は小さな整数型で足してから int32 で列方向に足す (ブロックの 1 列は最大 MV_BLOCK 画素)
        rows = np.add.reduceat(mismatch.view(np.uint8), row_starts, axis=0, dtype=np.uint8 if block < 256 else np.int32)
        costs[k] = np.add.reduceat(rows, col_starts, axis=1, dtype=np.int32)

    best_k = costs.argmin(axis=0)
    best_cost = np.take_along_axis(costs, best_k[None], axis=0)[0]
    use = (best_k != 0) & (best_cost + min_gain <= costs[0])

    mb_cols = len(col_starts)
    vectors = {}
    for by, bx in zip(*np.nonzero(use)):
        vectors[int(by * mb_cols + bx)] = candidates[int(best_k[by, bx])]
    return vectors


def apply_motion(prev, vectors, width, height, block=MV_BLOCK):
    """動きベクトルで前フレームから予測フレーム (1 次元) を作る。"""
    prev2d = np.asarray(prev).reshape(height, width)
    if not vectors:
        return prev2d.reshape(-1).copy()
    mb_cols, mb_rows = mb_grid(width, height, block)
    # ブロックごとのベクトルを画素ごとに広げ、全画素の参照先を一度に引く (ベクトルの無いブロックは (0, 0) = そのまま)
    shift = np.zeros((2, mb_rows * mb_cols), dtype=np.intp)
    blocks = np.fromiter(vectors.keys(), dtype=np.intp, count=len(vectors))
    shift[:, blocks] = np.array(list(vectors.values()), dtype=np.intp).T
    shift = shift.reshape(2, mb_rows, mb_cols)
    ys, xs = np.arange(height), np.arange(width)
    by, bx = ys // block, xs // block
    rows = np.clip(ys[:, None] + shift[1][by[:, None], bx[None, :]], 0, height - 1)
    cols = np.clip(xs[None, :] + shift[0][by[:, None], bx[None, :]], 0, width - 1)
    return prev2d[rows, cols].reshape(-1)


def pack_vector(dx, dy, search_range=MV_RANGE):
    span = 2 * search_range + 1
    return (dy + search_range) * span + (dx + search_range)


def unpack_vector(packed, search_range=MV_RANGE):
    span = 2 * search_range + 1
    dy, dx = divmod(packed, span)
    return dx - search_range, dy - search_range


# ---------------------------------------------------------------------------
# 立体 (3D) の動き補償
#   立体は MV_BLOCK×MV_BLOCK×MV_BLOCK のブリックに分け、各ブリックで (dx, dy, dz) を ±MV_RANGE_3D から探す。
#   予測[z][y][x] = 前フレーム[clamp(z + dz)][clamp(y + dy)][clamp(x + dx)]   (z は奥行き、y は高さ方向の行)
#   ブリック番号は (bz * ブリックの行数 + by) * ブリックの列数 + bx。
#   ベクトルは ((dz + r) * span + (dy + r)) * span + (dx + r) の 1 個の整数 (r = 2 なら 0〜124 で varint 1 バイト)。
#   平面のように奥行き方向を行に並べたまま 2D で探すと、奥へ・手前へ動く物体を予測できない。
# ---------------------------------------------------------------------------
MV_RANGE_3D = 2


def candidate_vectors_3d(search_range=MV_RANGE_3D):
    """3D の探索候補。同点のときにゼロベクトルを優先するため先頭に置く。"""
    span = range(-search_range, search_range + 1)
    return [(0, 0, 0)] + [(dx, dy, dz) for dz in span for dy in span for dx in span if (dx, dy, dz) != (0, 0, 0)]


def estimate_motion_3d(prev, cur, width, height, depth, block=MV_BLOCK, search_range=MV_RANGE_3D,
                       min_gain=MV_MIN_GAIN):
    """各ブリックの最良の動きベクトルを {ブリック番号: (dx, dy, dz)} で返す (ゼロベクトルは含めない)。

    前フレームを端の値で search_range だけ広げておき、候補ごとにずらした範囲 (コピーしないビュー) と比べる。
    """
    prev3d = np.asarray(prev).reshape(depth, height, width)
    cur3d = np.asarray(cur).reshape(depth, height, width)
    if not np.any(prev3d != cur3d):
        return {}
    r = search_range
    padded = np.pad(prev3d, r, mode="edge")
    bricks = [-(-n // block) for n in (depth, height, width)]
    candidates = candidate_vectors_3d(search_range)
    costs = np.empty((len(candidates), *bricks), dtype=np.int32)
    # 一致しない画素の印を、ブリックの倍数に広げた領域に書く (はみ出した部分は 0 のまま = 端のブリックは中の画素だけを数える)
    mismatch = np.zeros([n * block for n in bricks], dtype=bool)
    view = mismatch[:depth, :height, :width]
    bd, bh, bw = bricks
    for k, (dx, dy, dz) in enumerate(candidates):
        shifted = padded[r + dz:r + dz + depth, r + dy:r + dy + height, r + dx:r + dx + width]
        np.not_equal(shifted, cur3d, out=view)
        # 奥行き → 高さ → 幅 の順にブリックごとに足す (小さな整数型で足してから最後に int32)
        sums = mismatch.view(np.uint8).reshape(bd, block, -1).sum(axis=1, dtype=np.int16)
        sums = sums.reshape(bd, bh, block, -1).sum(axis=2, dtype=np.int16)
        costs[k] = sums.reshape(bd, bh, bw, block).sum(axis=3, dtype=np.int32)

    best_k = costs.argmin(axis=0)
    best_cost = np.take_along_axis(costs, best_k[None], axis=0)[0]
    use = (best_k != 0) & (best_cost + min_gain <= costs[0])
    bricks_y, bricks_x = bh, bw
    return {int((bz * bricks_y + by) * bricks_x + bx): candidates[int(best_k[bz, by, bx])]
            for bz, by, bx in zip(*np.nonzero(use))}


def apply_motion_3d(prev, vectors, width, height, depth, block=MV_BLOCK):
    """3D の動きベクトルで前フレームから予測 (1 次元) を作る。"""
    prev3d = np.asarray(prev).reshape(depth, height, width)
    pred = prev3d.copy()
    bricks_x, bricks_y = -(-width // block), -(-height // block)
    for brick, (dx, dy, dz) in vectors.items():
        bz, rest = divmod(brick, bricks_y * bricks_x)
        by, bx = divmod(rest, bricks_x)
        z0, y0, x0 = bz * block, by * block, bx * block
        z1, y1, x1 = min(z0 + block, depth), min(y0 + block, height), min(x0 + block, width)
        zs = np.clip(np.arange(z0, z1) + dz, 0, depth - 1)
        ys = np.clip(np.arange(y0, y1) + dy, 0, height - 1)
        xs = np.clip(np.arange(x0, x1) + dx, 0, width - 1)
        pred[z0:z1, y0:y1, x0:x1] = prev3d[np.ix_(zs, ys, xs)]
    return pred.reshape(-1)


def pack_vector_3d(dx, dy, dz, search_range=MV_RANGE_3D):
    span = 2 * search_range + 1
    return ((dz + search_range) * span + (dy + search_range)) * span + (dx + search_range)


def unpack_vector_3d(packed, search_range=MV_RANGE_3D):
    span = 2 * search_range + 1
    rest, dx = divmod(packed, span)
    dz, dy = divmod(rest, span)
    return dx - search_range, dy - search_range, dz - search_range
