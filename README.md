# Block Video Player — 統合版(Bedrock)ブロック動画プレイヤー (v2.3.0)

[umbreonben/mc-cushion-bad-apple](https://github.com/umbreonben/mc-cushion-bad-apple) (Java版 datapack) を Minecraft統合版 (Bedrock Edition) の Behavior Pack + Script API で再実装した超高速・超軽量・高画質ブロック動画再生アドオン＆変換システム。

**最新バージョン v2.3.0**: キーフレーム (I/P Frame, GOP=30) 方式を導入！画面崩れのない超高速 14ms シーク位置復元に対応。

---

## 🚀 主要機能・到達点 (v2.3.0)

- 🎞️ **キーフレーム (I/P Frame) 方式 ＆ 超高速画面復元**:
  - 30フレームごとに画面全体の完全ピクセルを保持する Iフレーム (キーフレーム) を挿入。シーク（ジャンプ）時に画面が一切崩れることなく **わずか 14ms で正確に場面を瞬時復元**。
- 📦 **完全独立スタンドアロン EXE (`BlockVideoPlayer.exe`) 対応**:
  - `python build_standalone.py` を実行するだけで、Python や Node.js のない PC 環境でもダブルクリックで動作する単体実行ファイル `dist/BlockVideoPlayer/BlockVideoPlayer.exe` を一括ビルド。ポータブル用 `ffmpeg.exe` の自動検出にも対応。
- ⚡ **GPU アクセラレーション (PyTorch / CUDA & FFmpeg HWAccel)**:
  - NVIDIA GPU 等の CUDA 環境で PyTorch テンソル演算による**全フレーム超並列減色**を実行。FFmpeg も `-hwaccel auto` で動画抽出を高速化（GPU未搭載時はCPUスレッドプールへ自動フォールバック）。
- 👑 **ウルトラ全110色パレット (`ultra_110`)**:
  - コンクリート (16色)、テラコッタ (17色)、自発光ブロック (6種)、羊毛 (16色)、木材 (11種)、コンクリート粉 (16色)、鉱石・石材 (22種) の**全112個のマイクラ実在ブロック**を精密解析。
  - CIELAB 色空間 $\Delta E = 20.54$ の**過去最高の色再現性**と圧倒的グラデーション・クオリティを実現。
- 🎵 **10秒分割 OGG 音声トラック同期再生**:
  - FFmpeg により動画から音声を10秒単位 `.ogg` に分割抽出。映像の再生・一時停止・シーク操作に音声が自動追従。
- 🎮 **ゲーム内リモコン GUI (`ActionFormData`)**:
  - プレイヤーが「コンパス (`minecraft:compass`)」を手に持ち右クリックすると、画面中央に**操作リモコンUI**が開く。
  - ▶再生/一時停止、⏹停止＆クリア、⏭次の動画、⏮前の動画、🔊音量調整、⏩シーク時間を直感操作可能。
- 🎬 **マルチ動画（多タイトル）＆ 遅延ロード (Lazy Load)**:
  - 1つの `.mcpack` に複数の動画を格納。起動時のワールド読み込み遅延はわずか **0.05 秒（即時起動）**。
- 📦 **長尺動画対応 (1時間 48.24 MB)**:
  - **Delta VarInt + Base64 圧縮** と **Adaptive FPS** により、データ容量を初期比 **97.2% 削減**。1GB パック制限内に **長編映画 約21本分**（1分ショート動画なら約 1,280本）を保存可能。
- ⚡ **超高速変換 & 超軽量描画**:
  - Pillow Cネイティブ量子化 + スレッドプール並列処理により、**2,800+ fps** で超高速変換。
  - マイクラ側の描画処理は 1tick (50ms) あたりわずか **0.48 ms**（負荷率 1% 以下）。

---

## 📊 パレット別・色再現性 ＆ 変換速度マトリクス

| パレット選択 | 使用色数 | 変換速度 (fps) | **CIELAB 平均色差 $\Delta E$** (低いほど高画質) | 画質・色再現性の特長 |
|---|---|---|---|---|
| concrete | 16色 | 181.3 fps | 29.97 | 基本色（ドット絵向け） |
| full | 39色 | 2,469.3 fps | 27.30 | 自発光・テラコッタ拡張 |
| all_55 | 63色 | 2,252.7 fps | 22.02 | 羊毛・鉱石追加 |
| 👑 **ultra_110 (ウルトラ)** | **112色** | **2,829.1 fps** | 👑 **20.54 (最高画質)** | 🌟 **実写・アニメの質感・グラデーション最高峰** |

---

## 🎮 使い方

### 1. 動画から `.mcpack` の作成 (GUI)

```bash
python video_player_gui.py
```

1. **「動画を追加…」** ボタンで1つまたは複数の動画ファイル (MP4 / MKV / AVI / WEBM 等) を一括追加。
2. 画質プリセット（軽量 64x64, 標準 96x96, 高画質 128x128）や再生間隔 (tick) を設定。
3. パレットで **「ウルトラ全110色（全マイクラ実在色・最高画質）」** を選択。
4. ディザリング手法（`Floyd-Steinberg`, `Atkinson`, `Ordered (Bayer)` など）を選択。
5. **「.mcpack を作成」** をクリックすると、音声抽出・フレーム変換・リソースパック自動ビルドが行われ `.mcpack` が出力されます。

### 2. Minecraft への導入 ＆ 操作

1. 出力された `.mcpack` をダブルクリックして Minecraft 統合版へインポート。
2. ワールドの Behavior Pack で有効化してワールドを起動。
3. **ゲーム内リモコン操作**:
   - インベントリから **「コンパス (`minecraft:compass`)」** を手に持ち右クリックするとリモコン画面が開きます。
   - **▶ 再生 / ⏸ 一時停止**: 映像と音声を再生・ストップ。
   - **⏹ 停止 ＆ クリア**: 画面クリアと音声停止。
   - **⏭ 次の動画 / ⏮ 前の動画**: 再生タイトルの切替。
   - **🔊 音量設定**: 音量スライダーで調整。
   - **⏩ シーク**: 時間移動（指定秒数へ即座にジャンプ追従）。
   - **📜 動画ライブラリ**: 収録動画一覧から選択再生。
4. **コマンド操作 (`/scriptevent`)**:
   - `/scriptevent badapple:setup` … プレイヤー足元を起点に原点を保存
   - `/scriptevent badapple:start` … 再生開始
   - `/scriptevent badapple:stop` … 停止・盤面クリア
   - `/scriptevent badapple:list` … 収録動画一覧を表示
   - `/scriptevent badapple:play <動画ID>` … 指定動画を選択再生
   - `/scriptevent badapple:gui` … リモコンGUIを開く

---

## 📂 プロジェクトの主要ファイル

```
.
├── convert.py                 # コア変換スクリプト (Delta VarInt Base64, 110色パレット, 5種ディザ)
├── video_player_gui.py        # 音声抽出・複数動画対応 GUI アプリケーション (Tkinter / Treeview)
├── main.js                    # Bedrock Script API 再生スクリプト (音声同期, リモコンGUI)
├── manifest.json              # Behavior Pack マニフェスト (v2.0.0, @minecraft/server-ui 依存)
├── pack_metadata.py           # バージョン・リリースノートの一元管理
├── PROJECT_STATUS.md          # 引き継ぎ: 現在の進捗
├── ARCHITECTURE.md            # 引き継ぎ: アーキテクチャ・データ構造・符号化仕様
├── DECISIONS.md               # 引き継ぎ: 採否判断マトリクスと技術的根拠
├── TODO.md                    # 引き継ぎ: 今後の開発課題・アイデア
└── BENCHMARK.md               # 引き継ぎ: 性能測定・ベンチマーク詳細結果
```

---

## 📜 バージョン履歴

- **v2.3.0**: キーフレーム (I/P Frame, GOP=30) 方式を導入。画面乱れのない超高速 14ms シーク復元に対応。
- **v2.2.0**: PyInstaller による独立スタンドアロン EXE ビルド環境 (`build_standalone.py`) およびポータブル FFmpeg 自動検索を追加。既存の全スクリプトを完全保持。
- **v2.1.0**: GPU (PyTorch / CUDA テンソル一括減色演算 & FFmpeg `-hwaccel auto` デコード) アクセラレーションおよびCPUフォールバック機能を完全統合。
- **v2.0.0**: 全110色以上のウルトラパレット (`ultra_110` / 112ブロック, $\Delta E=20.54$)、10秒単位OGG音声同期システム、コンパスによるゲーム内リモコンGUI (`ActionFormData`) を完全統合。
- **v1.9.0**: 音声トラック分離とリモコンGUIプロトタイプの導入。
- **v1.8.0**: 全50+色パレット解析、5種ディザリング (Atkinson, Burkes, Sierra, Ordered) 実装。
- **v1.7.0**: 1パック複数動画搭載マルチ動画アーキテクチャ (`videos.js` 静的インポート遅延ロード) 導入。
- **v1.6.0**: Adaptive FPS (動的フレーム間引き) 導入により 1時間あたり 48.24 MB に長尺圧縮。
- **v1.5.0**: Delta VarInt + Base64 符号化導入により JSON容量を初期比 87.5% 削減。
- **v1.4.1**: Pillow Cネイティブ量子化 + `ThreadPoolExecutor` 全コア並列化により 2,500+ fps 突破。
- **v1.1.0**: 16色 Concrete マップカラーパレットへの最近傍マッチング導入。

---

## 🔒 テスト・ベンチマーク用画像の管理方針

テスト・ベンチマーク用のPNG画像はリポジトリサイズ肥大化を防ぐため **Git管理対象外** としています。

**除外対象** (`.gitignore` で指定):
- `frames/` — FFmpegで抽出したPNG連番（変換の入力用一時ファイル）
- `scratch/` — ベンチマーク・実験スクリプトと生成画像
- `test_frames*/`, `bench_frames*/` — テスト・ベンチマーク用に自動生成されるダミーPNG
- `**/step*_test_frames/` — ステップ別ベンチマークで生成される画像
- `ci_frames_data.js`, `perf_output.*` — CI・ベンチマークの一時出力ファイル
- `*.mcpack` — ビルド生成物（GUIで再生成可能）

**除外しないファイル** (パック本体に必要):
- `pack_icon.png` — アドオンのサムネイル画像（`.gitignore` で `!pack_icon.png` として保護）
- `manifest.json`, `main.js` — アドオン本体のソースコード
