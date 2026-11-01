# 対応環境 / Compatibility

✅ = 実際に動かして確かめた / 🧪 = 自動テストで確かめた / ⚠️ = 未確認 (動く想定だが確かめていない)

## アドオン (Minecraft 側)

アドオンは **安定版** の Script API (`@minecraft/server` 2.6.0、`@minecraft/server-ui` 2.0.0) だけを使い、
`min_engine_version` は **1.26.10** です (v6.31.0 までは 1.19.0 / 1.2.0、1.21.80 以降)。ベータ API (「試験的な機能」) は不要です。

2.x にしたのは次のためです (npm の型定義で確認):
- **独自のコマンド** (`/badapple:setup` など、2.4.0 から): `cheatsRequired: false` で登録するので、チートを有効にしなくても
  使える見込み (**実機で未確認**)。チートを有効にすると、そのワールドでは実績が解除されなくなる。`/scriptevent` も今までどおり使える。
- **TickingAreaManager** (2.6.0 から): 常時読み込みの区画を、コマンドの上限 (ワールド全体で 10 区画) とは別の、このパック専用の枠で
  登録する。区画は一時的で、ワールドを開き直すと消えることがあるので、再生を始めるときにも無ければ登録し直す。
- **isChunkLoaded** (2.4.0 から): 再生の前に、スクリーンの範囲のチャンクが読み込まれたかを調べる。
- 2.10.0 の `cloneBlocks` は、効果が小さい見込み (docs/research/phase7x.md の 13 章) なので使っていない。

| 環境 | 状態 | メモ |
|---|---|---|
| Windows 11 版 (統合版) | ✅ | 作者の PC 1 台で再生・3D・`bench` を確認 (アドオン v6.0.0)。確認したときの Minecraft の版は未記録 |
| Windows 10 版 (統合版) | ⚠️ | 未確認 (Windows 11 と同じアプリなので動く想定) |
| Android / iOS | ⚠️ | 同じ安定版 API なので動く想定。端末の性能が低いと高画質設定は重い (軽量・標準プリセット推奨) |
| Xbox / PlayStation / Nintendo Switch | ⚠️ | アドオンの導入方法 (Realms・マーケットプレイス以外の読み込み) が限られる |
| Bedrock Dedicated Server (BDS) | ⚠️ | 未確認。`/scriptevent` と tickingarea を使う |
| Realms | ⚠️ | 未確認 |
| Java 版 | ❌ | 対応しない (統合版専用) |

## 変換ツール (PC 側)

| 環境 | 状態 | メモ |
|---|---|---|
| Windows 11 + EXE (`BlockVideoPlayer-*-windows-x64.zip`) | ✅ | Python 不要 (FFmpeg は別に必要)。作者の PC (AMD RX 9070 XT、DirectML) と CPU 変換で確認 |
| Windows 11 + Python 3.12 | ✅ | 作者の PC で GUI・CLI を確認 (CPU と AMD の DirectML)。NVIDIA (CUDA)・Intel・Windows 10 は未確認 ⚠️ |
| Windows + Python 3.13 | ✅ (CPU のみ) | DirectML は 3.12 まで |
| Linux (Ubuntu) + Python 3.12 | 🧪 | CI で CLI と全テストを毎回実行 (CPU)。GUI は Tkinter があれば動く想定 |
| macOS | ⚠️ | CLI は動く想定 (GPU は使わない) |

## 版ごとの記録

Minecraft の新しい版で動作を確かめたら、この表に追記してください (Minecraft の版は「設定 → 一般」の下に表示されます)。

| アドオンの版 | Minecraft の版 | 端末 | 結果 | 日付 |
|---|---|---|---|---|
| v6.0.0 | 未記録 | Windows 11 (作者の PC) | ✅ 再生・3D・bench | 2026-10-11 |
| v6.5.0 | 未記録 | Windows 11 (作者の PC) | ✅ 3D サンプルの再生・リモコン・動画の切り替え・停止とクリア。⚠️ リモコンの記号が ☒ で表示 (v6.5.1 で修正) | 2026-10-12 |

不具合を見つけたら [Issue](https://github.com/ntk758/MinecraftAddon_Bedrock_Video-Player/issues/new/choose) で
「Minecraft の版・端末・アドオンの版」を添えて知らせてください。
