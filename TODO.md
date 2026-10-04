# TODO & Future Roadmap

本ドキュメントでは、次の開発フェーズで取り組むべき推奨機能拡張、アイデア、およびバックログ課題を優先度別にまとめています。

---

## 🔴 優先度: 高 (High Priority / Phase 7.x Next-Gen Prediction)

### 1. Motion Vector Prediction (高度な動き予測)
- **概要**: ブロック単位での動きベクトル（Motion Vector）を算出し、フレーム間のパン・スクロールなどの動きを予測して圧縮効率を飛躍的に高める。
- **実装案**: マクロブロックベースの探索アルゴリズム（Diamond Search 等）の導入。

### 2. Tile Dictionary (タイル辞書圧縮)
- **概要**: 頻出するブロックのパターン（例：テキスト、UI要素、特定のテクスチャ）を辞書化し、インデックス参照で描画する。
- **実装案**: 空間的冗長性を排除するLZ77ベースのアプローチをタイルに適用。

### 3. GUI の Web アプリケーション化 (Web-based Converter)
- **概要**: Tkinter ベースのローカル GUI に加え、PyScript または WebAssembly (Wasm) + FFmpeg.wasm を用いて、ブラウザ上で完結する Web 版コンバーターを開発。

---

## 🟡 優先度: 中 (Medium Priority)

### 4. 3D 立体ブロック動画表示モード (3D Holographic Display)
- **概要**: 2D 平面スクリーンだけでなく、立体モデル（アバターや voxel アニメーション等）の 3D 差分データに対応。
- **データ構造拡張**: インデックス $idx = z \times (W \times H) + y \times W + x$ へ拡張し、3D 空間のブロック配置を展開。

### 5. 画面アスペクト比の自由自動調整 (Dynamic Aspect Ratio Fix)
- **概要**: 16:9 や 4:3 などの動画アスペクト比に合わせて、黒帯 (Letterbox) を出さずに済む最適な幅・高さを自動決定するプリセット (現状は指定サイズ内に中央寄せのレターボックス)。

---

## 🟢 優先度: 低 (Low Priority / Research)

### 6. 可変パレットの精度向上 (Per-Video Custom K-Means Block Mapping)
- **概要**: 現在の自動パレット (`--palette auto`) は RGB 空間の簡易 K-Means で 39 色の候補から選んでいる。OkLab 空間でのクラスタリングや、ブロック候補 DB の拡充 (重力で落ちないブロックに限る) で色差 $\Delta E$ をさらに下げる。

### 7. MS-SSIM 指標の実装
- **概要**: `benchmark/metrics/ms_ssim.py` は未実装 (常に NaN)。マルチスケール SSIM を実装してベンチマークに加える。

---

## ✅ 完了済み (Done)
- 音声同期再生 (10秒分割 OGG + Resource Pack 自動生成)
- ゲーム内リモコン GUI (コンパス使用で `ActionFormData` / `ModalFormData`)
