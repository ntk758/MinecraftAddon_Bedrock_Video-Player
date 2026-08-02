# Architectural Decision Records (ADR) & Technical Trade-offs

本ドキュメントでは、プロジェクト開発過程で検証・評価された各種アルゴリズム、データフォーマット、並列処理手法の**採用・不採用の決定事項およびその技術的理由**を詳細に記録します。

---

## 1. 採用された決定事項 (Accepted)

### ✅ A1. Delta VarInt + Base64 圧縮方式
- **理由**: 2D座標配列 `[[x, y, color], ...]` や RLE (`[[start, len, color], ...]`) を JSON に埋め込むと、`[`, `]`, `,` などのJSONメタ記号文字数が全データの70%以上を占めてしまう問題が発生。
- **効果**: 差分インデックス $\Delta idx$ と色レベルを結合し、VarInt化＋Base64文字列に集約することで、JSON記号文字を完全に排除。JSONサイズを初期比 **87.5% 圧縮 (1/8以下)**。

### ✅ A2. Pillow Quantize + ThreadPoolExecutor 並列処理
- **理由**: Python標準の NumPy ブロードキャスト減色は非常に重く（約 45 fps）、また KDTree や 3D-LUT はメモリ消費が大きく構築コストが高い。
- **効果**: Pillow の `Image.quantize()` は C言語ネイティブで実行され、Python の GIL (Global Interpreter Lock) を解放する。そのため `ProcessPoolExecutor`（IPC通信オーバーヘッド大）ではなく **`ThreadPoolExecutor`** を使用することで CPU 全コアを限界まで活用でき、**2,500〜4,200 fps** の爆速処理を達成。

### ✅ A3. Adaptive FPS (動的フレーム間引き)
- **理由**: 静止画シーンや会話字幕シーンで毎フレーム差分データを送るのは冗長。
- **効果**: 直前フレームとの変化ピクセル比率が 1.5% 未満の場合は空文字列 `""` を出力してスキップ。再生品質を一切落とさずに 1時間あたりの総容量をさらに **31% 削減 (48 MB/h)**。

### ✅ A4. 全50+色パレット & 5種ディザリング選択
- **理由**: 従来の 16色 Concrete だけでは実写動画やグラデーションでバンディング（縞模様）が発生。
- **効果**: 自発光ブロック（`sea_lantern`, `glowstone`, `shroomlight`, `froglights`）や Terracotta、Wool、鉱石ブロックを組み合わせた **39色/55色パレット** を構築。CIELAB 色空間 $\Delta E$ 計算と Floyd-Steinberg / Atkinson / Ordered (Bayer) ディザリングの選択により、**$\Delta E = 26.99$ の最高色精度** を達成。

### ✅ A5. 静的モジュールによる多タイトル遅延ロード (videos.js)
- **理由**: Minecraft Bedrock の Script API は動的 `import()` をサポートしない。
- **効果**: `videos.js` にて各動画データ `frames_{video_id}.js` を静的インポート・一覧オブジェクト化し、ゲーム内コマンド `/scriptevent <ns>:play <id>` で選択再生するアーキテクチャを確立。ワールド起動時のパース遅延を **0.05 秒（即時起動）** に抑圧。

---

## 2. 不採用・却下された決定事項 (Rejected)

### ❌ R1. Rectangle Merge (2D矩形結合アルゴリズム)
- **理由**: 隣接する同色ピクセルを矩形 `(x, y, w, h, color)` に結合するアルゴリズムを検証。画像変換時はブロック数が減るものの、JSON 内に5個の数値と配列記号 `[x,y,w,h,c],` が大量発生し、**最終的な JSON ファイルサイズが増大**したため却下。

### ❌ R2. RLE (Run-Length Encoding)
- **理由**: 連続する同色ピクセルを `(start_idx, length, color)` で符号化。動画の差分ピクセルは連続性が低く（散在する）、符号ヘッダーのオーバーヘッドで **JSONサイズが約 15% 肥大化**したため却下。

### ❌ R3. 3D-LUT (ルックアップテーブル) 減色
- **理由**: RGB空間を $64\times64\times64$ の立体格子に事前分割し、最近傍色を高速参照する手法。色空間テーブルの初期生成時間がかかり、Pillow Quantize (C言語実装) の方が圧倒的に高速（約4.5倍）であったため却下。

### ❌ R4. ProcessPoolExecutor (マルチプロセス並列)
- **理由**: Python で `ProcessPoolExecutor` を試算した結果、フレーム画像データや処理結果配列のプロセス間通信 (IPC Serialization) オーバーヘッドにより、`ThreadPoolExecutor` よりも **約 5.8 倍遅い**（スレッドプール: 2,558 fps vs プロセスプール: 438 fps）ことが実測証明されたため却下（Pillow が GIL を解放するためスレッドプールが最速）。

### ❌ R5. 動的 `import()` による遅延ロード
- **理由**: Bedrock Edition の JavaScript エンジン (QuickJS ベース) では ES Module の動的 `import()` ステートメントが禁止されており、実行時に `SyntaxError` が発生するため不採用。静的 `import` 組み合わせ構造で同等の遅延ロード効果を実現。
