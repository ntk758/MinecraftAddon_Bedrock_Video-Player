import numpy as np

def generate_auto_palette(frames_iter, all_blocks, max_colors=110, device=None, sample_stride=10, seed=0):
    """
    動画のフレーム群から代表色を抽出し、all_blocksの中から最適なMinecraftブロックを選出する。
    device は torch のデバイス (CUDA / ROCm / DirectML)。None なら CPU で計算する。
    sample_stride フレームごとに 1 枚をサンプリングする (呼び出し側で間引き済みなら 1)。
    同じ入力からは常に同じパレットを返すよう乱数シードを固定している。
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
        # PyTorchがない場合は単純に最初のN個を返す
        return all_blocks[:max_colors]

    try:
        return _kmeans_select(pixels, all_blocks, max_colors, device, seed)
    except Exception as error:
        if device is None:
            raise
        # GPU (DirectML 等) で未対応の演算があれば CPU でやり直す
        print(f"[MVCodec] 警告: 自動パレットを GPU で計算できないため CPU で再計算します: {error}")
        return _kmeans_select(pixels, all_blocks, max_colors, None, seed)


def _kmeans_select(pixels, all_blocks, max_colors, device, seed):
    """簡易 K-Means で代表色を求め、それぞれに最も近いブロックを選ぶ。"""
    import torch
    from mvcodec.device import squared_distances

    if device is None:
        device = torch.device("cpu")

    X = torch.tensor(pixels, device=device)
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
    all_rgb_tensor = torch.tensor(np.array([b['rgb'] for b in all_blocks], dtype=np.float32), device=device)
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
