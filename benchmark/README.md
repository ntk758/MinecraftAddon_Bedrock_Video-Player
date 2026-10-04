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

MS-SSIM は未実装のため常に `nan` と表示されます。

## アーキテクチャ
本フレームワークはモジュール化されており、新しい評価指標やデータセットを容易に追加できます。
