# System Architecture & Technical Specifications

本ドキュメントでは、**Block Video Player** のシステム構成、パイプライン設計、データ符号化仕様、および Minecraft Bedrock Edition 側の再生ロジックを解説します。

---

## 1. 全体パイプライン設計

```mermaid
flowchart TD
    A[動画ファイル MP4/MKV] -->|FFmpeg| B[PNG連番画像]
    B -->|convert.py / ThreadPoolExecutor| C[Pillow Quantize / Dither]
    C -->|色クラスタリング| D[全50+色マイクラブロックマッピング]
    D -->|差分検出 & Adaptive FPS| E[Delta VarInt 符号化]
    E -->|Base64 エンコード| F[frames_video_id.js]
    F -->|videos.js 自動生成| G[Behavior Pack .mcpack]
    G -->|Minecraft インポート| H[Script API / main.js]
    H -->|Base64 & VarInt 高速デコード| I[BlockPermutation 一括キャッシュ]
    I -->|dimension.setBlockPermutation| J[ゲーム内スクリーン盤面描画]
```

---

## 2. 差分データ符号化仕様 (Delta VarInt + Base64)

### ① 1D フラットインデックス変換
$N \times M$ のブロック盤面において、ピクセル座標 $(x, y)$ を $1\text{D インデックス } idx = y \times \text{width} + x$ へ展開。

### ② デルタ（差分）符号化
前回のフレームからの変化ピクセルのみ抽出し、直前の変更ピクセルインデックス $idx_{\text{prev}}$ からの差分を算出：
$$\Delta idx = idx - idx_{\text{prev}}$$

### ③ パッキング ＆ 可変長バイト符号化 (VarInt)
$\Delta idx$ とブロック色レベル ID ($0 \le color < 64$) を 1 つの 32ビット整数値 `val` に結合：
$$\text{val} = (\Delta idx \ll 6) \mid (color \ \& \ 0x3f)$$

この `val` を 7 ビット区切りの可変長整数 (VarInt) にバイト配列化（MSB Continuation Bit 適用）：
```python
def encode_varint(val, byte_arr):
    while val >= 0x80:
        byte_arr.append((val & 0x7f) | 0x80)
        val >>= 7
    byte_arr.append(val & 0x7f)
```

### ④ Base64 文字列化
バイト配列を ASCII Base64 文字列にエンコードし、JSON 内のカンマ `,` や括弧 `[` `]` の記号オーバーヘッドを **100% 撲滅**。

---

## 3. モジュール間データ構造

### `videos.js` (インデックス・モジュール)
```javascript
import { FRAME_DATA as video_badapple } from "./frames_badapple.js";
import { FRAME_DATA as video_demo } from "./frames_demo.js";

export const VIDEOS = {
  "badapple": video_badapple,
  "demo": video_demo,
};

export const VIDEO_LIST = [
  { id: "badapple", title: "Bad Apple", frame_count: 6573, width: 64, height: 64 },
  { id: "demo", title: "Demo Video", frame_count: 1200, width: 64, height: 64 },
];
```

### `frames_{video_id}.js` (動画差分データ)
```javascript
export const FRAME_DATA = {
  "width": 64,
  "height": 64,
  "level_blocks": [
    { "block": "minecraft:white_concrete", "states": {} },
    { "block": "minecraft:orange_concrete", "states": {} },
    ...
  ],
  "frame_count": 6573,
  "frames": [
    "A7dB...", // Base64 エンコードされた差分文字列 (または空文字 "" でフレームスキップ)
    ...
  ]
};
```

---

## 4. Minecraft 側 (`main.js`) 描画最適化設計

1. **`BlockPermutation` の一括事前キャッシュ (`initPaletteCache`)**:
   - C++ バインディングである `BlockPermutation.resolve(blockId, states)` を毎フレーム呼び出すと非常に重いため、起動時に配列へ 1 回だけキャッシュ。
2. **単一座標オブジェクトの再利用 (`tempBlockLoc`)**:
   - ガベージコレクション (GC) によるフレーム落ちを完全に防ぐため、`{ x: 0, y: 0, z: 0 }` オブジェクトを 1 つだけ生成してインスタンスを使い回し。
3. **`tickingarea` の自動設置**:
   - プレイヤーが離れても盤面ブロックがアンロードされないよう、`setup` 時に領域 `badapple_area` を自動生成。
4. **二重起動・ゴースト再生の防止**:
   - `timeoutId` と `intervalId` を独立管理し、`stopPlayback()` で確実に `system.clearRun()` を実行。
