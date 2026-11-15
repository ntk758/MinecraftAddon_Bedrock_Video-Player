# Project Status — Block & Image Video Player for Minecraft Bedrock Edition

今の実装と、確かめた範囲をまとめたものです。2026-11-15 に、v6.37.1 (`3234c68`) のコードと照らし合わせて書き直しました。

- 版ごとの変更は [CHANGELOG.md](CHANGELOG.md)、今後の計画は [TODO.md](TODO.md) にあります。
- 端末ごとの状況は [docs/COMPATIBILITY.md](docs/COMPATIBILITY.md)、既知の問題は [docs/KNOWN_ISSUES.md](docs/KNOWN_ISSUES.md) にあります。

## 1. 概要

動画を Minecraft 統合版のアドオン (`.mcaddon`、ビヘイビアーパックとリソースパック) に変換して、ゲーム内で再生するツールとアドオンです。

- **最新バージョン**: `v6.38.0` (版の唯一の管理場所は `pack_metadata.py` の `PACK_VERSION`)
- **アドオン**:
  - `@minecraft/server` 2.6.0 / `@minecraft/server-ui` 2.0.0 (安定版の API だけ)
  - `min_engine_version` 1.26.10
  - ブラウザ版だけは、1.21.80〜1.26.0 向けに Script API 1.x のアドオンも作れる (`legacy_target.py`。実機では未確認)
- **変換ツール**: 3 種類
  - Windows の EXE (Python 不要、FFmpeg は別に必要)
  - Python 3.12+ の GUI・CLI (PyTorch があれば CUDA / ROCm / DirectML)
  - ブラウザ版 (Pyodide。同じ `convert.py`・`mvcodec` を動かす。CPU だけ)

状態の記号: ✅ 実機で確認 (端末と Minecraft の版つき) / 🧪 自動テストだけ / ⚠️ 未検証 / ❌ 未実装。

## 2. 再生 (アドオン)

### ブロックモード (`main.js`・`codec.js`)

| 機能 | 状態 | メモ |
|---|---|---|
| 平面のスクリーン (床置き・`setup wall`・`setup face`・`setup align`) | ✅ | Windows 11。v6.36.0 で床から壁への置き直しを確認 |
| 立体 (relief / extrude / inflate / MagicaVoxel `.vox`)・2.5D | ✅ | Windows 11 (v6.0.0・v6.18.0 の録画) |
| 視点に合わせただまし絵・背景の映像・MMD | ✅ | Windows 11 (利用者、v6.20〜v6.27)。正しく見えるのは 1 人だけ |
| 音声 (10 秒のチャンク、プレイヤーごとに `playSound`) | ✅ | Windows 11。途中で入った人には、チャンクの頭から 1 秒以内なら鳴らす |
| 一時停止・再開・シーク・動画の切り替え・停止とクリア | ✅ 🧪 | モックで毎回確認。実機は v6.5.0・v6.32.1 の修正時 |
| リモコン (コンパス、`ActionFormData` / `ModalFormData`) | ✅ | 音量・シーク・動画リスト・目の前に置く |
| チート無しの独自のコマンド (`/<接頭辞>:setup` など) | ✅ | 2026-11-01 に利用者が確認。`/scriptevent` も使える |
| 常時読み込み (TickingAreaManager、このパックの枠) | ✅ | ワールドを開き直したら、再生の前に登録し直す |
| 描画方式の自動選択 (`fillBlocks` の箱・`ListBlockVolume`・1 個ずつ) と `bench`・`stats` | ✅ | 1 tick の上限は 4000 ブロック・30 ms (`MAX_BLOCKS_PER_TICK`・`TICK_TIME_BUDGET_MS`) |
| ゲーム内の表示の 10 言語 | ✅ 🧪 | プレイヤーごとのゲームの言語で表示 |
| 複数のスクリーンの同時再生 | ❌ | `setup` は前の画面を片付けて 1 つにする (`activePlayers.clear()`)。例外は、だまし絵の背景の映像だけ |
| 複数人 | 🧪 ⚠️ | ブロックは全員に同じに見える。音は人ごと。実機の複数人は未検証 |
| 長時間の再生 (メモリ・TPS) | ⚠️ | 未計測 |

### 画像モード (`image_mode/image_main.js`)

| 機能 | 状態 | メモ |
|---|---|---|
| アトラスのテクスチャを板状のエンティティに表示 (方式 A)、30 fps | ✅ | Windows 11 / Minecraft v26.63 (v6.36.0〜v6.37.1) |
| 夜でも暗くしない (`--lighting bright`、既定) | ✅ | v6.37.0 |
| 再生の最初から正面を向く | ✅ | v6.37.1 で修正 |
| 音声 (4 秒のチャンク) | ✅ | 一時停止・シーク・開き直しの後は、次の境目まで最大 4 秒無音 (仕様) |
| リモコン (時計)・`play`・`volume`・`stats`・`setup face/wall/align`・10 言語 | ✅ | v6.36.0 |
| ブロックモードと 1 つのファイルにまとめる | ✅ | v6.36.0 |
| 再生位置の数え方 | 🧪 ⚠️ | サーバーの tick の数で数える。TPS が下がったときの映像と音声のずれは未検証 (TODO P0-2) |
| 方式 C (自作マテリアル、`--method c`) | 🧪 | 既定では入れない。将来の更新で壊れるリスクがある |
| 予算 (Tier 2 = 225 MB / Tier 5 = 800 MB) を超えたときのスマートフォンの動き | ⚠️ | PC では 872 MB でも読み込めた |
| 画面は 1 つ | ❌ | 状態はワールドで 1 つ |

## 3. 変換

| 機能 | 状態 | メモ |
|---|---|---|
| 予測圧縮 (`varint_rle_v6`、動き補償・タイル辞書・区間符号) | 🧪 | Python の参照デコーダと `codec.js` の往復テスト |
| 自動パレット | 🧪 | 光るブロックを除く 214 色の候補から、シーンごとに 24〜127 色。全ブロックは 220 色 |
| パターンディザ (Blue Noise)・条件付き更新・8×8 の最適化 | 🧪 | GUI とブラウザ版の既定は、自動パレット + Blue Noise。CLI の既定は 39 色・ディザなし (`convert.py` の引数の既定) |
| 平らな面のムラを減らす `--flat-areas smooth\|balance` | 🧪 ✅ | v6.37.0。CLI だけで既定は `off`。PC の実機で空と草が一様に見えることを確認。近いブロックの無い色はくすむ |
| GPU の減色 (CUDA / ROCm / DirectML) と CPU への切り替え | ✅ | AMD (DirectML) と CPU だけ確認。NVIDIA・Intel は未検証 |
| 画像モードの変換 (`convert_image_mode.py`) | ✅ | 予算に合わせた優先 (`--priority`)、area 縮小、`--snap`、`--png-optimize`。ブラウザ版はブロックモードのコマの大きさのまま |
| 立体の入力 (relief など・`.vox`・MMD・AI の人物の切り抜き・深度動画) | ✅ 🧪 | KNOWN_ISSUES に限界を記載。音声が映像より短い動画での切り抜きの不具合は未修正 |
| はじめてガイド・できあがりのプレビュー | ✅ | Windows 版 |
| ブラウザ版 (10 言語、カメラ、MMD、OGG の音声) | ✅ 🧪 | Chromium と Node の Pyodide で CI。iPhone の音声の不具合は v6.29.1 で修正 |
| 利用数の計測 | ✅ | ブラウザ版だけ、GoatCounter で回数と言語 (個人は特定しない) |

## 4. 端末

| 環境 | 状態 |
|---|---|
| Windows 11 (統合版) | ✅ 作者の PC / Minecraft v26.63 (v6.36.0〜v6.37.1) |
| Windows 10・Android・iOS・BDS・Realms・Xbox・PlayStation・Switch | ⚠️ 未検証 (安定版の API だけなので動く想定) |
| 変換ツール: Windows 11 (EXE・Python 3.12、AMD DirectML・CPU) | ✅ |
| 変換ツール: Linux (CLI) | 🧪 CI |
| 変換ツール: macOS・NVIDIA・Intel・Windows 10 | ⚠️ |

## 5. 主な課題

優先度と完了条件は [TODO.md](TODO.md) にあります。

1. ドキュメントの版と数字が手作業で、ずれやすい (P0-1)。
2. 画像モードの再生位置が tick の数で、TPS が下がったときの同期が未検証 (P0-2)。シークの後の無音もある (P0-3)。
3. 実機の確認が Windows の 1 台だけで、版によって確かめた範囲がまちまち (P0-5・P1-1〜P1-3)。
4. 長時間の再生のメモリが未計測 (P0-6)。

## 6. 主なファイル

```
.
├── convert.py               ブロックモードの変換 CLI (2 パス・ストリーミング、GPU / CPU)
├── convert_image_mode.py    画像モードの変換 CLI
├── mvcodec/                 パレット・減色・符号化・Python の参照デコーダ・AI の切り抜き
├── image_mode/              画像モード (アトラス・予算・リソースパック・image_main.js・SPEC.md)
├── main.js / codec.js       ブロックモードの再生スクリプトと、Minecraft に依存しないデコーダ
├── manifest.json            ビヘイビアーパックのマニフェストのひな形
├── pack_builder.py          .mcaddon の組み立て
├── pack_metadata.py         版と更新履歴の唯一の管理場所
├── video_player_gui.py      Windows 版の GUI (gui_wizard.py ははじめてガイド)
├── web/                     ブラウザ版 (Pyodide)
├── legacy_target.py         Script API 1.x 向けの書き換え (ブラウザ版)
├── build_standalone.py      EXE のビルド (PyInstaller)
├── benchmark/               画質・容量・速さの計測
├── tests/                   pytest と Node のモック (tests/js/minecraft_mock)
├── docs/                    対応環境・既知の問題・テスト・リリース・研究の記録
└── .github/workflows/       CI・リリース・GitHub Pages・週 1 回の実映像の計測
```
