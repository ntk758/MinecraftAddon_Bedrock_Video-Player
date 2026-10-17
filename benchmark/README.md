# 統合ベンチマークフレームワーク (Benchmark Framework)

Phase 7 (Research Edition) にて導入された、マルチモジュール型の統合ベンチマークフレームワークです。

## 概要

動画変換における画質劣化と圧縮効率のトレードオフを正確に評価するため、単なる色差（$\Delta E$）にとどまらず、人間の知覚特性に近い複数の評価指標を網羅的に計測します。

## 測定指標 (Metrics)

- **PSNR (Peak Signal-to-Noise Ratio)**: ピクセル単位の物理的な誤差（MSE）を対数スケールで評価します。
- **SSIM (Structural Similarity)**: 局所的な輝度、コントラスト、構造の類似性を評価し、人間の視覚的品質と高い相関を持ちます。
- **LPIPS (Learned Perceptual Image Patch Similarity)**: 深層学習ベースの視覚的類似度。より人間の直感に近い画質評価が可能です。
- **$\Delta E_{2000}$**: 色空間における色差。人間の知覚に最も近い色差モデルを使用します。

## 使用方法 (Usage)

リポジトリのルートで `benchmark/run.py` を実行します。出力ファイルが無ければ `convert.py` で変換してから、ゲーム内と同じ規則で盤面を復元し (`mvcodec/decode.py`)、元動画と同じ解像度・fps で比較します。

```bash
pip install -r requirements-benchmark.txt
python benchmark/run.py --video input.mp4 --output out.js --fps 10
```

### オプション
- `--fps`: 変換時の fps (比較には出力ファイルに記録された fps を使います)。
- `--force`: 出力ファイルがあっても変換し直します。
- `--deep`: LPIPS も測定します (`torch` と `lpips` が必要)。

MS-SSIM は明るさについてのマルチスケール SSIM (Wang 2003、5 段。小さい画像では段数を減らす) です。ディザの細かい粒は粗いスケールで平均されるので、SSIM より人の見た目に近い値になります。

## アーキテクチャ
本フレームワークはモジュール化されており、新しい評価指標やデータセットを容易に追加できます。

## データ量と描画コストの比較 (`codec_bench.py`)

画質ではなく「データの大きさ」と「ゲーム内で置く回数」を、別の git の版と比べます。合成映像 (パン・アニメ風・白黒シルエット・ffmpeg の testsrc2 / mandelbrot) を自動で作るので、入力の動画は不要です。node (モックの Minecraft で `main.js` を再生) と ffmpeg (testsrc2 / mandelbrot) を使います。

```bash
python benchmark/codec_bench.py --out bench_out                                  # 平面、main と比較
python benchmark/codec_bench.py --out bench_out --baseline v5.4.0 --sizes 256x144
python benchmark/codec_bench.py --out bench_out --suite volume                   # 立体 (--volume-baseline の版と比較、既定 96e1f14)
```

`<out>/results_<suite>.md` に、版・設定ごとの出力 .js のバイト数、deflate 後のバイト数、1 フレームあたりの API 呼び出し数 (`calls_render=rect` など) と置いたブロック数が出力されます。

## ブロックモードの最適化の強さ (`rdo_bench.py`、Phase 8.5)

`--block-rdo` の強さを変えて同じ合成映像を変換し、画質 (ΔE2000・ぼかした ΔE2000・MS-SSIM)、deflate 後のデータ量、モックで rect / list 方式で再生したときの呼び出し数・置いたブロック数と、アドオンの費用のモデルで見積もった描画時間を並べます。

```bash
python benchmark/rdo_bench.py --out bench_out --strengths 0 16 32
```

タイル辞書に回転・反転の参照を足したときの効果の見積もりは `tile_orientation_study.py` です (結果は docs/research/phase7x.md の 12 章)。
