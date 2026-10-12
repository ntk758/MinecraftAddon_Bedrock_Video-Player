# Performance Benchmarks & Quality Evaluation

> **お知らせ:** 
> Phase 7 (Research Edition) より、ベンチマークに関する詳細なドキュメントと測定フレームワークは `benchmark/` ディレクトリに統合・移行されました。
> 
> 詳細は [benchmark/README.md](benchmark/README.md) をご参照ください。

## データ量と描画コスト (Phase 7.x)

`benchmark/codec_bench.py` は合成映像を作り、別の git の版 (平面は `--baseline`、既定 `main`。立体は作業中の版の送り方 (ボクセル / 2.5D の平面) どうし。`--volume-baseline 96e1f14` で 3D 動き補償を入れる前の版とも比べられる) と作業中の版でデータ量 (出力 .js・deflate 後) と、モックの Minecraft で再生したときの API 呼び出し数・置いたブロック数を比べます。

```bash
python benchmark/codec_bench.py --out bench_out --sizes 128x72 256x144
python benchmark/codec_bench.py --out bench_out --suite volume
```

結果は README の「📊 ベンチマーク (Phase 7.x)」と [docs/research/phase7x.md](docs/research/phase7x.md) にまとめています。

## 変換速度・メモリ / 画質 (色空間)

- 変換速度・最大メモリ・VRAM を実行環境と一緒に記録: `python benchmark/convert_perf.py --out bench_out`
- 自動パレットの色空間・色数の比較 (ΔE2000・MS-SSIM・ちらつき・データ量): `python benchmark/palette_bench.py --out bench_out`

結果と、変換速度とゲーム内の再生性能の違いは [docs/PERFORMANCE.md](docs/PERFORMANCE.md) にまとめています。
