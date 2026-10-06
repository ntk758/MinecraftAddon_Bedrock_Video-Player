# Minecraft Bedrock Video Player

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.12](https://img.shields.io/badge/Python-3.12-blue.svg)](https://www.python.org/)
[![Minecraft Bedrock](https://img.shields.io/badge/Minecraft-Bedrock-brightgreen.svg)](https://www.minecraft.net/)
[![Release v5.4.0](https://img.shields.io/badge/Release-v5.4.0-blue.svg)](#-releases)
[![CI](https://github.com/ntk758/MinecraftAddon_Bedrock_Video-Player/actions/workflows/ci.yml/badge.svg)](https://github.com/ntk758/MinecraftAddon_Bedrock_Video-Player/actions/workflows/ci.yml)
[![AI Generated](https://img.shields.io/badge/Made%20by-AI-blueviolet.svg)](#)

<p align="center">
  <img src="docs/images/demo.gif" alt="左: 元映像 / 右: 128×72 ブロックに変換した映像 (Left: source video, right: converted to 128×72 Minecraft blocks)" width="800">
  <br>
  <sub>左: 元映像 / 右: 128×72 ブロックに変換した映像 (ゲーム内と同じ規則で復元した画面) — Left: source / Right: converted to blocks</sub>
</p>

**▶ すぐ試す / Try it now:** [リリースページ](https://github.com/ntk758/MinecraftAddon_Bedrock_Video-Player/releases/latest) から `BlockVideoPlayer-sample-v5.4.0.mcaddon` (サンプル映像入り) をダウンロードしてインポートするだけで再生できます。自分の動画を変換するときは Windows 用の `BlockVideoPlayer-v5.4.0-windows-x64.zip` (Python 不要) を使ってください。
Download the sample add-on or the Windows app (no Python needed) from the [latest release](https://github.com/ntk758/MinecraftAddon_Bedrock_Video-Player/releases/latest).

[日本語](#日本語-japanese) | [English](#english)

## 日本語 (Japanese)

### 📖 概要 (Overview)
> **⚠️ 重要事項 (Important Note):**
> このプロジェクトは、コアアルゴリズム設計からPythonスクリプト、Bedrock Script API、GUIアプリケーション、最適化ロジック、そしてこのREADMEに至るまで、**すべて AI (Claude Opus / GPT / Gemini Pro等のエージェント) によって自律的に設計・プログラミング・構築されたものです**。

Minecraft Bedrock Edition（統合版）で、MP4 などの動画ファイルを「マイクラのブロック」へ変換し、ゲーム内で映像と音声を同期再生できるアドオン構築ツールです。GUI で動画を選んでボタンを押すだけで、そのままインポートできる `.mcaddon` を作成します。

### ✨ 特徴 (Features)
- 🎬 **MP4 等の動画から直接ブロック動画アドオンを生成** (映像は Behavior Pack、音声は Resource Pack)
- 🎵 **音声の同期再生** (10 秒単位の OGG に分割し、再生位置に合わせて切り替え)
- 🎮 **ゲーム内リモコン** (コンパスを使うと再生・一時停止・シーク・音量・動画切替の画面が開く)
- 📚 **マルチ動画対応** (1 つのアドオンに複数の動画を収録)
- 👑 **マイクラ実在ブロック 39 色パレット** (コンクリート・テラコッタ・発光ブロック) と、動画ごとに色を選ぶ自動パレット
- 🗜 **予測圧縮 (Phase 7.x)**: 変化したブロックだけを送る差分に、**動き補償** (平面は 8×8 ブロック、立体は 8×8×8 ブリック)・**タイル辞書** (一度出た模様を番号で再利用)・ランの位置の間隔符号化を組み合わせ、フレームごとに一番小さい方法を自動で選択。v5.4.0 よりデータ量が **47〜88% 減** ([計測結果](#-ベンチマーク-phase-7x))。シーンごとのキーフレームで高速シーク
- 🧊 **3D ホログラム再生**: 映像を立体 (明るさを奥行きにした **レリーフ**・明るい部分の **押し出し**・丸く **ふくらませた** 立体) にして再生。MagicaVoxel (`.vox`) の立体アニメもそのまま再生
- 🧱 **まとめ置き描画**: 同じ色の変化を箱 (`fillBlocks`) や色ごとの座標リストにまとめて置き、API 呼び出しを 1 フレーム数百〜千回 → **数回〜数百回** に。キーフレームでも変わったブロックだけを置き直す。実機で置き方の速さを計る `bench` コマンド付き
- ⚡ **GPU 変換対応**: NVIDIA (CUDA)・AMD (ROCm / DirectML)・Intel (DirectML)。GPU が無くても CPU で変換可能
- 🌐 **10 言語対応**: 日本語・English・简体中文・繁體中文・한국어・Español・Português (Brasil)・Français・Deutsch・Русский。GUI は画面右上で切り替え、ゲーム内のメッセージとリモコン画面は各プレイヤーの Minecraft の言語設定で表示

### 💻 必要な環境 (Requirements)
| 項目 | 内容 |
|---|---|
| OS | Windows 10 / 11 (変換ツール)。Linux でもコマンドラインで変換可能 |
| Minecraft | Bedrock Edition 最新版 (安定版 Script API `@minecraft/server` 1.19.0 / `@minecraft/server-ui` 1.2.0 を使用。**ベータ API は不要**) |
| Python | **3.12** (3.13 でも CPU 変換は動きますが、AMD GPU 用の DirectML は 3.12 までの対応です) |
| FFmpeg | PATH に通すか、`video_player_gui.py` と同じフォルダに `ffmpeg.exe` を置く |
| GPU (任意) | NVIDIA / AMD / Intel。PyTorch の追加が必要 ([GPU の設定](#4-gpu-の設定-任意)) |

### 🚀 インストールと使い方 (Installation & Usage)

#### 1. Python のインストール
- **Microsoft Store**: [Python 3.12 をインストール](https://apps.microsoft.com/detail/9ncvdn91xzqp) (推奨・最も簡単です)
- **公式サイト**: [Python.org](https://www.python.org/downloads/windows/) から 3.12 のインストーラーをダウンロード (「Add Python to PATH」に必ずチェック)

#### 2. FFmpeg のインストール
- **winget を使う場合**: コマンドプロンプトまたは PowerShell で次を実行し、ターミナルを開き直します。
  ```bash
  winget install Gyan.FFmpeg
  ```
- **手動インストール**: [FFmpeg 公式サイト](https://ffmpeg.org/download.html) から Windows 向けビルド (gyan.dev 等) を入手し、`ffmpeg.exe` をこのツールのフォルダに置くか PATH に追加します。

#### 3. ツールのセットアップ
このリポジトリをダウンロード (ZIP を解凍、または `git clone`) し、フォルダ内で必要なパッケージをインストールします。
```bash
pip install -r requirements.txt
```

#### 4. GPU の設定 (任意)
GPU を使わない場合はこの手順は不要です。

| GPU | OS | インストール方法 |
|---|---|---|
| NVIDIA | Windows / Linux | [PyTorch 公式サイト](https://pytorch.org/get-started/locally/) の CUDA 版 `torch` |
| AMD Radeon | Windows | `pip install -r requirements-directml.txt` (DirectML) または AMD 公式の ROCm 版 PyTorch for Windows (対応 GPU は AMD のドキュメントを参照) |
| AMD Radeon | Linux | [PyTorch 公式サイト](https://pytorch.org/get-started/locally/) の ROCm 版 `torch` |
| Intel Arc / 内蔵 GPU | Windows | `pip install -r requirements-directml.txt` (DirectML) |

- GUI の「変換デバイス」が **自動** なら **CUDA / ROCm → DirectML → CPU** の順に使えるものを選びます。DirectML では内蔵 GPU より単体 GPU を優先します。
- 変換前に GPU で小さなテスト変換を行い、失敗した場合は自動で CPU 変換に切り替えます。
- 実際に使われたデバイスは処理ログの `[MVCodec] 減色デバイス: AMD Radeon RX 9070 XT (directml)` (英語表示では `Conversion device:`) のような行で確認できます。
- GPU 経路は OkLab 色空間での色合わせ・知覚的なレート歪み最適化 (RDO)・時間方向ディザを行うため、CPU 経路 (Pillow) とは仕上がりが異なり、容量は小さめになります。処理が重い分、**変換全体が CPU より速くなるとは限りません** (例: RX 9070 XT + DirectML、256×256 で減色処理は約 200 fps)。
- GPU で処理できるディザは「なし / Blue Noise / Ordered」です。それ以外のディザを選ぶと CPU で処理されます。

#### 5. GUI で .mcaddon を作成
```bash
python video_player_gui.py
```
画面右上の **🌐 言語 (Language)** で表示言語を切り替えられます。初回は Windows の表示言語に合わせて選ばれ、選んだ言語は次回以降も使われます (入力中の内容はそのまま引き継がれます)。

1. **動画リスト**: 「動画を追加」で動画を選びます (複数可)。「ID編集」で動画 ID (英小文字・数字・`_`)、「サムネ秒編集」でアイコンに使う場面を変更できます。
2. **出力 .mcaddon**: 保存先を指定します。
3. **パック名・実行コマンド**: パック表示名と、ゲーム内コマンドの接頭辞 (既定 `badapple`) を設定します。
4. **変換・再生設定**:

   | 設定 | 説明 |
   |---|---|
   | 画質プリセット | 幅・高さ・再生間隔をまとめて設定します (下表) |
   | 幅 / 高さ | スクリーンのブロック数 (最大 512) |
   | 再生間隔 (tick) | 1 フレームを表示する tick 数。20 ÷ 間隔 = fps |
   | キーフレーム間隔 | 何フレームごとに全画面を描き直すか (シークの速さに影響)。0 ならシーンの先頭のみ |
   | パレット | 自動 (動画ごとに最適化) / 全 39 色 / 拡張 33 色 / 基本 16 色 |
   | ディザリング | なし / Blue Noise / Ordered (GPU 対応)、Floyd-Steinberg / Atkinson / Burkes / Sierra Lite (CPU のみ) |
   | 知覚最適化 | 輪郭部分だけにディザをかけて、平坦部のちらつきを抑えます |
   | 変換デバイス | 自動 / NVIDIA CUDA・AMD ROCm / DirectML / CPU のみ |

   | 画質プリセット | 解像度 | fps | 目安 |
   |---|---|---|---|
   | 軽量 | 64×64 | 20 | 動きの多い動画・低スペック向け |
   | 標準 | 96×96 | 10 | |
   | 高画質 (既定) | 128×128 | 10 | |
   | 高精細 | 128×128 | 5 | |
   | ウルトラ | 256×256 | 5 | 重い。PC 版推奨 |
   | 極限 | 512×512 | 2 | 非常に重い。PC 版推奨 |
5. **「.mcaddon を作成」** を押すと、処理ログに進行状況が表示され、完了するとダイアログが出ます。

#### 6. Minecraft で再生
1. 作成した `.mcaddon` をダブルクリックしてインポートします。
2. ワールドの設定で **ビヘイビアーパック** と **リソースパック** の両方を有効にします (同じパック名で 2 つあります)。`/scriptevent` を使うため **チートを有効** にしてください。
3. スクリーンを置きたい場所に立ち、次のコマンドを実行します (`badapple` は GUI で設定した接頭辞)。
   ```
   /scriptevent badapple:setup
   ```
   スクリーンは **プレイヤーの足元の高さに、東 (+X) と北 (-Z) 方向へ水平に** 作られます。範囲内のブロックは上書きされるので、平らで何もない場所で実行し、上空から見下ろしてください。
4. **コンパスを使う** (右クリック / 長押し) とリモコンが開きます。

| リモコンのボタン | 動作 |
|---|---|
| ▶ 再生 / 再開・⏸ 一時停止 | 一時停止やシークした位置から再開します |
| ⏹ 停止 ＆ クリア | 再生を止め、スクリーンを単色 (パレットの先頭のブロック) で塗りつぶします |
| ⏭ 次の動画・⏮ 前の動画・📜 動画リスト | 再生する動画を切り替えます |
| 🔊 音量設定 | 0〜100% |
| ⏩ シーク | 秒数を指定して移動します (移動後は一時停止) |

| コマンド | 動作 |
|---|---|
| `/scriptevent badapple:setup` | 現在地にスクリーンを設置 |
| `/scriptevent badapple:start` | 選択中の動画を最初から再生 |
| `/scriptevent badapple:list` | 収録動画の一覧 |
| `/scriptevent badapple:play <動画ID>` | 指定した動画を再生 |
| `/scriptevent badapple:gui` | リモコン画面を開く |
| `/scriptevent badapple:stop` | 停止してスクリーンを単色で塗りつぶす |
| `/scriptevent badapple:render rect\|list\|block` | 描画方式を切り替える (既定 `rect`。下の「描画方式」を参照) |
| `/scriptevent badapple:bench` | スクリーンの範囲で 6 通りの置き方の速さを計り、1 ブロックあたりの時間をチャットに表示 |

**描画方式**: `rect` は同じ色の変化を箱にまとめて `fillBlocks` で置き (既定)、`list` は色ごとの座標リストを `fillBlocks(ListBlockVolume)` で置き、`block` は 1 個ずつ置きます。`list` は呼び出し回数が最も少なく (1 フレーム数回〜数十回)、細かい模様や立体で有利です。どれが速いかは端末によって違うので、`bench` の結果 (µs/ブロック) を見て選んでください。選んだ方式はワールドに保存されます。

#### 7. 3D ホログラム再生
<p align="center">
  <img src="docs/images/hologram_inflate.gif" alt="白黒のシルエットを丸くふくらませた立体 (inflate)" width="380">
  <img src="docs/images/hologram_relief.gif" alt="色の明るさを奥行きにした立体レリーフ (relief)" width="420">
  <br>
  <sub>左: シルエットを丸くふくらませた立体 (<code>inflate</code>, 64×48×12) / 右: 明るさを奥行きにした立体レリーフ (<code>relief</code>, 64×48×16)。<br>どちらも変換したデータをゲーム内と同じ規則で復元し、斜め上から描いたもの (<code>scripts/preview_volume.py</code>)</sub>
</p>

GUI の **表示モード** で立体の作り方を選び、**奥行き (ブロック)** を指定して作成します。`.vox` を動画リストに追加すると MagicaVoxel の立体アニメになります (サムネイルは立体を斜め上から見た絵)。

| 表示モード | 内容 | 向いている映像 |
|---|---|---|
| 平面スクリーン | 従来どおり水平な画面 | すべて |
| 立体レリーフ (`relief`) | 明るいほど手前に飛び出す浮き彫り。表面と段差の側面だけを置く | 風景・CG・グラデーションの多い映像 |
| 立体シルエット (`extrude`) | 明るい部分を前後に押し出した板 | Bad Apple のような白黒映像 |
| 立体ふくらみ (`inflate`) | 明るい部分を、縁が丸く中央が厚い立体にふくらませる | 白黒のシルエット・ロゴ |
| MagicaVoxel (`vox`) | `.vox` の各モデル (またはアニメーションのキーフレーム) を 1 フレームとして再生 | ボクセルアート |

- `/scriptevent badapple:setup` を実行すると、立体は **プレイヤーの 2 ブロック北に、左右中央をそろえて足元から上へ** 立ちます。建築できる高さ (オーバーワールドは Y=319 まで) からはみ出す場合は下へずらしてお知らせします。南側 (プレイヤー側) から見るのが正面です。
- 奥行き・明るさの判定は減色前の元の映像の明るさを使うので、ディザをかけても表面が荒れません。`relief` は明るさが少し揺れただけでは奥行きを変えないので (ヒステリシス)、ちらつきと置き直しが減ります。
- 立体は `list` 描画方式がとくに有利です (下の計測では 1 フレーム 2〜21 回の呼び出し)。
- 変換結果は Minecraft を起動しなくても確認できます:
  ```bash
  python scripts/preview_volume.py out.js --output preview.gif --source-frames frames_dir
  ```

コマンドラインでの例:
```bash
# 明るさを奥行き 16 段にした立体レリーフ
python convert.py --input-video in.mp4 --output out.js --width 64 --height 48 --fps 10 --mode relief --depth 16
# 白黒映像の白い部分を丸くふくらませる (黒い部分なら --extrude-invert)
python convert.py --input-video badapple.mp4 --output out.js --width 64 --height 48 --mode inflate --depth 12
# MagicaVoxel の立体アニメ (各モデルが 1 フレーム、または .vox が並んだフォルダ)
python convert.py --input-vox anim.vox --output out.js --fps 10
```

#### スタンドアロン EXE (任意)
ビルド済みの EXE は [リリースページ](https://github.com/ntk758/MinecraftAddon_Bedrock_Video-Player/releases/latest) からダウンロードできます (DirectML 同梱)。自分でビルドする場合は PyInstaller を使います。DirectML (torch-directml) がビルド環境に入っていれば同梱されます。
```bash
pip install pyinstaller
python build_standalone.py
```
`dist/BlockVideoPlayer/BlockVideoPlayer.exe` ができます。同じフォルダに `ffmpeg.exe` を置くと PATH の設定が不要になります。

---

## English

### 📖 Overview
> **⚠️ Note:** This entire project—including the Python scripts, Minecraft add-on JavaScript, GUI application, and this README—was **fully designed, programmed, and generated by AI**.

A tool that converts video files (MP4, etc.) into Minecraft blocks and builds a `.mcaddon` that plays them in Minecraft Bedrock Edition with synchronized audio. Pick your videos in the GUI, press one button, and import the result.

### ✨ Features
- 🎬 **Video → block add-on** (video in the Behavior Pack, audio in the Resource Pack)
- 🎵 **Synchronized audio** (10-second OGG chunks switched by playback position)
- 🎮 **In-game remote control** (use a compass: play/pause, seek, volume, switch videos)
- 📚 **Multiple videos per add-on**
- 👑 **39-color palette of real blocks** (concrete, terracotta, light sources) plus a per-video automatic palette
- 🗜 **Predictive compression (Phase 7.x)**: on top of sending only changed blocks, each frame picks the smallest of **motion compensation** (8×8 blocks on screens, 8×8×8 bricks in 3D), a **tile dictionary** (patterns seen before are reused by number) and gap-coded run positions — **47–88% less data** than v5.4.0 ([benchmarks](#-ベンチマーク-phase-7x)); per-scene keyframes for fast seeking
- 🧊 **3D hologram playback**: turn a video into a volume (brightness-as-depth **relief**, **extruded** or rounded **inflated** silhouettes) or play MagicaVoxel (`.vox`) animations directly
- 🧱 **Batched placement**: same-colour changes are placed as boxes (`fillBlocks`) or per-colour position lists, cutting API calls from hundreds–thousands per frame to **a few–a few hundred**; keyframes only re-place blocks that changed. `/scriptevent <prefix>:bench` measures the placement methods on your device
- ⚡ **GPU conversion** on NVIDIA (CUDA), AMD (ROCm / DirectML) and Intel (DirectML); CPU works too
- 🌐 **10 languages**: Japanese, English, Simplified/Traditional Chinese, Korean, Spanish, Portuguese (Brazil), French, German, Russian — in the GUI (selector at the top right) and in game (chat messages and the remote follow each player's Minecraft language)

### 💻 Requirements
- **OS**: Windows 10 / 11 for the GUI (the CLI also runs on Linux)
- **Minecraft**: latest Bedrock Edition (stable Script API `@minecraft/server` 1.19.0 / `@minecraft/server-ui` 1.2.0 — **Beta APIs are not required**)
- **Python**: 3.12 (3.13 works for CPU conversion, but DirectML supports up to 3.12)
- **FFmpeg**: on PATH or next to `video_player_gui.py`

### 🚀 Usage
1. `pip install -r requirements.txt`
2. Optional GPU: CUDA build of PyTorch (NVIDIA), ROCm build (AMD on Linux, or AMD's PyTorch for Windows), or `pip install -r requirements-directml.txt` (AMD / Intel on Windows). With **Device = Auto** the converter tries CUDA/ROCm → DirectML → CPU, runs a small self-test on the GPU and falls back to CPU if it fails. The log line `[MVCodec] Conversion device:` shows the device in use.
3. Run `python video_player_gui.py` (switch the UI language with **🌐 Language** at the top right — it follows your Windows display language by default), add videos, choose a quality preset / palette / dithering / device, and press **".mcaddon を作成"** (Create .mcaddon).
4. Import the `.mcaddon`, enable both the Behavior Pack and the Resource Pack, and turn on cheats (for `/scriptevent`).
5. Stand where the screen should be and run `/scriptevent badapple:setup` (`badapple` is the command prefix set in the GUI). The screen is built horizontally at your foot level, extending east (+X) and north (−Z); blocks in that area are overwritten.
6. Use a compass to open the remote control, or use `/scriptevent badapple:start | list | play <id> | gui | stop`.
7. **3D holograms**: choose a **Display mode** (relief / extrude / inflate) and a **Depth** in the GUI, or add a `.vox` file. `setup` builds the volume 2 blocks north of you, centred, from your feet upward (moved down if it would exceed the build height). Switch placement with `/scriptevent badapple:render rect|list|block` and compare them with `/scriptevent badapple:bench` (`list` uses the fewest calls, especially for 3D). Preview any converted file without Minecraft: `python scripts/preview_volume.py out.js --output preview.gif`.

A prebuilt Windows EXE (with DirectML) and a ready-to-play sample add-on are on the [latest release](https://github.com/ntk758/MinecraftAddon_Bedrock_Video-Player/releases/latest). To build the EXE yourself: `pip install pyinstaller` then `python build_standalone.py`.

---

## 📂 アーキテクチャとディレクトリ構成 (Architecture)

```text
.
├── video_player_gui.py        # GUI & pack builder (.mcaddon generator)
├── gui_i18n.py                # GUI translations loader, language detection, saved settings
├── locales/                   # Translations for the GUI, in-game texts (addon.*) and converter logs (convert.*); en.json is the reference
├── convert.py                 # Video → block data converter CLI (2-pass streaming, GPU/CPU)
├── mvcodec/                   # Codec library
│   ├── color.py               #   Palettes, dithering, OkLab
│   ├── auto_palette.py        #   Per-scene automatic palette (K-Means)
│   ├── device.py              #   GPU backend detection (CUDA / ROCm / DirectML)
│   ├── encode.py              #   RLE + VarInt encoder (gap-coded runs), 15-bit string packing
│   ├── motion.py              #   Motion estimation / compensation (2D blocks, 3D bricks)
│   ├── tiles.py               #   Implicit tile dictionary shared by encoder and decoders
│   ├── stream.py              #   Per-frame choice of delta / motion / tiles, GOP chunks and index
│   ├── volume.py              #   3D volumes: relief / extrude / inflate, MagicaVoxel .vox, isometric preview
│   └── decode.py              #   Python reference decoder (benchmark / tests)
├── main.js                    # In-game player (Bedrock Script API)
├── codec.js                   # Minecraft-independent decoder shared by main.js and Node tests
├── manifest.json              # Behavior Pack manifest template
├── pack_metadata.py           # Version and release notes
├── build_standalone.py        # PyInstaller build for the standalone EXE
├── benchmark/                 # Quality benchmark (PSNR, SSIM, ΔE2000, LPIPS) and codec_bench.py (size / API calls vs another revision)
├── scripts/                   # Utilities (demo GIF generator, preview_volume.py for 3D previews)
├── docs/images/               # README demo GIF and social preview image
├── tests/                     # pytest + Node round-trip tests
└── requirements*.txt          # Runtime / dev / benchmark / DirectML dependencies
```

データ形式やゲーム内の描画方式の詳細は [ARCHITECTURE.md](ARCHITECTURE.md) を参照してください。

## 🛠 技術スタック (Tech Stack)
- **Python 3.12**: 変換処理と GUI (Tkinter)
- **NumPy / Pillow**: 減色・ディザ (CPU)
- **PyTorch**: GPU 減色 (CUDA / ROCm / DirectML)
- **FFmpeg**: フレーム抽出・OGG 音声分割
- **Minecraft Script API**: `fillBlocks` (`BlockVolume` / `ListBlockVolume`) と `setBlockPermutation` によるブロック描画、`@minecraft/server-ui` のリモコン画面

## 🧪 開発者向け (Development)
```bash
pip install -r requirements-dev.txt
pytest -q
```
- `tests/test_codec_v4.py` は変換結果を Python デコーダと `codec.js` (Node.js が必要) の両方で復元し、一致・シーク・パレット切替を検証します。
- `tests/test_motion.py` / `test_tiles.py` / `test_volume.py` / `test_volume3d.py` は動き補償・タイル辞書・立体 (3D 動き補償・inflate・ヒステリシス) を、Python と `codec.js` の一致・シークを含めて検証します。
- `tests/test_main_js.py` / `test_render_modes.py` は `main.js` をモックの Minecraft で動かし、置いたブロックが正しいこと、描画方式による違いが無いこと、変わったブロックだけを置くこと、3D の箱でまとめること、建築限界内に収めることを検証します。
- `tests/test_device.py` は GPU の選択と CPU へのフォールバックを検証します (PyTorch があれば GPU 用の処理も CPU で実行して確認)。
- GitHub Actions (`.github/workflows/ci.yml`) で push / PR ごとにテストと参考ベンチマークを実行します。

翻訳を追加・修正するには `locales/` の JSON を編集します。1 つのファイルに GUI (通常のキー)・ゲーム内の表示 (`addon.*`、引数は `%1` `%2`)・変換器のログ (`convert.*`) がまとまっています。
- 新しい言語は `en.json` をコピーして `<言語コード>.json` を作ると GUI の選択肢に現れます。ゲーム内でも使うには `gui_i18n.py` の `MINECRAFT_LOCALES` に Minecraft の言語コード (例: `it_IT`) を追加してください。
- ゲーム内の文字列は、GUI でビルドするときにリソースパックの `texts/*.lang` に書き出されます。翻訳が無い言語の Minecraft では英語 (`en_US`) で表示されます。
- `tests/test_i18n.py` がキーとプレースホルダーの過不足を、`tests/test_main_js.py` が `main.js` をモック環境で動かして、表示がすべて翻訳済みかを検査します。翻訳の改善 PR も歓迎です。

コマンドラインでの変換:
```bash
python convert.py --input-video in.mp4 --output out.js --width 128 --height 128 --fps 10 --palette auto --device auto
```

| 主なオプション | 説明 |
|---|---|
| `--input-video` / `--frames-dir` | 入力動画、または画像連番フォルダ |
| `--width` `--height` `--fps` `--duration` | 解像度・fps (最大 20)・変換する秒数 |
| `--palette` | `concrete` / `expanded` / `full` / `auto` |
| `--dither-method` | `none` / `blue_noise` / `ordered` / `floyd` / `atkinson` / `burkes` / `sierra` |
| `--device` | `auto` / `cuda` / `rocm` / `directml` / `cpu` |
| `--keyframe-interval` | キーフレーム間隔 (0 でシーン先頭のみ) |
| `--no-adaptive-fps` / `--no-perceptual` | 変化の少ないフレームの省略・知覚最適化を無効化 |
| `--no-motion` / `--mv-range` | 動き補償を無効化 / 局所探索の範囲 (既定 4、大きいほど速い動きに強いが変換が遅い。立体は ±2 固定) |
| `--tile-size` | タイル辞書のタイルの大きさ `auto` (既定: 4 と 8 の小さい方) / `4` / `8` / `16` / `0` (無効) |
| `--mode` | `screen` (既定) / `relief` / `extrude` / `inflate` / `vox` |
| `--input-vox` | MagicaVoxel の `.vox` (または `.vox` が並んだフォルダ)。`--mode vox` |
| `--depth` | 立体の奥行き (2〜128 ブロック、既定 16) |
| `--depth-video` | `relief` の奥行きに使うグレースケール動画 (明るいほど手前) |
| `--depth-hysteresis` | `relief` で奥行きを変えない揺れの幅 (段数、既定 1、0 で無効) |
| `--extrude-threshold` / `--extrude-invert` | `extrude` / `inflate` で立体にする明るさのしきい値 / 暗い部分を立体にする |
| `--thickness` | `extrude` の厚み (既定は奥行き全体) |
| `--hollow` | 外から見えない内部を置かない (既定は無効。`block` 描画方式で置く数を減らしたいとき用) |
| `--ffmpeg` | ffmpeg のパス |

画質ベンチマーク:
```bash
pip install -r requirements-benchmark.txt
python benchmark/run.py --video in.mp4 --output out.js --fps 10
```

データ量と描画コストのベンチマーク (合成映像を作り、別の版と比べる。node と ffmpeg を使用):
```bash
python benchmark/codec_bench.py --out bench_out                  # 平面スクリーン (main と比較)
python benchmark/codec_bench.py --out bench_out --suite volume   # 立体
```

## 📊 ベンチマーク (Phase 7.x)

著作権の問題がない合成映像 5 種 (パン・アニメ風・白黒シルエット・ffmpeg の `testsrc2` / `mandelbrot`、各 60 フレーム、10 fps、39 色パレット) を `benchmark/codec_bench.py` で変換し、v5.4.0 (main) と比べました。描画コストは、モックの Minecraft で `main.js` を再生して数えた 1 フレームあたりの API 呼び出し回数です。

**データ量 (出力 .js、128×72) — v5.4.0 比**

| 映像 | ディザなし | Blue Noise | 呼び出し/フレーム (ディザなし): v5.4.0 → `rect` / `list` |
|---|---|---|---|
| パン (風景) | **−88%** (237 → 28 KB) | −43% | 1190 → 402 / **5.0** |
| アニメ風 (字幕・まばたき) | **−77%** (75 → 17 KB) | −64% | 513 → 107 / **8.8** |
| 白黒シルエット | **−75%** (73 → 18 KB) | −67% | 623 → 94 / **2.1** |
| testsrc2 | −62% | −58% | 1282 → 188 / **19.4** |
| mandelbrot | −47% | −57% | 762 → 424 / **23.3** |

- 256×144 でも −54〜88% (`.mcaddon` と同じ deflate 圧縮後は −58〜94%)。v5.4.0 は 100 チャンクを超える範囲の常時読み込みを登録できず、256×144 以上ではスクリーンを設置できませんでした (修正済み)。
- 置くブロックの数も減ります (キーフレームで全ブロックを置き直さない): アニメ風 513 → 365 / フレーム。
- Blue Noise は模様が画面に固定されるため動き補償が効きにくく、主な効果はラン位置の符号化とディザの決定化 (v5.4.0 はスレッド間で乱数を共有していたため、静止部分でもフレームごとに模様が変わっていた) によるものです。

**3D ホログラム (64×48×16、60 フレーム) — 3D 対応前 (2D 動き補償・中空・奥行きごとの長方形) との比較**

| 映像 / モード | データ量 | 呼び出し/フレーム `rect` | `list` |
|---|---|---|---|
| シルエット / `extrude` | 296 → 170 KB (**−42%**) | 1868 → **67** (−96%) | 2.4 |
| シルエット / `inflate` (新規) | 156 KB (動き補償なしなら 250 KB) | 743 | 2.3 |
| mandelbrot / `relief` | 195 → 167 KB (−15%) | 801 → 397 (−50%) | 21 |
| testsrc2 / `relief` | 25 → 26 KB | 47 → 22 (−54%) | 3.5 |

- 3D 動き補償: 12×12×12 の模様付きの箱が 1 マスずつ動く .vox では、差分フレーム 1 枚あたり **奥へ 108 → 23 B (−79%)**、**横 / 上下 180 → 23 B (−87%)** (2D の動き補償は奥行き方向の動きを予測できない)。
- `relief` の奥行きのヒステリシス (既定 1 段) は mandelbrot で データ −11%・呼び出し −19%、Blue Noise のパンで データ −34%。ディザなしのパンだけはデータが増えますが (23 → 41 KB)、置くブロックは −27%・呼び出しは −45% です。

設計・先行事例・計測の詳細は [docs/research/phase7x.md](docs/research/phase7x.md) を参照してください。

## ❓ よくある質問 (FAQ)

**Q. Java版で使えますか？ (Does this work on Java Edition?)**
A. いいえ、統合版 (Bedrock Edition) 専用です。

**Q. スマホやスイッチでも動きますか？ (Will it run on mobile/consoles?)**
A. `.mcaddon` 自体はどの端末の統合版でも動作するはずですが、高画質設定は非常に重いため PC 版を推奨します。

**Q. GPU があるのに CPU で変換されます。**
A. 処理ログの `[MVCodec]` の行を確認してください。PyTorch (または torch-directml) が入っていない場合や、GPU のテスト変換に失敗した場合は CPU になります。DirectML は Python 3.13 に対応していないため、Python 3.12 の環境に `requirements-directml.txt` を入れてください。Floyd-Steinberg などの CPU 専用ディザを選んでいる場合も CPU で処理されます。

**Q. 「ffmpeg が見つかりません」と表示されます。**
A. `ffmpeg -version` がターミナルで動くか確認してください。動かない場合は `ffmpeg.exe` をツール (または EXE) と同じフォルダに置いてください。

**Q. 再生が重い・カクつきます。**
A. 解像度を下げる (軽量・標準プリセット) か、fps を下げてください。1 tick あたりのブロック設置数には上限があり、超えた分は次の tick に持ち越されます。

**Q. 作り直したアドオンを入れたら同じパックが 2 つ並びます / 更新されません。**
A. パック名とコマンド接頭辞が同じなら同じパック (同じ UUID) として作られ、バージョンはビルドごとに上がります。名前か接頭辞を変えると別のパックとして扱われます。

## 🗺 ロードマップ (Roadmap)
- [x] MP4対応 (Video format support)
- [x] GUI実装 (GUI Builder)
- [x] 音声同期再生 (Audio Sync)
- [x] GPU対応 (NVIDIA CUDA / AMD ROCm・DirectML / Intel DirectML)
- [x] マルチ動画パック対応 (Multi-video support)
- [x] **Phase 7: Research Edition**
  - オブジェクト指向JSエンジン (VideoPlayer クラスによるマルチスクリーン再生)
  - 局所的SSIMベースの知覚的RDO (エッジ・ディテール保存)
  - シーン適応型パレット & シーンGOP (0.5*SAD + 0.3*Hist + 0.2*Edge)
  - 統合ベンチマーク (SSIM, PSNR, LPIPS, ΔE2000)
- [x] **Phase 7.x: 次世代予測圧縮 (Next-Gen Prediction)** — `varint_rle_v5`
  - [x] Motion Vector Prediction (動き予測) — 8×8 ブロック、局所 ±4 + 縮小画像での全体探索。立体は 8×8×8 ブリックで (dx, dy, dz) ±2
  - [x] Tile Dictionary (タイル辞書圧縮) — エンコーダとデコーダが同じ規則で作る暗黙の辞書 (送らない)、新しい順の番号で参照、タイルの大きさ 4 / 8 を自動選択
  - [x] ラン位置の間隔符号化 (−38〜41%)、差分 / 動き / タイル / 両方 からフレームごとに最小を選択
  - [x] まとめ置き描画 (`fillBlocks` の箱・`ListBlockVolume`)、キーフレームで変わったブロックだけを置く、実機ベンチマーク (`bench`)
- [x] **3D立体ホログラム再生 (3D Hologram playback)** — `relief` (ヒステリシス付き) / `extrude` / `inflate` / MagicaVoxel `.vox` (シーングラフ・アニメーション対応)、3D の箱でまとめて設置、建築限界内への自動調整、等角投影のプレビュー
- [ ] **Phase 8: ワールド内レンダリングと知覚的な安定化 (In-World Rendering & Perceptual Stability)**
  - [ ] **8.1 描画方式の自動選択**: `bench` の結果 (µs/ブロック) をワールドに保存し、フレームごとに「呼び出し回数 × 1 回の費用 + ブロック数 × 1 個の費用」が最小の方式 (箱 / 座標リスト / 1 個ずつ) を選ぶ。計測では `list` が 1 フレーム 2〜33 回で、`rect` の 1/10〜1/300
  - [ ] **8.2 ディザの時間的安定化**: 前フレームと同じパレット番号で色差が許容範囲 (ΔE) 内なら変えない (条件付き更新) / ディザ模様を全体の動きに合わせてずらす。Blue Noise では動き補償が効かず、ディザなしのパンの −88% に対して −43% にとどまっているため
  - [ ] **8.3 2.5D パラメトリック立体**: `relief` / `extrude` / `inflate` を「色の平面 + 高さ (厚み) の平面」の 2 枚の 2D 映像として送り、アドオン側で列ごとに立体を組み立てる。2D の動き補償・タイル辞書がそのまま効き、調査ではレリーフで約 −69%
  - [ ] **8.4 エントロピー符号化**: モード・位置・色を別々のストリームにして、GOP ごとの静的ハフマン (Smacker 型) か LZ (LZ4 型) をかける。xz では v5 のデータからさらに 22〜39% 縮むことを確認済み
  - [ ] **8.5 ブロックモードのレート歪み最適化**: 8×8 ごとに SKIP / 動き / 単色 / 2 色マスク / 辞書 / ラン を、データ量 + 呼び出しの費用 + 色差 で選ぶ (Smacker・RoQ 型)。タイルの 8 通りの回転・反転も参照できるようにする (調査では −3〜9%)
  - [ ] **8.6 構造物とクローンによる描画**: よく使うタイルを `StructureManager` の構造物にして 1 回で置く、Script API 2.x の `cloneBlocks` (2.10.0 以降) で動きベクトルのブロックを 1 回でコピーする (API の移行と機能の有無の確認が必要)
  - [ ] **8.7 ホログラムの素材と入力**: ステンドグラスや発光ブロックの半透明パレット、AI による単眼深度推定 (Video-Depth-Anything など) の深度動画を `--depth-video` に使う手順、サブチャンク (16 ブロック) にそろえた設置
  - [ ] **8.8 実映像での評価**: 権利上公開できる実映像のデータセットで、データ量・呼び出し回数・ゲーム内の tick 時間 (MSPT)・ちらつき (時間方向の変化率) を CI で継続計測

詳細は [TODO.md](TODO.md) を参照してください。

## 📝 謝辞 (Acknowledgments)
- Original Java Datapack idea inspired by [umbreonben/mc-cushion-bad-apple](https://github.com/umbreonben/mc-cushion-bad-apple).
- Video processing powered by **FFmpeg**.

## 🤝 コントリビュート (Contributing)
Issue や Pull Request はいつでも歓迎します！
Feel free to open an Issue or submit a Pull Request!

## 📜 ライセンス (License)
This project is licensed under the [MIT License](LICENSE).

---

## 🏷 Releases

- **v5.4.0**: ゲーム内表示の多言語対応。チャットのメッセージとリモコン画面が、各プレイヤーの Minecraft の言語設定 (10 言語、未対応の言語は英語) で表示されるように。変換器のログも GUI で選んだ言語で表示。`main.js` をモック環境で動かす結合テストを追加。
- **v5.3.0**: GUI の多言語対応。画面右上の「🌐 言語」で 10 言語 (日本語・English・简体中文・繁體中文・한국어・Español・Português (Brasil)・Français・Deutsch・Русский) に切り替え可能。初回は OS の表示言語を自動選択し、選んだ言語を保存。翻訳は `locales/*.json` で追加・修正できます。
- **v5.2.0**: AMD GPU 対応。ROCm 版 PyTorch (Linux / Windows) と DirectML (Windows の AMD / Intel GPU) で GPU 変換が可能に。GUI に「変換デバイス」選択を追加し、GPU の動作確認に失敗した場合は自動で CPU 変換へ切り替え。GUI からの変換が引数エラーで失敗していた v5.1.0 の不具合を修正。
- **v5.1.0**: 品質改善リリース。自動パレット使用時にシーン切替で色が崩れる問題 (GOP 先頭を必ずキーフレーム化)、横長動画・サムネイルが中央に配置されない問題、シーク後に再開できず先頭に戻る問題、別の動画を選んでも切り替わらない問題、EXE 版で変換できない問題を修正。再生速度を動画データの fps から決定、変換を 2 パスのストリーミング化してメモリ使用量を削減、盤面クリアの分割実行、再ビルド時もパック UUID を維持、CI でテストとベンチマークを実行するよう改善。
- **v5.0.0**: Phase 7 Research Edition。オブジェクト指向JSエンジンによるマルチスクリーン再生、SSIMベースの知覚的RDO、シーン適応型パレット＆GOPを導入。
- **v4.0.0**: Phase 6。OkLab知覚色空間への移行による色再現性の改善、シーン適応型の自動圧縮制御(RDO/ME)、NumPyベクトル化によるエンコード効率向上、予測型GOPプリフェッチとスマートティック予算による再生安定性の強化。
- **v3.2.0**: v4 GOP-Chunked 遅延デコードフォーマットを導入。GOP 単位の独立チャンク分割+LRUキャッシュにより、高解像度動画のワールド読み込み速度を改善。
- **v3.1.0**: Ultra-HD (512x512) 描画、バジェットベースRDO/MEによる負荷分散、v3バイナリ連結フォーマットによる高速ワールドロード、Temporal Dithering などを搭載。
- **v2.8.0**: Phase 4 MVCodec 導入。動的自動ブロックパレット生成 (K-Means)、Blue Noise ディザリングと知覚最適化フィルター、GUIへのベンチマーク表示機能を追加。
- **v2.7.0**: Zero-copy FFmpegパイプライン導入によるストリーミング対応、UTF-16バイナリエンコードによる容量削減、FFmpeg HWAccelのYUV破損バグを修正。
- **v2.6.4**: Script API での音声再生時、Bedrock 1.21以降の厳格な引数仕様(`location`)により音が鳴らない問題を修正。
- **v2.6.3**: `.mcaddon` 生成時に Script API の依存関係が消えてしまうバグを修正。
- **v2.6.2**: GUI起動時の変数初期化エラーを修正。
- **v2.6.1**: READMEの全面改修、GUIのディザリングGPU対応表記を最適化。
- **v2.6.0**: Ordered (Bayer) ディザリング時のGPUテンソル並列計算と、VRAMパンク(OOM)対策を実装。
- **v2.5.0**: 音声再生に完全対応。`.mcaddon` 形式へのアーキテクチャ刷新。
- **v2.1.0**: PyTorch GPU アクセラレーション統合。
- **v2.0.0**: 拡張パレット、ゲーム内リモコンGUI搭載。
