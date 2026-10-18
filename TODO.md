# TODO & Future Roadmap

本ドキュメントでは、次の開発フェーズで取り組むべき推奨機能拡張、アイデア、およびバックログ課題を優先度別にまとめています。
Phase 8 の各項目の根拠 (計測結果・先行事例) は [docs/research/phase7x.md](docs/research/phase7x.md) を参照してください。

---

## 🔁 定期的な運用 (Operations)
- **2 週間に 1 回**: `python scripts/traffic_report.py` で閲覧数・参照元・ダウンロード数を確認 (GitHub は直近 14 日分しか残らない)。CurseForge の作者ページでダウンロード数とコメントを確認。
- **リリースごと**: [docs/TESTING.md](docs/TESTING.md) の「リリースごとの記録」に自動テスト・モック・EXE・実機の結果を書く。手順は [docs/RELEASING.md](docs/RELEASING.md)。
- **実機の計測を集める**: Windows 以外の端末での動作と、`/scriptevent <接頭辞>:stats` の結果を [docs/COMPATIBILITY.md](docs/COMPATIBILITY.md) と [docs/PERFORMANCE.md](docs/PERFORMANCE.md) に追記する (まだ Windows の `bench` しか無い)。

## 🔴 優先度: 高 (Phase 8: In-World Rendering & Perceptual Stability)

### ✅ 8.1 描画方式の自動選択 (実装済み)
- **概要**: `/scriptevent <接頭辞>:bench` の結果 (方式ごとの µs/ブロック と 1 回あたりの費用) をワールドに保存し、フレームごとに「呼び出し回数 × 1 回の費用 + ブロック数 × 1 個の費用」が最小の方式 (箱 / 座標リスト / 1 個ずつ) を選ぶ。
- **根拠**: モックでの計測では `list` の呼び出しは 1 フレーム 2〜33 回で `rect` の 1/10〜1/300。ただし 1 回あたりの費用は端末ごとに実機で測る必要がある。

### ✅ 8.2 ディザの時間的安定化 (実装済み: 条件付き更新 `--temporal-hysteresis`。詳細は docs/research/phase7x.md の 9 章)
- **概要**: 前フレームと同じパレット番号で色差 (OkLab ΔE) が許容範囲内なら変えない条件付き更新、ディザ模様を全体の動きに合わせてずらす動き補償付きディザ。
- **根拠**: Blue Noise ではディザ模様が画面に固定されるため、パン映像の削減率が −88% (ディザなし) → −43% に落ちる。

### ✅ 8.3 2.5D パラメトリック立体 (実装済み: `--volume-encoding`、既定で有効。詳細は docs/research/phase7x.md の 10 章)
- **概要**: `relief` / `extrude` / `inflate` を「色の平面 + 高さ (厚み) の平面」の 2 枚の 2D 映像として送り、アドオン側で列ごとに立体を組み立てる (自分か上下左右の値が変わった列だけ作り直す)。
- **結果**: 置くブロック・呼び出しは同じで、データは deflate 圧縮後 −54〜85%。

---

## 🟡 優先度: 中 (Medium Priority)

### ✅ 8.4 エントロピー符号化 (実装済み: `varint_rle_v6`、`--entropy` で既定。詳細は docs/research/phase7x.md の 11 章)
- **概要**: モード・位置・色を別々のストリームにして、GOP ごとの静的ハフマン (Smacker 型) か LZ4 型の一致検索をかける。
- **根拠**: xz で v5 のデータからさらに 22〜39% 縮む。QuickJS での復号時間 (512×512) を計測して判断する。

### ✅ 8.5 ブロックモードのレート歪み最適化 (実装済み: `--block-rdo`、既定の強さ 16。詳細は docs/research/phase7x.md の 12 章)
- **概要**: 8×8 ごとに 減色のまま / 前のフレームのまま / 動き補償の予測 / 単色 / 2 色 を、色差 + 強さ × (置く費用 + データ量) で選ぶ (Smacker・RoQ 型)。データ形式は変わらない。
- **結果**: Blue Noise でデータ −13〜−44%、rect の呼び出し −23〜−53%、描画時間の見積もり −8〜+1%、色差は 5 本中 4 本で改善。
- **見送り**: 回転・反転したタイルの参照は Blue Noise で 0〜4.6% (上限の見積もり) で、データ形式の変更に見合わない。
- **次の課題**: 立体・2.5D の色の平面への適用、強さを映像に合わせて自動で決める、変換の速さ (512×288 で 1 フレーム約 170 ms 増)。

### ⏸ 8.6 構造物とクローンによる描画 (調査の結果見送り。詳細は docs/research/phase7x.md の 13 章)
- **構造物**: 実機で 1 ブロック 2.31 µs (座標リストの 1.6 倍)、タイルの再利用も平均 1.1〜1.6 回で、得にならない。
- **`cloneBlocks`**: Script API 2.10.0 (Minecraft 26.50 以降) が必要。動きベクトルのブロックをコピーしても、コピーが 1 ブロック 0.5 µs なら描画時間 −1% 以下、0.1 µs でも −9% 以下 (`benchmark/clone_study.py`)。読み込める Minecraft の版を狭める代償に見合わない。
- **再検討の条件**: 実機で `cloneBlocks` が 1 ブロック 0.2 µs 以下と分かったとき。

### 8.7 ホログラムの素材と入力
- **概要**: ステンドグラス・発光ブロックの半透明パレット、AI による単眼深度推定 (Video-Depth-Anything など) で作った深度動画を `--depth-video` に使う手順、16 ブロック区画にそろえた設置 (再描画される区画を減らす)。

### GUI の Web アプリケーション化 (Web-based Converter)
- **概要**: Tkinter ベースのローカル GUI に加え、PyScript または WebAssembly (Wasm) + FFmpeg.wasm を用いて、ブラウザ上で完結する Web 版コンバーターを開発。

### 画面アスペクト比の自由自動調整 (Dynamic Aspect Ratio Fix)
- **概要**: 16:9 や 4:3 などの動画アスペクト比に合わせて、黒帯 (Letterbox) を出さずに済む最適な幅・高さを自動決定するプリセット (現状は指定サイズ内に中央寄せのレターボックス)。

---

## 🟢 優先度: 低 (Low Priority / Research)

### 8.8 実映像での評価
- **概要**: 権利上公開できる実映像のデータセットで、データ量・呼び出し回数・ゲーム内の tick 時間 (MSPT)・ちらつき (時間方向の変化率) を CI で継続計測する。合成映像は辞書の再利用を多く (または少なく) 見積もるおそれがある。

### 可変パレットの精度向上 (Per-Video Custom K-Means Block Mapping)
- **検証済み (Issue #3)**: OkLab でのクラスタリングを `--palette-space oklab` として実装し比較した ([docs/research/palette_space.md](docs/research/palette_space.md))。候補が 39 色しか無いため既定 (上限 64 色) では全ブロックが選ばれて差が出ず、16 色に絞ると映像によって良し悪しが分かれたので、既定は RGB のまま。
- **✅ 候補の拡充 (実装済み)**: 公式テクスチャの実測の平均色で 108 色に拡充 (`scripts/generate_block_colors.py`)。GUI の既定を自動パレット (光るブロックを除く 101 色から最大 64 色) にした。v6.4.0 の既定より色差 ΔE2000 が 18〜62% 減、データは Blue Noise で −3〜+30% (ディザなしのパンは 2.2 倍)。
- **未計測**: 光るブロックを置き替える費用 (明るさの再計算)、色が増えたときの `fillBlocks` の呼び出し数。実機で測れたら、光るブロックを自動の候補に入れるか決める。
- **次の課題**: 色数の上限 (`--palette-colors`) を映像に合わせて自動で決める。上面と側面の見た目が違うブロック (原木・柱など) を立体の側面に使う。

### ✅ MS-SSIM 指標の実装 (実装済み)
- **概要**: `benchmark/metrics/ms_ssim.py` に明るさの MS-SSIM (Wang 2003、5 段・小さい画像は段数を減らす) を実装。1 段では scikit-image の SSIM と一致することをテストで確認。

---

## ✅ 完了済み (Done)
- **Phase 7.x 次世代予測圧縮** (`varint_rle_v5`): 動き補償 (2D 8×8 ブロック + 全体探索、3D 8×8×8 ブリック)、暗黙のタイル辞書 (4 / 8 自動選択)、ラン位置の間隔符号化、フレームごとの方式の自動選択。v5.4.0 比でデータ 47〜88% 減
- **3D ホログラム再生**: `relief` (ヒステリシス付き) / `extrude` / `inflate` / MagicaVoxel `.vox` (シーングラフ・アニメーション)、等角投影のプレビュー (`scripts/preview_volume.py`)
- **まとめ置き描画**: `fillBlocks` の箱 (立体は奥行きにも伸ばす)・`ListBlockVolume`・1 個ずつ の切替、実機ベンチマーク (`bench`)、キーフレームで変わったブロックだけを置く、建築限界内への自動調整、常時読み込みの分割登録
- Motion Vector Prediction: 8×8 マクロブロックの動き補償。詳細は `docs/research/motion_vector.md` と `docs/research/phase7x.md`
- 音声同期再生 (10秒分割 OGG + Resource Pack 自動生成)
- ゲーム内リモコン GUI (コンパス使用で `ActionFormData` / `ModalFormData`)
