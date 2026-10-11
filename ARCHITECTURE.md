# System Architecture & Technical Specifications

本ドキュメントでは、**Block Video Player** のシステム構成、パイプライン設計、データ符号化仕様、および Minecraft Bedrock Edition 側の再生ロジックを解説します。

---

## 1. 全体パイプライン設計

```mermaid
flowchart TD
    A[動画ファイル MP4/MKV または PNG連番] -->|FFmpeg rawvideo パイプ / 縮小+レターボックス| B[RGB フレームストリーム]
    V[MagicaVoxel .vox] -->|シーングラフを評価 / ブロック色へ最近傍対応| L
    B -->|Pass 1: シーン適応型GOP 0.5*SAD + 0.3*Hist + 0.2*Edge| C[GOP 境界 + GOP ごとの自動パレット]
    B -->|Pass 2: OkLab 減色 + 知覚的RDO (GPU: CUDA/ROCm/DirectML) / Pillow 減色 (CPU)| D[パレット番号フレーム]
    C --> D
    D -->|screen| E
    D -->|relief / extrude / inflate| L[立体 (奥行き×高さ×幅)]
    L --> E[StreamEncoder: 差分 / 動き補償 (2D・3D) / タイル辞書 のうち最小を選択]
    E -->|間隔符号化 RLE + VarInt| F[GOP 単位バイナリ]
    F -->|15bit/文字 UTF-16 パッキング| G[frames_video_id.js]
    G -->|videos.js 自動生成| H[Behavior Pack + Resource Pack .mcaddon]
    H -->|Minecraft インポート| I[Script API / main.js + codec.js]
    I -->|VideoPlayer: decoded (論理盤面) と board (表示中) を分けて差分だけ描画| J[rect: fillBlocks(BlockVolume) の箱 / list: fillBlocks(ListBlockVolume) / block: setBlockPermutation]
```

変換は 2 パスのストリーミングで行い、動画全体をメモリに載せません (Pass 1 と Pass 2 でそれぞれ入力を先頭から読み直します)。

---

## 2. 差分データ符号化仕様 (varint_rle_v5)

### ① ランの抽出
予測フレーム (キーフレームでは「全画素未設置」、立体では「全部空気」) と比べて変化したピクセルを行ごとに走査し、同じ色が続く区間を 1 ラン (最大 64 ブロック) にまとめます。

### ② パッキング ＆ 可変長バイト符号化 (VarInt)
ランの位置・長さ・パレット番号 ($0 \le color < 128$) を 1 つの整数に結合します。
$$\text{val} = (\text{gap} \ll 13) \mid ((\text{length} - 1) \ll 7) \mid color$$

`gap` は **直前のランの終わりからの距離** です (フレームの先頭のランは位置 0 から)。旧形式 v4 は絶対位置 $y \times \text{width} + x$ を入れていましたが、ランは位置の順に並ぶので差の方がずっと小さく、varint のバイト数が減ります (平面 128×72 で −27〜43%。立体は位置が 16〜20 bit になるため効果が大きく、relief 64×48×16 で 320 → 195 KB)。

1 フレームのバイト列は次のとおりです。VarInt は 7 ビット区切り (MSB が継続ビット) です。

```
varint(frame_no << 3 | タイル参照あり << 2 | 動きベクトルあり << 1 | is_keyframe)
[動きベクトルあり] varint(個数), { varint(前のブロック番号との差 - 1), varint(packed ベクトル) } × 個数
[タイル参照あり]   varint(個数), { varint(前のタイル番号との差 - 1), varint(辞書の新しい方から何番目か) } × 個数
varint(ラン数), varint(val) × ラン数        … 予測 (動き補償 → タイル貼り付けの結果) との差分
```

旧形式 `varint_rle_v4` はヘッダが `frame_no << 1 | is_keyframe` で、動きベクトル・タイル参照が無く、ランの位置が絶対位置です (デコーダは両方に対応)。

### ②' 動き補償 (Motion Vector Prediction)
- **平面**: 画面を `mv_block` (8) × 8 のマクロブロックに分け、各ブロックについて前フレームを (dx, dy) ずらしたときに一致する画素が最も多い位置を探します。予測は `予測[y][x] = 前フレーム[clamp(y+dy)][clamp(x+dx)]` (範囲外は端の画素) です。探索は局所 ±`--mv-range` (既定 4) に加え、高解像度では縮小画像 (長辺約 96 画素) で画面全体の動きを ±8 の範囲で探し、その周辺も候補にします (2 段階探索)。`packed = (dy + r) × (2r + 1) + (dx + r)`、`r` は FRAME_DATA の `mv_range`。
- **立体**: 8×8×8 のブリックごとに (dx, dy, dz) を ±2 から探します (125 候補)。`予測[z][y][x] = 前フレーム[clamp(z+dz)][clamp(y+dy)][clamp(x+dx)]`、ブリック番号は `(bz × ブリックの行数 + by) × ブリックの列数 + bx`、`packed = ((dz + r) × span + (dy + r)) × span + (dx + r)` (r = 2 なら 0〜124 で varint 1 バイト)。奥へ・手前へ動く物体も予測できます (平面のように奥行きを行に並べたまま 2D で探すと予測できません)。
- エンコーダはフレームごとに「差分のみ / 動き補償 / タイル参照 / 両方」をすべて作り、**一番小さいものを採用** します。悪化することはありません。キーフレームは動きベクトル・タイル参照を使いません。

### ②'' タイル辞書 (Tile Dictionary)
- エンコーダとデコーダが **同じ規則で同じ辞書を作る** 暗黙の辞書です (LZ78/GIF の LZW と同じ考え方で、辞書の中身は送りません)。
  - キーフレーム: 辞書を空にして、そのフレームの全タイル (`tile_size` × `tile_size`、端からはみ出すタイルは対象外) を行優先で登録
  - それ以外: 直前の盤面から内容が変わったタイルを行優先で登録 (`tile_dict_max` = 32768 個に達したら次のキーフレームまで登録しない)
- 参照は「辞書の新しい方から何番目か」。最近現れた模様 (まばたき・口の形・点滅する字幕・行き来する物体) ほど小さい番号になります。
- 貼り付けは動き補償の後、差分ランの前です。エンコーダは予測との不一致が `MIN_MISMATCH` (4) 画素以上あるタイルだけ、辞書の完全一致を探します。
- タイルの大きさは `--tile-size auto` (既定) で 4 と 8 の両方を符号化し、小さい方を使います (動き探索の結果は共有)。

### ③ GOP チャンクとキーフレーム
- フレームはシーン適応型 GOP ごとに 1 本のバイナリへ連結し、GOP 単位で遅延デコード (LRU キャッシュ + 次 GOP の先読み) します。
- **各 GOP の先頭は必ずキーフレーム** です。自動パレットでは GOP ごとにパレット番号の意味が変わるため、GOP 先頭で盤面を描き直します。
- GOP 内では `keyframe_interval` フレームごとにもキーフレームを入れます (0 なら GOP 先頭のみ)。シークは目的フレーム以前の直近キーフレームから目的フレームまでを復元し (途中のフレームはブロックを置かない)、目的フレームとの差だけを置きます。タイル辞書もキーフレームで作り直すので、途中から再生しても一致します。main.js は `decoded` が何番のフレームの状態かを覚えていて、シークの中断・ベンチマーク・描画途中の一時停止などで順番が飛んだときも、同じ方法で復元し直してから描きます。
- adaptive-fps で省略したフレームは長さ 0 のエントリになり、盤面を更新しません。立体では「空気以外のマス」に対する変化率で判定します (ほとんど空気の立体で、動いている物体を省略しないため)。

### ④ 文字列化 (15bit/文字)
バイト列を 15 ビットずつ区切り、`0x1000 + 値` の文字にします (先頭文字はパディングビット数)。サロゲート領域や引用符・バックスラッシュを含まないため、JSON 文字列としてそのまま埋め込めます。

### ⑤ 立体 (3D ホログラム) のレイアウト
- 立体は (奥行き D, 高さ H, 幅 W) の配列で、空気は `air_level` (127) 番です。符号化では (D×H) 行 × W 列の 2D 画像として扱い、位置 `p = (z × H + y) × W + x` です (z = 0 が一番奥 = 北、y = 0 が一番上)。
- キーフレームは「全部空気」を基準に符号化するので、空気のランは含まれません。
- 立体の作り方 (`mvcodec/volume.py`):
  - `relief`: 各画素の明るさ (または `--depth-video` の深度動画) を奥行きにし、表面と段差の側面だけを置く。前フレームの段から `--depth-hysteresis` (既定 1) 段未満しか動いていない画素は段を変えない (ちらつき・置き直しを減らす)
  - `extrude`: 明るい (または暗い) 部分を奥行きの中央に `--thickness` 段押し出す
  - `inflate`: 4 近傍の距離変換で縁からの距離 d を求め、厚みの半分を `sqrt(d × D / 2)` (D/2 で頭打ち) にした丸い立体
  - `vox`: MagicaVoxel の .vox。シーングラフ (nTRN/nGRP/nSHP/LAYR) とアニメーションのキーフレームを ogt_vox と同じ規則で評価し、vox の x を東、y を北 (y が大きいほど奥)、z を上へ、回転 (鏡像にならない) で対応させる
- `--hollow` は外から見えない内部を空気にします。既定の描画 (箱でまとめて fillBlocks) では詰まっている方が呼び出しもデータも少ないため、既定は無効です。

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
  "mv_block": 8,                   // 動き補償のマクロブロック (立体はブリック) の大きさ
  "mv_range": 4,                   // 動きベクトル成分の最大の絶対値 (立体は 2)
  "tile_size": 4,                  // タイル辞書のタイルの大きさ (0 なら不使用)
  "tile_dict_max": 32768,          // 辞書に登録するタイルの上限 (キーフレームで空に戻る)
  "gop_boundaries": [0, 120, 300, ...],
  "adaptive_palette": false,       // true なら level_blocks は GOP ごとの配列
  "level_blocks": [{ "block": "minecraft:white_concrete" }, ...],
  "layout": "screen",              // "screen" (平面) または "volume" (立体)
  // 立体のときだけ: "depth": 16, "air_level": 127
  "index": "...",                  // フレームごとの [gop_id, offset<<1|keyframe, length] (VarInt, 15bit文字列)
  "chunks": ["...", "..."]         // GOP ごとのフレームバイナリ (15bit文字列)
};
```

デコーダは `codec.js` (ゲーム内・Node 共通) と `mvcodec/decode.py` (ベンチマーク・テスト用) の 2 実装があり、`tests/test_codec_v4.py` / `test_motion.py` / `test_tiles.py` / `test_volume.py` / `test_volume3d.py` で結果の一致を検証しています。

---

## 4. Minecraft 側 (`main.js`) 描画最適化設計

1. **`VideoPlayer` クラスによるオブジェクト指向化**:
   - `VideoPlayer` クラスをインスタンス化することで、座標や状態を個別にカプセル化し、同一ワールド内で複数のスクリーンを独立して同時再生可能なマルチスクリーンアーキテクチャを実現。
2. **`BlockPermutation` の一括事前キャッシュ (`initPaletteCache`)**:
   - C++ バインディングである `BlockPermutation.resolve(blockId, states)` を毎フレーム呼び出すと非常に重いため、起動時に配列へ 1 回だけキャッシュ。
3. **tick 予算付きの描画 (`MAX_BLOCKS_PER_TICK` / `TICK_TIME_BUDGET_MS`)**:
   - 1 フレームの描画をジェネレータで分割し、1 tick あたりのブロック設置数と時間 (30 ms) を上限内に抑える。盤面クリアやシークも `system.runJob` で分割実行する。
4. **論理盤面と表示盤面の分離 (`decoded` / `board`)**:
   - `decoded` はエンコーダの参照と同じ「直前のフレーム」で、動き補償・タイル辞書の予測はこれを使う。`board` は **実際にワールドに置かれているブロック** (不明は -1)。
   - `decodeFrameInto` で次の盤面を作ってから、`board` と値が違うブロックだけを置きます。キーフレームでも全ブロックを置き直さず、実際に変わったブロックだけを置きます (自動パレットで GOP が変わったときだけ、表示中の盤面を「不明」にして置き直す)。
   - 置くのに失敗したマスは -1 (不明) にして、次のフレームで置き直します。
5. **描画方式 (`/scriptevent <接頭辞>:render rect|list|block`)**:
   - `rect` (既定): 同じ色の変更ブロックを、横 → 縦 → (立体では) 奥行きの順に伸ばした箱にまとめ、`dimension.fillBlocks(new BlockVolume(from, to))` 1 回で置く (1 回 32768 ブロックまで)。
   - `list`: 変わったマスを色ごとの座標リストにまとめ、`fillBlocks(new ListBlockVolume(座標))` で置く (4096 個ずつ)。呼び出し回数はおよそ「変わった色の数」。
   - `block`: 1 個ずつ `setBlockPermutation`。
   - `/scriptevent <接頭辞>:bench` はスクリーンの範囲 (最大 64×64) で 6 通りの置き方 (`setBlockPermutation` / 1 行ずつの `fillBlocks` / 全面 1 回の `fillBlocks` / `ListBlockVolume` / `structureManager.place` / `fill` コマンド) を実機で計り、1 ブロックあたりのマイクロ秒をチャットに出す。端末によって速い方式が違うため、結果を見て描画方式を選ぶ。
6. **単一座標オブジェクトの再利用 (`tempBlockLoc`)**:
   - ガベージコレクション (GC) によるフレーム落ちを防ぐため、1 個ずつ置くときは `{ x: 0, y: 0, z: 0 }` を使い回す。
7. **常時読み込み範囲と設置位置**:
   - `tickingarea` は 1 区画 100 チャンクまでなので、10×10 チャンクずつに分けて最大 8 区画登録する (足りない場合は警告して続行)。
   - 立体はプレイヤーの 2 ブロック北に左右中央をそろえて立て、`dimension.heightRange` (オーバーワールドは Y=-64〜319) に収まるよう高さを調整する。
8. **表示文字列の多言語化 (`tr` / `notify`)**:
   - チャットやリモコン画面には文字列を直接渡さず、`{ translate: "bvp.<キー>", with: {...} }` の RawMessage を渡す。各プレイヤーのクライアントが、自分のゲーム言語の `texts/<言語>.lang` で表示する (マルチプレイでもプレイヤーごとに別の言語になる)。
   - `.lang` は GUI のビルド時に `locales/*.json` の `addon.*` キーから生成する (`gui_i18n.addon_lang_files`)。翻訳の無い言語では `en_US` が使われる。
   - コンテンツログ向けの `console.warn` は開発者向けなので英語のまま。

---

## 5. Phase 7: エンコーダーパイプラインの進化

1. **局所的SSIMベースの知覚的RDO (Perceptual Rate-Distortion Optimization)**:
   - 従来の一律な圧縮ではなく、エッジやディテール（文字など）が集中する重要な領域を局所的SSIMで判定し、視覚的品質を保持したままレート歪み最適化を実施。
2. **シーン適応型GOP (Scene Adaptive GOP)**:
   - 評価式 `0.5*SAD (Sum of Absolute Differences) + 0.3*Hist (Histogram Diff) + 0.2*Edge (Edge Diff)` に基づき、シーンチェンジを動的に検知。静的なシーンではGOPを長く、激しいシーンでは短く自動調整。
3. **シーン適応型パレット (Adaptive Palette)**:
   - シーンごとに最適なカラーパレットを算出し、パレットハッシュで管理。シーン転換時に動的にパレットを切り替えることで色再現性を極限まで高める。

## 6. Phase 7.x: 次世代予測圧縮と 3D ホログラム

実装の詳細・先行事例・計測結果は [docs/research/phase7x.md](docs/research/phase7x.md) を参照してください。

| 要素 | 実装 | 主なファイル |
|---|---|---|
| 動き補償 (2D) | 8×8 ブロック、局所 ±4 + 縮小画像での全体探索 | `mvcodec/motion.py` |
| 動き補償 (3D) | 8×8×8 ブリック、(dx, dy, dz) ±2 | `mvcodec/motion.py` (`*_3d`)、`codec.js` (`decodeMotion3d`) |
| タイル辞書 | 暗黙の辞書 (キーフレームで初期化)、新しい順の番号で参照、4/8 自動選択 | `mvcodec/tiles.py`、`codec.js` (`TileDictionary`) |
| ラン位置の間隔符号化 | 直前のランの終わりからの距離 | `mvcodec/encode.py`、`codec.js` |
| 方式の選択 | 差分 / 動き / タイル / 両方 の最小をフレームごとに選ぶ | `mvcodec/stream.py` |
| 立体の生成 | relief (ヒステリシス付き) / extrude / inflate / vox | `mvcodec/volume.py` |
| 描画 | 3D の箱 (rect)・色ごとの座標リスト (list)・実機ベンチマーク | `main.js` |
| プレビュー | 等角投影の GIF (ゲームと同じ規則で復元) | `scripts/preview_volume.py`、`mvcodec/volume.py` (`iso_view`) |
| ベンチマーク | 合成映像で別の版とデータ量・API 呼び出し数を比較 | `benchmark/codec_bench.py` |
