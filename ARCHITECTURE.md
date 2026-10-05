# System Architecture & Technical Specifications

本ドキュメントでは、**Block Video Player** のシステム構成、パイプライン設計、データ符号化仕様、および Minecraft Bedrock Edition 側の再生ロジックを解説します。

---

## 1. 全体パイプライン設計

```mermaid
flowchart TD
    A[動画ファイル MP4/MKV または PNG連番] -->|FFmpeg rawvideo パイプ / 縮小+レターボックス| B[RGB フレームストリーム]
    B -->|Pass 1: シーン適応型GOP 0.5*SAD + 0.3*Hist + 0.2*Edge| C[GOP 境界 + GOP ごとの自動パレット]
    B -->|Pass 2: OkLab 減色 + 知覚的RDO (GPU: CUDA/ROCm/DirectML) / Pillow 減色 (CPU)| D[パレット番号フレーム]
    C --> D
    D -->|行単位 RLE 差分 + VarInt| E[GOP 単位バイナリ]
    E -->|15bit/文字 UTF-16 パッキング| F[frames_video_id.js]
    F -->|videos.js 自動生成| G[Behavior Pack + Resource Pack .mcaddon]
    G -->|Minecraft インポート| H[Script API / main.js + codec.js]
    H -->|オブジェクト指向 VideoPlayer クラス| I[マルチスクリーン同時再生]
    I -->|dimension.setBlockPermutation| J[ゲーム内スクリーン盤面描画]
```

変換は 2 パスのストリーミングで行い、動画全体をメモリに載せません (Pass 1 と Pass 2 でそれぞれ入力を先頭から読み直します)。

---

## 2. 差分データ符号化仕様 (varint_rle_v5)

### ① ランの抽出
前フレーム (キーフレームでは「全画素未設置」) と比べて変化したピクセルを行ごとに走査し、同じ色が続く区間を 1 ラン (最大 64 ブロック) にまとめます。

### ② パッキング ＆ 可変長バイト符号化 (VarInt)
ラン先頭の 1D 位置 $idx = y \times \text{width} + x$、長さ、パレット番号 ($0 \le color < 128$) を 1 つの整数に結合します。
$$\text{val} = (idx \ll 13) \mid ((\text{length} - 1) \ll 7) \mid color$$

1 フレームのバイト列は次のとおりです。VarInt は 7 ビット区切り (MSB が継続ビット) です。

```
varint(frame_no << 2 | 動きベクトルあり << 1 | is_keyframe)
[動きベクトルありの場合] varint(個数), { varint(前のブロック番号との差 - 1), varint(packed ベクトル) } × 個数
varint(ラン数), varint(val) × ラン数        … 予測フレームとの差分
```

旧形式 `varint_rle_v4` はヘッダが `frame_no << 1 | is_keyframe` で動きベクトルがありません (デコーダは両方に対応)。

### ②' 動き補償 (Motion Vector Prediction)
- 画面を `mv_block` (8) × 8 のマクロブロックに分け、各ブロックについて前フレームを (dx, dy) ずらしたときに一致する画素が最も多い位置を探します。予測は `予測[y][x] = 前フレーム[clamp(y+dy)][clamp(x+dx)]` (範囲外は端の画素) です。
- 探索は局所 ±`--mv-range` (既定 4) に加え、高解像度では縮小画像 (長辺約 96 画素) で画面全体の動きを ±8 の範囲で探し、その周辺も候補にします (2 段階探索)。ベクトル成分の最大値は FRAME_DATA の `mv_range` に記録され、`packed = (dy + mv_range) × (2 × mv_range + 1) + (dx + mv_range)` です。
- エンコーダはフレームごとに「前フレームとの差分」と「動き補償 + 予測との差分」の両方を作り、**小さい方を採用** します。動き補償で悪化することはありません。キーフレームは動きベクトルを使いません。
- 計測例 (128×72, 10fps, 39 色, ディザなし): パン映像 −41%、図形アニメ −37%。Blue Noise ディザでは模様が画面に固定されるため −16〜24%。

### ③ GOP チャンクとキーフレーム
- フレームはシーン適応型 GOP ごとに 1 本のバイナリへ連結し、GOP 単位で遅延デコード (LRU キャッシュ + 次 GOP の先読み) します。
- **各 GOP の先頭は必ずキーフレーム** です。自動パレットでは GOP ごとにパレット番号の意味が変わるため、GOP 先頭で盤面全体を描き直します。
- GOP 内では `keyframe_interval` フレームごとにもキーフレームを入れます (0 なら GOP 先頭のみ)。シークは目的フレーム以前の直近キーフレームから順に描画します。
- adaptive-fps で省略したフレームは長さ 0 のエントリになり、盤面を更新しません。

### ④ 文字列化 (15bit/文字)
バイト列を 15 ビットずつ区切り、`0x1000 + 値` の文字にします (先頭文字はパディングビット数)。サロゲート領域や引用符・バックスラッシュを含まないため、JSON 文字列としてそのまま埋め込めます。

---

## 3. モジュール間データ構造

### `videos.js` (インデックス・モジュール)
```javascript
import { FRAME_DATA as video_badapple } from "./frames_badapple.js";

export const VIDEOS = { "badapple": video_badapple };

export const VIDEO_LIST = [
  { id: "badapple", title: "Bad Apple", frame_count: 6573, width: 64, height: 64 },
];
```

### `frames_{video_id}.js` (動画差分データ)
```javascript
export const FRAME_DATA = {
  "width": 64,
  "height": 64,
  "fps": 10.0,                     // 再生速度 (main.js は 20 / fps tick ごとに 1 フレーム進める)
  "frame_count": 6573,
  "keyframe_interval": 30,
  "format": "varint_rle_v5",
  "mv_block": 8,                   // 動き補償のマクロブロックの大きさ
  "mv_range": 4,                   // 動きベクトル成分の最大の絶対値
  "gop_boundaries": [0, 120, 300, ...],
  "adaptive_palette": false,       // true なら level_blocks は GOP ごとの配列
  "level_blocks": [{ "block": "minecraft:white_concrete" }, ...],
  "index": "...",                  // フレームごとの [gop_id, offset<<1|keyframe, length] (VarInt, 15bit文字列)
  "chunks": ["...", "..."]         // GOP ごとのフレームバイナリ (15bit文字列)
};
```

デコーダは `codec.js` (ゲーム内・Node 共通) と `mvcodec/decode.py` (ベンチマーク・テスト用) の 2 実装があり、`tests/test_codec_v4.py` で結果の一致を検証しています。

---

## 4. Minecraft 側 (`main.js`) 描画最適化設計

1. **`VideoPlayer` クラスによるオブジェクト指向化**:
   - `VideoPlayer` クラスをインスタンス化することで、座標や状態を個別にカプセル化し、同一ワールド内で複数のスクリーンを独立して同時再生可能なマルチスクリーンアーキテクチャを実現。
2. **`BlockPermutation` の一括事前キャッシュ (`initPaletteCache`)**:
   - C++ バインディングである `BlockPermutation.resolve(blockId, states)` を毎フレーム呼び出すと非常に重いため、起動時に配列へ 1 回だけキャッシュ。
3. **tick 予算付きの描画 (`MAX_BLOCKS_PER_TICK`)**:
   - 1 フレームの描画をジェネレータで分割し、1 tick あたりのブロック設置数を上限内に抑える。盤面クリアやシークも `system.runJob` で分割実行する。
4. **単一座標オブジェクトの再利用 (`tempBlockLoc`)**:
   - ガベージコレクション (GC) によるフレーム落ちを完全に防ぐため、各プレイヤーインスタンス内で `{ x: 0, y: 0, z: 0 }` オブジェクトを 1 つだけ生成してインスタンスを使い回し。
5. **表示文字列の多言語化 (`tr` / `notify`)**:
   - チャットやリモコン画面には文字列を直接渡さず、`{ translate: "bvp.<キー>", with: {...} }` の RawMessage を渡す。各プレイヤーのクライアントが、自分のゲーム言語の `texts/<言語>.lang` で表示する (マルチプレイでもプレイヤーごとに別の言語になる)。
   - `.lang` は GUI のビルド時に `locales/*.json` の `addon.*` キーから生成する (`gui_i18n.addon_lang_files`)。翻訳の無い言語では `en_US` が使われる。
   - コンテンツログ向けの `console.warn` は開発者向けなので英語のまま。
6. **表示中の盤面を保持した差分描画 (`board` / `placeChangedBlocks`)**:
   - 各 `VideoPlayer` は今スクリーンに出ているパレット番号を `Int16Array` で持ち、`codec.js` の `decodeFrameInto` で次の盤面を作ってから、**値が変わったブロックだけ** を置きます。動き補償のフレームでも、置くブロック数は「実際に変わった画素」だけです。
   - キーフレーム・盤面クリア後は盤面を「不明 (-1)」にして全ブロックを置き直すため、パレット切替やシークでも表示が崩れません。

---

## 5. Phase 7: エンコーダーパイプラインの進化

1. **局所的SSIMベースの知覚的RDO (Perceptual Rate-Distortion Optimization)**:
   - 従来の一律な圧縮ではなく、エッジやディテール（文字など）が集中する重要な領域を局所的SSIMで判定し、視覚的品質を保持したままレート歪み最適化を実施。
2. **シーン適応型GOP (Scene Adaptive GOP)**:
   - 評価式 `0.5*SAD (Sum of Absolute Differences) + 0.3*Hist (Histogram Diff) + 0.2*Edge (Edge Diff)` に基づき、シーンチェンジを動的に検知。静的なシーンではGOPを長く、激しいシーンでは短く自動調整。
3. **シーン適応型パレット (Adaptive Palette)**:
   - シーンごとに最適なカラーパレットを算出し、パレットハッシュで管理。シーン転換時に動的にパレットを切り替えることで色再現性を極限まで高める。
