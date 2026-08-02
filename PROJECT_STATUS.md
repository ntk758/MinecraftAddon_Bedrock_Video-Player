# Project Status — Block Video Player for Minecraft Bedrock Edition

## 1. プロジェクト概要
本プロジェクトは、動画ファイル（MP4 / MKV / AVI / WEBM 等）を Minecraft Bedrock Edition（統合版）の Behavior Pack（.mcpack）へ超高速かつ最小容量で変換し、ゲーム内のブロック盤面上でスムーズに動動画再生するアドオン＆変換ツール群です。

- **最新バージョン**: `v1.8.0`
- **対象環境**: Minecraft Bedrock Edition 1.21.0 以上 (Script API v1.x)
- **変換GUI環境**: Python 3.10+ (Pillow, NumPy, Tkinter, FFmpeg)

---

## 2. 現在の達成状況と到達点

### 🚀 圧倒的な圧縮率と長尺保存
- **1時間動画あたり 48.24 MB (0.047 GB)** のデータ容量達成（初期バージョン v1.3.4 比 **97.2% 削減**）。
- 1GB (1024MB) のパック制限容量内に **約 21.2 時間分（長編映画 約 10 本分）**、1分ショート動画なら **約 1,280 本** を丸ごと格納可能。

### ⚡ 爆速スルーブットと軽量描画
- **変換速度**: **1,000 〜 4,200 fps**（Pillow Quantize + `ThreadPoolExecutor` 全コアマルチスレッド並列処理）。
- **マイクラ側描画負荷**: 1tick (50ms) あたりわずか **0.48 ms**（負荷率 1% 以下）。`BlockPermutation` 一括キャッシュと単一座標オブジェクト使い回しによりメモリGCスパイクを撲滅。

### 🎨 高精細な色再現性と表現力
- **全50+色マイクラブロック解析**: 16色 Concrete + 17色 Terracotta + 6種 自発光ブロック（`sea_lantern`, `glowstone`, `shroomlight`, 3種 `froglight`）+ 16色 Wool + 8種 鉱石/金属ブロック。
- **CIELAB 色空間最適化**: 人間視覚モデル上での色距離 $\Delta E$ 再計算。
- **全5種類ディザリング**: `Floyd-Steinberg`, `Atkinson`, `Burkes`, `Sierra-Lite`, `Ordered (4x4 Bayer Matrix)` を自由選択。

### 🎬 マルチ動画（多タイトル）＆ 遅延ロード
- 1つの `.mcpack` に複数動画を搭載し、GUI及びゲーム内コマンド `/scriptevent <ns>:list` と `{ns}:play <動画ID>` で選択・即時再生可能。
- **遅延ロード (Lazy Load)** により、ワールド起動時の読み込み時間 **0.05 秒（即時起動）**。

---

## 3. リポジトリの主要ファイル構成

```
.
├── convert.py                 # コア変換スクリプト (Delta VarInt Base64, 55色パレット, 5種ディザ)
├── video_player_gui.py        # 複数動画対応 GUI アプリケーション (Tkinter / Treeview)
├── main.js                    # Bedrock Script API 再生スクリプト (Base64/VarIntデコーダー, Permutationキャッシュ)
├── manifest.json              # Behavior Pack マニフェストファイル
├── pack_metadata.py           # リリースバージョン・更新履歴の一元管理
├── PERFORMANCE_REPORT.md      # 最新パフォーマンス報告
├── .github/workflows/         # CI 性能回帰自動テスト (5%低下判定)
│   └── benchmark_regression.yml
├── PROJECT_STATUS.md          # [本書] 現在のプロジェクト進捗
├── ARCHITECTURE.md            # アーキテクチャ・データ構造・描画仕様
├── DECISIONS.md               # 採否判断マトリクスと技術的根拠
├── TODO.md                    # 今後の開発課題・アイデア
└── BENCHMARK.md               # 性能測定・ベンチマーク詳細結果
```
