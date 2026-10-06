# Performance Benchmarks & Quality Evaluation

> **お知らせ:** 
> Phase 7 (Research Edition) より、ベンチマークに関する詳細なドキュメントと測定フレームワークは `benchmark/` ディレクトリに統合・移行されました。
> 
> 詳細は [benchmark/README.md](benchmark/README.md) をご参照ください。

## データ量と描画コスト (Phase 7.x)

`benchmark/codec_bench.py` は合成映像を作り、指定した git の版 (既定 `main`) と作業中の版でデータ量 (出力 .js・deflate 後) と、モックの Minecraft で再生したときの API 呼び出し数・置いたブロック数を比べます。

```bash
python benchmark/codec_bench.py --out bench_out --sizes 128x72 256x144
python benchmark/codec_bench.py --out bench_out --suite volume
```

結果は README の「📊 ベンチマーク (Phase 7.x)」と [docs/research/phase7x.md](docs/research/phase7x.md) にまとめています。
