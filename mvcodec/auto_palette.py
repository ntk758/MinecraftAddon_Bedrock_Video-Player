import numpy as np

# K-Means とブロックの対応を行う色空間。oklab は知覚的に均等な色空間 (GPU の減色と同じ)
COLOR_SPACES = ("oklab", "rgb")
# OkLab の L は 0〜1 なので、収束判定 (atol) と RGB の 0〜255 の目盛りをそろえるために掛ける
OKLAB_SCALE = 100.0


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
        return _histogram_select(pixels, all_blocks, max_colors)

    try:
        return _kmeans_select(pixels, all_blocks, max_colors, device, seed, color_space)
    except Exception as error:
        if device is None:
            raise
        # GPU (DirectML 等) で未対応の演算があれば CPU でやり直す
        if warn is not None:
            warn(error)
        else:
            print(f"[MVCodec] Warning: the auto palette could not be computed on the GPU; retrying on the CPU: {error}")
        return _kmeans_select(pixels, all_blocks, max_colors, None, seed, color_space)


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

    selected_blocks = []
    for best_idx in best:
        block = all_blocks[int(best_idx)]
        if block not in selected_blocks:
            selected_blocks.append(block)

    # 指定数に満たない場合は、残りを順番に埋める
    for b in all_blocks:
        if len(selected_blocks) >= max_colors:
            break
        if b not in selected_blocks:
            selected_blocks.append(b)

    return selected_blocks
