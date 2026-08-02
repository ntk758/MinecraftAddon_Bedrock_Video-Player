# TODO & Future Roadmap

本ドキュメントでは、次の開発フェーズで取り組むべき推奨機能拡張、アイデア、およびバックログ課題を優先度別にまとめています。

---

## 🔴 優先度: 高 (High Priority)

### 1. 音声同期再生機能 (Audio Sync System)
- **概要**: 動画の音声トラック（AAC / MP3）を Minecraft の Custom Sound Resource Pack（`sounds.json` + `.ogg`）へ書き出し。
- **実装案**: 
  - `video_player_gui.py` で FFmpeg を使い音声 `.ogg` を同時出力し、Resource Pack を自動ビルド。
  - `main.js` の `startPlayback()` 実行時に `dimension.playSound()` または `world.playSound()` を呼び出し、映像と音声を完全に同期再生。

### 2. GUI の Web アプリケーション化 (Web-based Converter)
- **概要**: Tkinter ベースのローカル GUI に加え、PyScript または WebAssembly (Wasm) + FFmpeg.wasm を用いて、ブラウザ上で完結する Web 版コンバーターを開発。

---

## 🟡 優先度: 中 (Medium Priority)

### 3. 3D 立体ブロック動画表示モード (3D Holographic Display)
- **概要**: 2D 平面スクリーンだけでなく、立体モデル（アバターや voxel アニメーション等）の 3D 差分データに対応。
- **データ構造拡張**: インデックス $idx = z \times (W \times H) + y \times W + x$ へ拡張し、3D 空間のブロック配置を展開。

### 4. 画面アスペクト比の自由自動調整 (Dynamic Aspect Ratio Fix)
- **概要**: 16:9 や 4:3 などの動画アスペクト比に合わせて、黒帯 (Letterbox) を自動計算して最適な幅・高さを決定するプリセット。

---

## 🟢 優先度: 低 (Low Priority / Research)

### 5. 可変パレットの全自動生成 (Per-Video Custom K-Means Block Mapping)
- **概要**: 動画ごとのカラーヒストグラムを自動解析し、最も色差 $\Delta E$ が小さくなるブロックの組み合わせ（16色/32色）を全ブロックDBから全自動選択。

### 6. ゲーム内 UI フォーム対応 (In-Game Modal Form GUI)
- **概要**: `/scriptevent` コマンド入力の代わりに、プレイヤーが特定アイテム（例: コンパスや時計）を使用すると Bedrock の `ActionFormData` / `ModalFormData` ダイアログが開き、GUI で動画選択や再生・停止ができる機能。
