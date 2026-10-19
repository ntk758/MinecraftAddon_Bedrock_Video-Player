import numpy as np

# K-Means とブロックの対応を行う色空間。oklab は知覚的に均等な色空間 (GPU の減色と同じ)
COLOR_SPACES = ("oklab", "rgb")
# OkLab の L は 0〜1 なので、収束判定 (atol) と RGB の 0〜255 の目盛りをそろえるために掛ける
OKLAB_SCALE = 100.0
# max_colors="auto" で試す色数 (少ない順) と、許す色の誤差の増え方 (一番多い色数のときの何倍まで)。
# 16 色はパンの映像でぼかした色差が +1.0 と大きく崩れたので候補にしない (docs/research/phase7x.md の 16 章)。
# 許す倍率は 1.05 → 1.02 (v6.11.0): 実際の動画 (アニメ MV) と録画の比較で、1.05 では色の多い場面でも 24 色が選ばれ、
# グラデーションが粗くなっていた。8 本の評価用映像でぼかした色差 −1〜−9%、データ +10〜15% (docs/research/phase7x.md の 17 章)
AUTO_COLOR_COUNTS = (24, 32, 48, 64, 96, 127)
AUTO_COLOR_TOLERANCE = 1.02
# K-Means の代表色に一番近いブロックが OkLab × 100 でこれより遠いときは、パターンディザで混ぜてその色に一番近くなる
# 2 つのブロックをパレットに入れる (近いブロックの無い暗いオリーブ色・鮮やかな青などを、混ぜて表せるように)。
# 2 でジャンル 24 本の色差 −1.6%・実写 16 本で −3.9%、データ +0.2〜1.9% (docs/research/phase7x.md の 24 章)
MIX_THRESHOLD = 2.0
# 混ぜる 2 つは、代表色に近い順にこの数のブロックから選ぶ。割合はパターンディザと同じ 1/8 ずつ (線形の明るさ)
MIX_NEIGHBORS = 24
MIX_STEPS = 8


def generate_auto_palette(frames_iter, all_blocks, max_colors=110, device=None, sample_stride=10, seed=0, warn=None,
                          color_space="rgb"):
    """
    動画のフレーム群から代表色を抽出し、all_blocksの中から最適なMinecraftブロックを選出する。
    device は torch のデバイス (CUDA / ROCm / DirectML)。None なら CPU で計算する。
    sample_stride フレームごとに 1 枚をサンプリングする (呼び出し側で間引き済みなら 1)。
    warn は GPU で失敗して CPU でやり直すときに例外を渡して呼ぶ関数 (表示言語は呼び出し側で決める)。
    同じ入力からは常に同じパレットを返すよう乱数シードを固定している。
    color_space は K-Means と「代表色に一番近いブロック」を求める色空間 ("oklab" か "rgb")。
    """
    rng = np.random.default_rng(seed)
    sampled_pixels = []
    # 最初の数十フレームからピクセルをサンプリング
    for i, frame in enumerate(frames_iter):
        if i % sample_stride == 0:
            h, w, c = frame.shape
            # 1フレームあたり 1000 ピクセル程度をサンプリング
            indices = rng.choice(h * w, min(1000, h * w), replace=False)
            sampled = frame.reshape(-1, 3)[indices]
            sampled_pixels.append(sampled)
        if len(sampled_pixels) >= 30:
            break
            
    if not sampled_pixels:
        return all_blocks[:max_colors] # フォールバック
        
    pixels = np.concatenate(sampled_pixels, axis=0).astype(np.float32)
    
    try:
        import torch  # noqa: F401
    except ImportError:
        # PyTorch が無いときは、各画素に一番近いブロックを数えて、多く使われる色から選ぶ (numpy だけ)
        def select(count):
            return _histogram_select(pixels, all_blocks, count)
    else:
        state = {"device": device}

        def select(count):
            try:
                return _kmeans_select(pixels, all_blocks, count, state["device"], seed, color_space)
            except Exception as error:
                if state["device"] is None:
                    raise
                # GPU (DirectML 等) で未対応の演算があれば CPU でやり直す
                if warn is not None:
                    warn(error)
                else:
                    print(f"[MVCodec] Warning: the auto palette could not be computed on the GPU; retrying on the CPU: {error}")
                state["device"] = None
                return _kmeans_select(pixels, all_blocks, count, None, seed, color_space)

    if max_colors != "auto":
        return select(max_colors)
    return _select_color_count(pixels, select)


def _select_color_count(pixels, select):
    """AUTO_COLOR_COUNTS の少ない方から、色の誤差 (各画素と一番近いブロックの OkLab の距離の平均) が、
    一番多い色数のときの AUTO_COLOR_TOLERANCE 倍以内に収まる最初のパレットを選ぶ。色数が少ないほど、
    ディザの粒・ちらつき・データ・座標リストの呼び出し (色の数) が減る。"""
    from mvcodec.block_rdo import rgb_to_oklab

    lab = rgb_to_oklab(np.clip(pixels, 0, 255).astype(np.uint8))

    def error_of(blocks):
        block_lab = rgb_to_oklab(np.array([b["rgb"] for b in blocks], dtype=np.uint8))
        nearest = np.full(len(lab), np.inf, dtype=np.float32)
        for start in range(0, len(block_lab), 16):
            part = block_lab[start:start + 16]
            nearest = np.minimum(nearest, ((lab[:, None, :] - part[None]) ** 2).sum(-1).min(1))
        return float(np.sqrt(nearest).mean())

    largest = select(AUTO_COLOR_COUNTS[-1])
    limit = error_of(largest) * AUTO_COLOR_TOLERANCE
    for count in AUTO_COLOR_COUNTS[:-1]:
        blocks = select(count)
        if error_of(blocks) <= limit:
            return blocks
    return largest


def _histogram_select(pixels, all_blocks, max_colors):
    """各画素に一番近いブロック (RGB の距離) を数え、多い順に max_colors 個を選ぶ。足りなければ候補の順に埋める。"""
    block_rgb = np.array([b["rgb"] for b in all_blocks], dtype=np.float32)
    counts = np.zeros(len(all_blocks), dtype=np.int64)
    for start in range(0, len(pixels), 4096):
        chunk = pixels[start:start + 4096]
        nearest = ((chunk[:, None, :] - block_rgb[None]) ** 2).sum(axis=2).argmin(axis=1)
        counts += np.bincount(nearest, minlength=len(all_blocks))
    order = [int(i) for i in np.argsort(-counts, kind="stable") if counts[i] > 0][:max_colors]
    selected = [all_blocks[i] for i in order]
    for b in all_blocks:
        if len(selected) >= max_colors:
            break
        if b not in selected:
            selected.append(b)
    return selected


def _to_space(rgb, color_space):
    """(N, 3) の RGB (0〜255) の tensor を、K-Means に使う色空間の座標にする。"""
    if color_space == "rgb":
        return rgb
    if color_space == "oklab":
        from mvcodec.color import rgb_to_oklab_torch

        return rgb_to_oklab_torch(rgb) * OKLAB_SCALE
    raise ValueError(f"未対応の色空間: {color_space}")


def _kmeans_select(pixels, all_blocks, max_colors, device, seed, color_space="rgb"):
    """簡易 K-Means で代表色を求め、それぞれに最も近いブロックを選ぶ (どちらも color_space で計算する)。"""
    import torch
    from mvcodec.device import squared_distances

    if device is None:
        device = torch.device("cpu")

    X = _to_space(torch.tensor(pixels, device=device), color_space)
    num_clusters = min(max_colors, len(all_blocks), X.shape[0])

    # 乱数で初期化 (シード固定で再現性を保つ)
    generator = torch.Generator().manual_seed(seed)
    indices = torch.randperm(X.shape[0], generator=generator)[:num_clusters].to(device)
    centroids = X[indices]

    for _ in range(10):  # 最大10イテレーション
        labels = torch.argmin(squared_distances(X, centroids), dim=1)
        new_centroids = torch.stack([X[labels == k].mean(dim=0) if (labels == k).sum() > 0 else centroids[k] for k in range(num_clusters)])
        if torch.allclose(centroids, new_centroids, atol=1e-2):
            break
        centroids = new_centroids

    # centroids に最も近い Minecraft ブロックを選択
    all_rgb_tensor = _to_space(
        torch.tensor(np.array([b['rgb'] for b in all_blocks], dtype=np.float32), device=device), color_space)
    best = torch.argmin(squared_distances(centroids, all_rgb_tensor), dim=1).cpu().numpy()

    # 大きいクラスタの代表色から順に、その色を表すブロック (1 つか、混ぜる 2 つ) を入れる
    selected_blocks = []
    sizes = np.bincount(labels.cpu().numpy(), minlength=num_clusters)  # DirectML には bincount が無い
    centroid_rgb = _centroids_to_rgb(centroids.cpu().numpy(), color_space)
    for k in np.argsort(-sizes, kind="stable"):
        for idx in _blocks_for_color(centroid_rgb[k], all_blocks, MIX_THRESHOLD):
            block = all_blocks[idx]
            if block not in selected_blocks and len(selected_blocks) < max_colors:
                selected_blocks.append(block)
    for best_idx in best:
        block = all_blocks[int(best_idx)]
        if block not in selected_blocks and len(selected_blocks) < max_colors:
            selected_blocks.append(block)

    # 指定数に満たない場合は、残りを順番に埋める
    for b in all_blocks:
        if len(selected_blocks) >= max_colors:
            break
        if b not in selected_blocks:
            selected_blocks.append(b)

    return selected_blocks


def _centroids_to_rgb(centroids, color_space):
    """K-Means の代表色 (color_space の座標) を sRGB (0〜255) に戻す。"""
    if color_space == "rgb":
        return np.clip(centroids, 0, 255)
    lab = centroids / OKLAB_SCALE
    m2_inv = np.linalg.inv(np.array([[0.2104542553, 0.7936177850, -0.0040720468], [1.9779984951, -2.4285922050, 0.4505937099],
                                     [0.0259040371, 0.7827717662, -0.8086757660]]))
    m1_inv = np.linalg.inv(np.array([[0.4122214708, 0.5363325363, 0.0514459929], [0.2119034982, 0.6806995451, 0.1073969566],
                                     [0.0883024619, 0.2817188376, 0.6299787005]]))
    lin = np.clip((lab @ m2_inv.T) ** 3 @ m1_inv.T, 0, 1)
    srgb = np.where(lin <= 0.0031308, lin * 12.92, 1.055 * lin ** (1 / 2.4) - 0.055)
    return srgb * 255



def _blocks_for_color(rgb, all_blocks, threshold):
    """色 rgb を表すブロックの番号: 一番近いブロックが OkLab × 100 で threshold 以内ならそれ 1 つ、遠ければ、
    パターンディザで混ぜたとき (線形の明るさで 1/8 ずつの割合) に一番近くなる 2 つのブロック。"""
    from mvcodec.block_rdo import rgb_to_oklab, _srgb_to_linear

    block_rgb = np.array([b["rgb"] for b in all_blocks], dtype=np.float32)
    target = rgb_to_oklab(np.asarray(rgb, np.float32)[None])[0]
    block_lab = rgb_to_oklab(block_rgb)
    dist = np.linalg.norm(block_lab - target, axis=1)
    nearest = int(dist.argmin())
    if dist[nearest] <= threshold:
        return [nearest]
    near = np.argsort(dist)[:MIX_NEIGHBORS]
    lin = _srgb_to_linear(block_rgb[near])
    t = np.arange(1, MIX_STEPS)[:, None, None, None] / MIX_STEPS
    mixes = lin[None, :, None] * (1 - t) + lin[None, None, :] * t  # (steps, n, n, 3)
    mix_srgb = np.where(mixes <= 0.0031308, mixes * 12.92, 1.055 * np.maximum(mixes, 0) ** (1 / 2.4) - 0.055) * 255
    mix_dist = np.linalg.norm(rgb_to_oklab(mix_srgb.reshape(-1, 3)).reshape(mixes.shape) - target, axis=-1)
    _, i, j = np.unravel_index(int(mix_dist.argmin()), mix_dist.shape)
    if mix_dist.min() >= dist[nearest]:
        return [nearest]
    return [int(near[i]), int(near[j])]
