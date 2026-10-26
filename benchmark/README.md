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

`cloneBlocks` で動きベクトルのブロックをコピーしたときの得の見積もりは `clone_study.py` です (結果は docs/research/phase7x.md の 13 章)。

## 実映像での評価 (`real_video_bench.py`、Phase 8.8)

`benchmark/datasets/real_videos.json` の実映像で、データ量・画質 (ΔE2000・MS-SSIM)・ちらつき・ゲーム内の描画時間の見積もり・変換時間を測ります。映像はリポジトリに入れず、初回に公式の配布元から取得して `--cache` (既定 `<out>/videos`) に置きます。`--baseline` に git の版を渡すと並べて比べます。週 1 回の CI (`.github/workflows/real-video-bench.yml`) でも最新のタグと比べています。

```bash
python benchmark/real_video_bench.py --out bench_out --baseline v6.7.0
```

使っている映像 (どれも CC-BY 3.0):
- Tears of Steel — (CC) Blender Foundation | mango.blender.org
- Sintel (予告編) — © copyright Blender Foundation | durian.blender.org
- Big Buck Bunny (予告編) — (c) copyright 2008, Blender Foundation | www.bigbuckbunny.org

## だまし絵の前後比較 (`stage_bench.py`)

立体のだまし絵 (`--view anamorphic`) の「投影・描き直し・展開・置く」の重さを、git の版 (既定 `main`) と作業中の木で比べます。出力が同じはずの変更 (投影の高速化など) では、盤面が 1 マスも違わないことも確かめます。node と ffmpeg が必要です。

```bash
python benchmark/stage_bench.py --out D:/work/stage_bench                       # main と作業中の木を比べる
python benchmark/stage_bench.py --out D:/work/stage_bench --baseline v6.28.0    # 別の版・タグ。git worktree のフォルダも可
python benchmark/stage_bench.py --out D:/work/stage_bench --baseline worktree   # 同じ版どうし (ノイズの大きさを測る)
python benchmark/stage_bench.py --out D:/work/stage_bench --addon my_mmd80.mcaddon   # ローカルの第三者のアドオンも測る
python benchmark/stage_bench.py --out D:/work/stage_bench --quick               # 確認用 (mmd40 を 20 フレームだけ、数秒)
```

`--out` はリポジトリの外にします (中だと拒否します)。結果は `<out>/results.json` と `<out>/results.md`、作った素材は `<out>/fixtures`、取り出した版は `<out>/trees` に残ります。`--baseline` の git の版は `main.js` と `codec.js` だけを `git archive` で取り出すので、コミットしていない変更は含まれません (素材は作業中の木の `convert.py` で 1 回だけ作り、両方の版で同じデータを再生します)。

素材 (`--fixtures`、既定は全部。乱数の種は固定で、毎回同じものができます):

| 素材 | 中身 |
|---|---|
| `mmd40`・`mmd80` | `tests/mmd_fixture.py` の箱人形を踊らせ (`mmd_to_vox`、高さ 40・80)、GUI の MMD のだまし絵と同じ引数で変換。表面だけの殻なので中が空洞になる (変換は閉じた空洞を埋める) |
| `solid56` | コードで作る、中身の詰まった人型 (高さ 56、腕と脚が動く) の `.vox` |
| `person80` | ffmpeg で作った白い人型の動画を人物の範囲にした inflate (80x45、奥行き 16) |
| `person80_bg` | `person80` に testsrc2 の背景の映像 (後ろの壁の大きさ、`--fit cover`) を隠し項目として足したもの |

測るもの (素材ごと。`--repeats` 回 (既定 5) の中央値・最小・最大と、baseline からの Δ%):

- `micro` (`benchmark/js/stage_micro.mjs`、node `--jitless`): 同じプロセスで baseline と作業中の `codec.js` を 1 回ごとに交互に呼ぶ。変化のあるフレーム (最大 24) × 目 3 か所 (座標は整数でない) の投影 ms、目を (+0.3, +0.05, −0.1) 動かした描き直し ms、全フレームの `decodeFrameInto` ms (タイル辞書あり、main.js と同じ手順)、と、2 つの版で盤面が食い違うマスの数 (投影・描き直し・展開)。
- `still`・`walk` (`benchmark/js/stage_play.mjs`): モックの上で `main.js` を最後まで再生する。置く費用はゲーム内の実測値 (setBlock 2.3 µs、fillBlocks 15 µs + 0.55 µs/ブロック、座標リスト 15 µs + 1.42 µs/ブロック)。目は `still` = 舞台の正面の決まった位置、`walk` = 1 tick に 0.2 ブロックずつ舞台の中を往復。毎回新しい一時フォルダで動かし、`stats_load` の 4 つの数 (遅れたフレーム・最大の遅れ・1 フレームの平均と最大の ms)、tick の ms (平均・p95・最大)、JS の時間と置く費用の見積り、最後の盤面の SHA-1 (再生が終わった後に目を正面へ戻して描き直しが落ち着いてから取るので、目の動かし方によらず最後のフレームの絵で比べられる) を出す。
- 「ゲーム内の見込み」の列は、worktree の中央値 × 3.1〜4.3 です。モックとゲーム内の時間の比 (これまでの実測の範囲) を当てはめた**見積りで、実測ではありません**。素材は実在の MMD より見える面が少ないので (箱人形)、実在のモデルは `--addon` でローカルにだけ測ります。

### ノイズの大きさと使い方の注意

- 同じ版どうし (`--baseline worktree`) を、何も動かさない機械で 2 回続けて動かしたときの差 (2026-10-26、node v24、Windows 11、5 回の中央値どうし): 通し再生の 1 フレームの平均 ms は素材と目の動かし方によらず最大 ±4.4%、micro の baseline と worktree の差は最大 ±2.5%、micro の 2 回の間の差は最大 ±4.3%。盤面の食い違いは 0。**同じ版どうしで ±10% 以内**を目安にし、それより小さい差は信じないでください。
- ほかの重い処理 (変換・AI の切り抜き・別のベンチマーク・ブラウザの動画など) と同時に動かさないでください。重い処理と同時に動かすと、同じ版でも ±40〜60% ぶれました。交互の A/B と 5 回の中央値は、ゆっくりした揺れを消すためのものです。
- 1 回目の実行は 2 回目より数 % 遅くなることがあります (キャッシュ)。前後を比べるときは、同じ実行の中で baseline と worktree を比べてください。
- 時間が決まるのは `main.js` の tick の予算 (`TICK_TIME_BUDGET_MS`) が実時間を見るためで、遅れたフレームの数は機械が遅いほど増えます。
- 第三者の動画・MMD・アドオン (`--addon`) から作ったものは、画像・マスク・アドオンを含めて、リポジトリに書かない・コミットしない・公開しないでください。`--addon` は `--out` の下に展開し、残すのは数値だけです。

### 投影が厳密なことのテスト

`tests/test_stage_exact.py` (`tests/js/stage_exact.mjs`) は、乱数 (種は固定) で作った立体 (中が空洞の殻・薄い板・ばらばらの点・詰まった塊・細い棒、24×40×8 と 40×80×16) を、座標が整数でない目 20 か所 (正面・横・高い位置・ブロックの面のすぐ外) から写し、盤面の各マスの中心から目への線分を 3D DDA でたどった正解と食い違うマスが 0 であることを確かめます。目が人物のブロックの並びの中や後ろにあるときは数マスが抜けることが分かっていて (既知)、その確認は `xfail` にしてあります。写る範囲の求め方の近道 (床・後ろの壁・左の壁・右の壁のどれか 1 つにしか写らないブロックと、それ以外の一般の場合) は、それぞれのブロックを多く含む目 (`stage_exact.mjs --path floor|back|left|right|general`) で確かめます。外から見える面を持つブロックの一覧を作る処理 (空気だけの区間の読み飛ばし) は `tests/js/stage_surface.mjs` が、図形の置き場所・空気の番号・NaN のビット列を変えて、ブロックを 1 つずつ調べた正解と比べます。だまし絵の変換が埋める閉じた空洞 (`fill_enclosed_air`) は、埋めても投影が 1 マスも変わらず、調べる面だけが減ることを確かめます。`tests/test_stage_bench.py` は `stage_bench.py --quick` の smoke テストです。

## AI の人物の切り抜きの測定 (`person_bench.py`、ローカル専用)

`mvcodec/person.py` の切り抜きを、構成 (版 × メモリの段階) ごとに新しいプロセスで動かして、速さ・メモリ・マスクの安定性を測ります。CI では動かしません (モデルと GPU が要る)。動画は `--video` で渡すだけで、リポジトリには何も書きません (マスクと背景の動画は、数値を出した後に消します)。

```bash
set BVP_MODEL_DIR=D:/work/models        # モデルの置き場 (あれば)
python benchmark/person_bench.py --out D:/work/person --video clip.mp4 --tree baseline --tree worktree --profiles gpu --duration 30 --vram
```

測るもの: wall 時間と ms/フレーム、ピークの working set と private (psutil、200 ms ごと)、`--vram` で GPU の専用メモリの最大 (Windows の `Get-Counter`)。マスクは 80×45 にボックス平均で縮めて 0.5 以上をブロックとし、空のフレーム (ブロック単位・面積 0.5% 未満)、1 フレームあたりの変わるマスと 1 フレームだけ点滅するマス (場面の切り替わりをまたぐ変化は含めない)、大きさの跳び (隣り合うフレームの |Δlog 面積| の p90) を出します。場面の切り替わりは元の動画の構図で決めます。
