# Block Video Player — 統合版(Bedrock)ブロック動画プレイヤー (v2.6.1)

[umbreonben/mc-cushion-bad-apple](https://github.com/umbreonben/mc-cushion-bad-apple) (Java版 datapack) を Minecraft統合版 (Bedrock Edition) の Behavior Pack + Script API 向けに再実装したブロック動画再生アドオン＆変換システムです。

**最新バージョン v2.6.1**: README および GUI の表示を整理し、ディザリングの仕様（GPU対応/非対応）を分かりやすく記載しました。

---

## 🚀 主要機能 (v2.6.1)

- ⚡ **GPU (PyTorch/CUDA) 変換対応**:
  - `convert.py` は PyTorch を用いた GPU テンソル計算に対応しています。
  - **重要**: GPU を使用して変換を高速化する場合は、ディザリング設定で「なし」または「Ordered (Bayer)」を選択してください。「Floyd-Steinberg」などの誤差拡散型ディザリングはアルゴリズムの都合上並列処理ができないため、CPU 処理に自動的にフォールバックします。
  - VRAM の容量不足 (OOM) を防ぐため、内部でミニバッチ処理とキャッシュ解放を行っています。
- 🎵 **OGG 音声再生の同期**:
  - 音声データ (Resource Pack) と スクリプト (Behavior Pack) を分離した `.mcaddon` 自動構築に対応。インポートするだけで、マイクラ内で音声同期再生が可能になります。
- ⚡ **高画質モード (128×128) 用の描画調整**:
  - 1tick (50ms) あたりのブロック更新数を最大 800 個に制限し、マイクラ側の処理負荷を抑えています。
- 📦 **単体実行ファイル (EXE) 対応**:
  - `python build_standalone.py` を実行すると、Python などの環境構築が不要な単体実行ファイル (`BlockVideoPlayer.exe`) を生成できます。
- 👑 **マイクラ実在ブロックによる色再現**:
  - コンクリート、テラコッタ、自発光ブロック、羊毛、木材、コンクリート粉、鉱石・石材など、全112個のブロックを利用したカラーパレット (`ultra_110`) を採用しています。
- 🎮 **ゲーム内リモコン GUI**:
  - プレイヤーが「コンパス (`minecraft:compass`)」を手に持ち右クリックすると、操作リモコンUIが開きます（再生、一時停止、停止、次の動画、音量調整、シーク）。
- 🎬 **マルチ動画対応**:
  - 1つの `.mcaddon` パックに複数の動画を格納し、ゲーム内で切り替えて再生できます。

---

## 🎮 使い方

### 1. 動画から `.mcaddon` の作成 (GUI)

```bash
python video_player_gui.py
```
（またはビルドされた `BlockVideoPlayer.exe` をダブルクリック）

1. **「動画を追加」** ボタンで動画ファイル (MP4 / MKV / AVI / WEBM 等) を追加します。複数追加可能です。
2. 画質プリセット（軽量 64x64, 高画質 128x128 など）や再生間隔 (tick) を設定します。
3. パレットで **「ウルトラ全110色」** などを選択します。
4. ディザリング手法を選択します。**GPU を活用したい場合は「なし」または「Ordered (Bayer)」を選択してください**。
5. 出力先（`.mcaddon`）を指定し、**「ビルド開始」** をクリックすると、アドオンが出力されます。

### 2. Minecraft への導入 ＆ 操作

1. 出力された `.mcaddon` をダブルクリックして Minecraft 統合版へインポートします。
2. ワールドの設定で **Behavior Pack (ビヘイビアパック)** と **Resource Pack (リソースパック)** を有効化します。
3. **【重要】** 同じくワールドの設定画面から **「実験」** の項目を開き、**「ベータ API (Beta APIs)」** のトグルを必ず **オン** にしてワールドを起動してください（これがオフだと動画が再生されません）。
4. **ゲーム内リモコン操作**:
   - インベントリから **「コンパス (`minecraft:compass`)」** を手に持ち右クリックするとリモコン画面が開きます。
   - **再生 / 一時停止 / 停止**: 映像と音声の制御。
   - **次の動画 / 前の動画**: 再生タイトルの切替。
   - **音量設定**: 音量スライダーでの調整。
   - **シーク**: 指定した時間へのジャンプ。
4. **コマンド操作 (`/scriptevent`)**:
   - `/scriptevent badapple:setup` … プレイヤーの足元を起点に原点を保存
   - `/scriptevent badapple:start` … 再生開始
   - `/scriptevent badapple:stop` … 停止・盤面クリア
   - `/scriptevent badapple:list` … 収録動画一覧を表示
   - `/scriptevent badapple:play <動画ID>` … 指定動画を選択再生
   - `/scriptevent badapple:gui` … リモコンGUIを開く

---

## 📂 プロジェクトの主要ファイル

```
.
├── convert.py                 # コア変換スクリプト
├── video_player_gui.py        # 複数動画対応 GUI アプリケーション
├── main.js                    # Bedrock Script API 再生スクリプト
├── manifest.json              # マニフェストファイル (生成ベース用)
├── pack_metadata.py           # バージョン・リリースノート管理
└── README.md                  # このドキュメント
```

---

## 📜 バージョン履歴

- **v2.6.1**: READMEとGUIのテキストを整理。事実ベースの説明へ改修し、ディザリング時のGPU対応状況を明記。
- **v2.6.0**: Ordered (Bayer) ディザリング時の GPU テンソル並列計算と、VRAM パンクを防ぐミニバッチ・キャッシュ解放 (OOM 対策) を実装。
- **v2.5.0**: 音声再生の完全対応。パック構造を Behavior Pack (BP) と Resource Pack (RP) に分離し、`.mcaddon` 形式で出力するようアーキテクチャを刷新。
- **v2.4.1**: 差分デコード中断によるブロック座標崩壊バグを解消。
- **v2.4.0**: 高画質 (128×128) 時の 1tick ブロック更新上限 (800) と、`playSound` / 44.1kHz OGG 抽出による音声再生修復を実装。
- **v2.3.1**: GUIクラス構造 (`_build_ui`) の修復と安定化。
- **v2.3.0**: キーフレーム (I/P Frame, GOP=30) 方式を導入。シーク復元に対応。
- **v2.2.0**: PyInstaller によるスタンドアロン EXE ビルド環境 (`build_standalone.py`) 追加。
- **v2.1.0**: GPU (PyTorch / CUDA) アクセラレーション統合。
- **v2.0.0**: 全110色のウルトラパレット、10秒単位OGG音声同期システム、コンパスによるゲーム内リモコンGUIを完全統合。
