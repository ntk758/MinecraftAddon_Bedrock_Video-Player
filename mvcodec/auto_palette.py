import numpy as np

def generate_auto_palette(frames_iter, all_blocks, max_colors=110, use_gpu=False):
    """
    動画のフレーム群から代表色を抽出し、all_blocksの中から最適なMinecraftブロックを選出する。
    """
    sampled_pixels = []
    # 最初の数十フレームからピクセルをサンプリング
    for i, frame in enumerate(frames_iter):
        if i % 10 == 0:
            h, w, c = frame.shape
            # 1フレームあたり 1000 ピクセル程度をサンプリング
            indices = np.random.choice(h * w, 1000, replace=False)
            sampled = frame.reshape(-1, 3)[indices]
            sampled_pixels.append(sampled)
        if len(sampled_pixels) >= 30: # 300フレーム分まで
            break
            
    if not sampled_pixels:
        return all_blocks[:max_colors] # フォールバック
        
    pixels = np.concatenate(sampled_pixels, axis=0).astype(np.float32)
    
    # 簡易 K-Means
    try:
        import torch
        device = "cuda" if use_gpu and torch.cuda.is_available() else "cpu"
        
        X = torch.tensor(pixels, device=device)
        num_clusters = min(max_colors, len(all_blocks))
        
        # K-Means++ のように初期化（ここではランダム）
        indices = torch.randperm(X.shape[0])[:num_clusters]
        centroids = X[indices]
        
        for _ in range(10): # 最大10イテレーション
            dists = torch.cdist(X, centroids)
            labels = torch.argmin(dists, dim=1)
            new_centroids = torch.stack([X[labels == k].mean(dim=0) if (labels == k).sum() > 0 else centroids[k] for k in range(num_clusters)])
            if torch.allclose(centroids, new_centroids, atol=1e-2):
                break
            centroids = new_centroids
            
        centroids_rgb = centroids.cpu().numpy()
        
        # centroids に最も近い Minecraft ブロックを選択
        selected_blocks = []
        all_rgb = np.array([b['rgb'] for b in all_blocks], dtype=np.float32)
        all_rgb_tensor = torch.tensor(all_rgb, device=device)
        
        for c in centroids_rgb:
            c_tensor = torch.tensor(c, device=device).unsqueeze(0)
            dists = torch.cdist(c_tensor, all_rgb_tensor)
            best_idx = torch.argmin(dists).item()
            block = all_blocks[best_idx]
            if block not in selected_blocks:
                selected_blocks.append(block)
                
        # 指定数に満たない場合は、残りを順番に埋める
        for b in all_blocks:
            if len(selected_blocks) >= max_colors:
                break
            if b not in selected_blocks:
                selected_blocks.append(b)
                
        return selected_blocks
    except ImportError:
        # PyTorchがない場合は単純に最初のN個を返す (あるいは別の軽量アルゴリズム)
        return all_blocks[:max_colors]
