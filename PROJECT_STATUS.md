# Project Status — Block Video Player for Minecraft Bedrock Edition

## 1. プロジェクト概要
本プロジェクトは、動画ファイル（MP4 / MKV / AVI / WEBM 等）を Minecraft Bedrock Edition（統合版）の Behavior Pack（.mcpack）へ超高速かつ最小容量で変換し、ゲーム内のブロック盤面上でスムーズに動動画再生するアドオン＆変換ツール群です。

- **最新バージョン**: `v6.25.0` (Phase 10.3: 暗い場面の色、人物の切り抜きの残像)
- **対象環境**: Minecraft Bedrock Edition 最新版 (安定版 Script API `@minecraft/server` 1.19.0 / `@minecraft/server-ui` 1.2.0。ベータ API 不要)
- **変換GUI環境**: Python 3.12+ (PyTorch: CUDA / ROCm / DirectML, Pillow, NumPy, Tkinter, FFmpeg) または **独立スタンドアロン EXE (`BlockVideoPlayer.exe`)**

---

## 2. 現在の達成状況と到達点

### 🧊 Phase 8.3: 2.5D パラメトリック立体 (v6.4.0)
- `relief` / `extrude` / `inflate` を色の平面と値の平面 (表面の段 / 中央にそろえた厚み) の 2 枚の 2D 映像として送り (`layout: "heightfield"`)、アドオンが変わった列だけ立体に組み立て直す。`--volume-encoding auto|planes|voxels` (既定 auto = 送れるなら planes)。
- 置くブロック・呼び出しは同じで、データは deflate 後 −54〜85% (64×48×16 の合成映像)。

### ⚙️ Phase 8.1: 描画方式の自動選択 (v6.1.0)
- 実機の bench で、`fillBlocks` は 1 回 約 15 µs + 1 ブロック 約 0.55 µs、`setBlockPermutation` は 1 ブロック約 2.3 µs と判明。最適な置き方はフレームの内容で変わる。
- 描画方式 `auto` (既定) が、フレームごとに箱 / 座標リスト / 1 個ずつ の時間を見積もって一番速い方式を選ぶ。`bench` は 1 回の費用と 1 ブロックの費用を分けて求め、ワールドに保存する。
- GUI で立体の表示モードを選ぶと、ディザを自動で「なし」にする (Blue Noise の粒が立体の表面のノイズになるため)。
- 立体の変換を約 4 倍高速化 (タイル辞書・ランの抽出・3D 動き探索をまとめて計算。出力は同じ) し、変換中の進み具合と残り時間をログに表示。

### 🧊 Phase 7.x: 次世代予測圧縮と 3D ホログラム (v6.0.0)
- **予測圧縮 (`varint_rle_v5`)**: 動き補償 (平面 8×8 + 全体探索 / 立体 8×8×8 ブリック)、暗黙のタイル辞書、ラン位置の間隔符号化を、フレームごとに一番小さいものを選んで使う。v5.4.0 比でデータ 47〜88% 減 (合成映像 128×72、ディザなし)。
- **3D ホログラム**: `relief` (元の明るさ + ヒステリシス) / `extrude` / `inflate` / MagicaVoxel `.vox` (シーングラフ・アニメーション、向きの修正)。GUI の表示モードと 10 言語の表示に対応。
- **まとめ置き描画**: `rect` (箱、立体は奥行きにも伸ばす) / `list` (`ListBlockVolume`) / `block` を `/scriptevent <接頭辞>:render` で切替。`bench` で実機の費用を計測。呼び出しは v5.4.0 の 1/1.7〜1/7 (`rect`)、1 フレーム 2〜33 回 (`list`)。
- **再生の改善**: キーフレームでも変わったブロックだけを置く (論理盤面と表示盤面の分離)、置けなかったマスは次のフレームで置き直す、256×144 以上でも常時読み込みを分割登録して設置できる、立体を建築限界内に収める。
- **ツール**: `scripts/preview_volume.py` (変換結果の等角投影 GIF)、`benchmark/codec_bench.py` (別の版とデータ量・呼び出し数を比較)。詳細は `docs/research/phase7x.md`。

### 🌐 ゲーム内表示の多言語対応 (v5.4.0)
- **プレイヤーごとの言語**: チャット・リモコン画面を Bedrock の翻訳キー (`texts/*.lang`) で表示。各プレイヤーのゲーム言語で表示され、未対応の言語は英語。
- **変換器のログ**も GUI で選んだ言語 (`--lang`) で表示。
- **結合テスト**: `main.js` を Minecraft API のモックで動かし、再生・表示の翻訳漏れを検査。

### 🌐 GUI 多言語対応 (v5.3.0)
- **10 言語**: 日本語・English・简体中文・繁體中文・한국어・Español・Português (Brasil)・Français・Deutsch・Русский。翻訳は `locales/*.json`。
- **言語切替**: GUI 右上の選択欄で即時切替 (入力内容・動画リスト・ログを保持)。初回は OS の表示言語、以後は保存した言語。

### 🟥 AMD GPU 対応 (v5.2.0)
- **GPU バックエンドの抽象化 (`mvcodec/device.py`)**: CUDA / ROCm / DirectML を自動検出 (`--device auto`)。DirectML では内蔵 GPU より単体 GPU を優先。
- **DirectML 互換の距離計算**: `torch.cdist` を行列積ベースの二乗距離に置き換え。
- **GPU 自己テストと CPU フォールバック**: 未対応演算などで GPU 処理が動かない場合は変換前に検出して CPU へ切り替え。
- **GUI の変換デバイス選択** と、GUI からの変換が引数エラーで失敗していた不具合の修正。

### 🛠 品質改善 (v5.1.0)
- **GOP 先頭の強制キーフレーム化**: 自動パレットのシーン切替で色が崩れる問題を解消。シークも直近キーフレームから正しく復元。
- **2 パス・ストリーミング変換**: 動画全体をメモリに載せずに変換 (長尺・高解像度でもメモリ一定)。
- **fps を動画データに格納**: 再生速度・音声同期・シーク時間を各動画の fps から決定。
- **再生まわりの不具合修正**: シーク後の再開、動画切替、大画面の盤面クリア (runJob で分割) など。
- **テストと CI**: Python デコーダと `codec.js` の往復テスト、Ubuntu 上での pytest・ベンチマーク実行。

### 🔬 Phase 7: Research Edition 完成 (v5.0.0 新機能)
- **オブジェクト指向JSエンジン (VideoPlayer)**: `VideoPlayer` クラスの導入により、1つのワールド内で複数のスクリーン（マルチスクリーン）同時再生が可能に。
- **局所的SSIMベースの知覚的RDO**: エッジやディテールを保持しつつ、人間の視覚特性（SSIM）に合わせたレート歪み最適化を実現。
- **シーン適応型パレット & シーンGOP**: `0.5*SAD + 0.3*Hist + 0.2*Edge` の式に基づく動的GOPリサイズと、パレットハッシュを用いたシーンごとの適応型パレット切り替え。
- **統合ベンチマークフレームワーク**: `benchmark/` 傘下に SSIM, PSNR, LPIPS, $\Delta E_{2000}$ を測定するモジュラー設計のフレームワークを構築 (`run.py --deep` 対応)。

### ⚡ 高画質軽量化バッチ上限 ＆ 音楽再生補修 (v2.4.0 新機能)
- **128×128高画質での重さ解消 (MAX_BLOCKS_PER_TICK = 800)**: 1tick内でのブロック設置上限数を設定し、描画スパイク（ラグ）を解消。
- **playSound() 大域オーディオ再生**: BGM音量オフにも影響されない音響再生システム。44.1kHzステレオOGG切り出しによる確実な音声同期。

### 🎞️ キーフレーム (I/P Frame, GOP=30) シーク復元 (v2.3.0 新機能)
- **画面崩れゼロの超高速 14ms シーク追従**: 30フレームごとに完全なキーフレーム (Iフレーム) を自動挿入し、目標フレームへのジャンプ時に画面の乱れなく一瞬で正確な場面を復元。
- **データサイズ最適化**: キーフレームを挿入しても容量増加は微増に抑えられ、1時間あたり 48MB 級の超高度圧縮を維持。

### 📦 完全独立スタンドアロン EXE ビルド (v2.2.0 新機能)
- **非開発者環境対応 (`build_standalone.py`)**: PyInstaller により、Python・Node.js 未インストール環境でも動作する単体実行ファイル `dist/BlockVideoPlayer.exe` を全自動ビルド可能。
- **ポータブル FFmpeg 自動検出**: PATH 未設定環境でも、アプリと同階層に置かれた `ffmpeg.exe` を自動検出して優先利用。
- **既存開発ファイル保持**: `convert.py`, `video_player_gui.py`, `main.js` 等のソースコードは 100% 維持。

### ⚡ GPU アクセラレーション (v2.1.0 新機能)
- **PyTorch テンソル一括減色 (CUDA / ROCm / DirectML)**: GPU 上で全ピクセルの色距離計算・パレット量子化を並列実行。未搭載時・非対応時は CPU スレッドプールへ自動フォールバック。
- **FFmpeg GPU HWAccel デコード**: `-hwaccel auto` による動画フレーム抽出のハードウェア加速。

### 🎵 音声同期 ＆ リモコンGUI (v1.9.0 新機能)
- **10秒分割 OGG 音声同期システム**: FFmpeg で音声を10秒単位 `.ogg` に自動切り出し、`player.playMusic()` で再生・シーク位置追従。
- **リモコンアイテム (コンパス) GUI**: 右クリックで `ActionFormData` UI を呼び出し。▶再生、⏸一時停止、⏭次、⏮前、🔊音量調整、⏩シーク時間をゲーム内で直感操作。

### 🚀 圧倒的な圧縮率と長尺保存
- **1時間動画あたり 48.24 MB (0.047 GB)** のデータ容量達成（初期バージョン v1.3.4 比 **97.2% 削減**）。
- 1GB (1024MB) のパック制限容量内に **約 21.2 時間分（長編映画 約 10 本分）**、1分ショート動画なら **約 1,280 本** を丸ごと格納可能。

### ⚡ 爆速スルーブットと軽量描画
- **変換速度**: **1,000 〜 4,200 fps**（Pillow Quantize + `ThreadPoolExecutor` 全コアマルチスレッド並列処理）。
- **マイクラ側描画負荷**: 1tick (50ms) あたりわずか **0.48 ms**（負荷率 1% 以下）。`BlockPermutation` 一括キャッシュと単一座標オブジェクト使い回しによりメモリGCスパイクを撲滅。

### 🎨 高精細な色再現性と表現力
- **39色マイクラブロックパレット**: 16色 Concrete + 17色 Terracotta + 6種 自発光ブロック（`sea_lantern`, `glowstone`, `shroomlight`, 3種 `froglight`）。自動パレットはこの中から動画ごとに選択。
- **CIELAB 色空間最適化**: 人間視覚モデル上での色距離 $\Delta E$ 再計算。
- **全5種類ディザリング**: `Floyd-Steinberg`, `Atkinson`, `Burkes`, `Sierra-Lite`, `Ordered (4x4 Bayer Matrix)` を自由選択。

### 🎬 マルチ動画（多タイトル）＆ 遅延ロード
- 1つの `.mcpack` に複数動画を搭載し、GUI及びゲーム内コマンド `/scriptevent <ns>:list` と `{ns}:play <動画ID>` で選択・即時再生可能。
- **遅延ロード (Lazy Load)** により、ワールド起動時の読み込み時間 **0.05 秒（即時起動）**。

---

## 3. リポジトリの主要ファイル構成

```
.
├── convert.py                 # コア変換 CLI (2パス・ストリーミング, GPU/CPU)
├── mvcodec/                   # パレット・減色・RLE/VarInt エンコーダ・Python デコーダ
├── video_player_gui.py        # 複数動画対応 GUI アプリケーション (Tkinter / Treeview)
├── main.js                    # Bedrock Script API 再生スクリプト (VideoPlayer クラス)
├── codec.js                   # Minecraft 非依存のデコーダ (main.js と Node テストで共有)
├── manifest.json              # Behavior Pack マニフェストのテンプレート
├── pack_metadata.py           # リリースバージョン・更新履歴の一元管理
├── build_standalone.py        # スタンドアロン EXE ビルド (PyInstaller)
├── benchmark/                 # 画質・容量ベンチマーク
├── tests/                     # pytest + Node 往復テスト
├── .github/workflows/ci.yml   # CI (テスト + 参考ベンチマーク)
├── PROJECT_STATUS.md          # [本書] 現在のプロジェクト進捗
├── ARCHITECTURE.md            # アーキテクチャ・データ構造・描画仕様
├── DECISIONS.md               # 採否判断マトリクスと技術的根拠
├── TODO.md                    # 今後の開発課題・アイデア
└── BENCHMARK.md               # 性能測定・ベンチマーク詳細結果
```
