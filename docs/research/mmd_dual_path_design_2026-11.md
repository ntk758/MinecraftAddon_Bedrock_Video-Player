# MMD の 2 つの経路: 骨で動かす人物 (Block Mode) と、描いた動画 (Rendered Mode) — 設計 (2026-11-23)

この文書は、MMD の人物を高解像度にする作業と、PMX・VMD・音声から動画を描いて画像モードで流す新しい経路の設計です。
Phase 0 (監査) で確かめたことを先に書き、そのあとに決めたことを書きます。実機 (Minecraft の中) で確かめていないことは「未確認」と書きます。

## 1. 今の作り (Phase 0 の監査)

```
MMD (.pmx / .vmd / .bvh)
├── mvcodec/mmd.py           PMX・VMD を読む、Poser (FK・IK・付与)、モーフ、カメラ、skin_vertices、mmd_to_vox
├── mvcodec/mmd_physics.py   物理 (light = 鎖、standard / full = XPBD の剛体)
├── mvcodec/bvh.py           BVH (CMU など) を VMD に移し替える
│
├── ブロックモードの立体 (.vox → convert.py)         mmd.mmd_to_vox。ブロックで描く立体の動画 (今回は変えない)
│
└── 画像モードの「骨で動かす人物」(この文書の Block Mode)
    ├── image_mode/rig.py        初期姿勢をボクセルにし、ボーンごとの部品に分ける (MAX_VOXELS = 512)
    ├── image_mode/rig_face.py   表情を部品の切り替えにする
    ├── image_mode/rig_anim.py   PosedDance (全コマの姿勢)、Bedrock のアニメーション、カメラ
    ├── image_mode/planes.py     ボクセルの面を板 (厚み 0 の直方体) にし、シートに並べる
    └── convert_image_mode.py    mmd_glass_entry → build_glass_files → .mcaddon

画像モード (テクスチャで動画を流す)
├── image_mode/atlas.py      ffmpeg でコマを読み、シート (PNG) に並べる。memory_report (Tier の予算)
├── image_mode/budget.py     予算に入るよう、大きさ・fps・長さのどれを下げるか (quality / smooth / length)
├── image_mode/rp.py         geometry・render controller・client entity (方式 a: 1 マス 1 ボーン + part_visibility)
├── image_mode/dedup.py      同じコマをまとめる
└── image_mode/image_main.js 再生の制御 (時計、seq、音のチャンク、カメラ)
```

監査で確かめたこと:

- 画像モードの動画は、全部のコマをシートに並べ、client entity の `textures` に全部のシートを書いて、`Array.sheets[v.sheet]`
  で選ぶ。つまり **再生する全部のコマが、アドオンのテクスチャとして最初から読み込まれる** 前提で作ってある
  (SPEC.md)。Molang から外のファイルを読む手段は無い。
- テクスチャの量は Microsoft Learn「Texture Budgets」の数え方 (テクセル × 4 バイト) で、アドオンの予算は
  Tier 1 150 MB・Tier 2 225 MB・Tier 3 300 MB・Tier 4 600 MB・Tier 5 800 MB (atlas.TIER_BUDGETS_MB)。同じページは
  「テクスチャをいつ読み込むか」「途中で捨てるか」を書いていない (2026-11-23 に読んだ)。
- 同じく Learn の「Building Sub-Packs」: リソースパックの `subpacks` (folder_name・name・memory_tier、試験中の
  memory_performance_tier 1〜5) は、端末の Tier を超えない一番上のものが自動で選ばれ、プレイヤーは 1 段下にも変えられる。
  サブパックのファイルは本体のファイルを上書きする。Tier と端末の表: Tier 1 Switch、Tier 2 PS4・Xbox One・スマートフォンの 60 %、
  Tier 3 Xbox One X・Series S、Tier 4 Series X・PS5、Tier 5 PS5 Pro・PC の 70 %。memory_tier は 1 つ 250 MB の RAM。
- この PC: Intel 24 コア 32 スレッド、RAM 31.7 GB、AMD Radeon RX 9070 XT。Blender・MMD・MMD Tools は入っていない。
  Python には numpy 2.4・Pillow・scipy・psutil があり、OpenGL・moderngl・pyrender・numba は無い。FFmpeg 9.0.1 (libx264・h264_amf)。

## 2. 今の作りの問題

1. 人物の細かさは `--figure-detail` (standard 256×1・high 256×2・max 384×2) と `--figure-voxels` しか無く、端末の重さに合わせた段が無い。
   384・512 の重さ (時間・RAM・テクスチャ・板の数) を測ったことが無い。
2. 高解像度の MMD の動画を作る経路が無い (今あるのは、骨で動かす人物と、ブロックの立体だけ)。
3. 画像モードは全部のコマを読み込むので、**720p 30 fps を長く流すことはできない** (下の 4 章)。これは作り方ではなく
   Bedrock の予算の問題で、ストリーミングで逃げられる証拠も無い。
4. 端末ごとに大きさを変える仕組みが無い (1 つのアドオンに 1 つの大きさ)。

## 3. 決めたこと

### 3.1 Block Mode (骨で動かす人物) の高解像度化

- 細かさの段 `--figure-preset` を足す。`--figure-detail` (standard・high・max) は今のまま残し、意味も変えない
  (古い設定と Windows 版の選択が同じ人物を作るように)。優先は `--figure-voxels` > `--figure-preset` > `--figure-detail`。

  | preset | ボクセル | 板の画素 | 向けの端末 |
  |---|---|---|---|
  | mobile | 64 | 2 | 弱い端末 |
  | standard | 128 | 2 | 標準 |
  | high | 192 | 2 | 強い端末 |
  | ultra | 256 | 2 | 強い PC (今の既定 `--figure-detail high` と同じ人物) |
  | experimental | 384 | 2 | 試験 |
  | extreme | 512 | 1 | 重さの検証 (上限の 512 は上げない) |

  板の画素は、計測で 4096 のシートと予算に入る値にする (6 章の結果で決める)。
- 細かさを上げるほど、細い所 (指・髪の先) が残る。改善は計測で一番効く所を選ぶ (6 章)。

### 3.2 Rendered Mode (描いた動画)

```
PMX + VMD (+ 表情の VMD・カメラの VMD・BVH) + 音声
  → PosedDance (mvcodec.mmd の Poser・IK・物理・モーフ。人物と同じ物を使う)
  → mvcodec/mmd_render.py (numpy のソフトウェアラスタライザー)
  → PNG の連番 (作業フォルダー。途中から再開できる)
  → FFmpeg で MP4 (音声を入れる。h264_amf、無ければ libx264)
  → convert_image_mode.py の普通の動画として画像モードのアドオン (予算に合わせて縮める)
```

- **描き手**: この PC には Blender も MMD Tools も無く、確かめられない物は入れない (依頼の条件)。そこで、
  すでにある PMX の読み込み・姿勢・IK・物理・モーフ・カメラを使い、numpy だけで三角形を描く描き手を作る。
  新しい依存は無い。Blender + MMD Tools は、入っていれば使える別の描き手として後で足せるよう、描き手を関数 1 つで呼ぶ形にする
  (今回は作らない。動かせない物を「対応」と書かない)。
- 描くもの: 頂点の変形 (BDEF1・BDEF2・BDEF4。SDEF は BDEF2、QDEF は BDEF4 として扱う)、頂点モーフ、ボーンモーフ、IK、物理、
  カメラの VMD (無ければ全身が入る固定のカメラ)、材質の色・テクスチャ・透明 (アルファテストと、半透明の重ね)、
  両面の印、トゥーンの陰 (共有トゥーンは MMD 本体の画像なので、2 段の陰で近づける。モデルのトゥーンの画像があれば使う)、
  スフィア (乗算・加算)、鏡面、輪郭線 (エッジの印・太さ・色)、背景の色か画像、アンチエイリアス (縦横 N 倍で描いて縮める)。
- 描かないもの (はっきり書く): セルフシャドウと地面の影、UV モーフ・材質モーフ、MME のエフェクト、追加 UV、SDEF の正確な変形、
  ステージの物理。
- 段:

  | preset | 大きさ | fps |
  |---|---|---|
  | mobile | 854×480 | 24 |
  | standard | 1280×720 | 30 |
  | high | 1920×1080 | 30 |
  | ultra | 2560×1440 | 30 |
  | experimental | 3840×2160 | 30 |

- 再開: コマは `frame_000123.png` に、書き終えてから名前を付ける (書きかけのファイルを残さない)。再開のときは
  開いて大きさが合うコマだけを使い、壊れたコマは描き直す。設定 (モデル・モーション・大きさ・fps など) が変わったら、
  作業フォルダーの `render.json` と比べて、混ぜずに描き直す。
- FFmpeg が失敗したら止める (成功にしない)。でき上がった MP4 は、長さとコマの数を読み直して確かめる。

### 3.3 画像モードとの統合

- 描いた MP4 は、画像モードの普通の動画として `convert_image_mode.py` に渡す。シート・音声のチャンク・時計・
  スクリプトは今のまま使う (互換のため、画像モードの中は変えない)。
- 予算に入らない分は `budget.plan` (quality・smooth・length) で下げる。描いた動画の大きさのまま Minecraft に入るとは限らない
  (4 章の表)。
- **端末ごとの大きさ: サブパック**。`--subpack-tiers 2,5` で、Tier ごとに大きさの違うシートと RP のファイル
  (geometry・render controller・client entity・テクスチャ) を `RP/subpacks/tier<n>/` に作り、RP の manifest に `subpacks`
  を書く。fps・コマ数・音声は全部の Tier で同じにする (BP とスクリプトは 1 つで済む)。端末は自分の Tier に合うものを自動で選び、
  プレイヤーは設定で 1 段下にも変えられる (Learn)。存在しない API で端末を見分けることはしない。
  memory_performance_tier は試験中 (manifest V3) なので書かず、ずっと前からある memory_tier (250 MB 単位の RAM) を書く:
  Tier 1 → 0、Tier 2 → 12、Tier 3 → 18、Tier 4 → 24、Tier 5 → 32 (Learn の表の RAM の下限)。
  **未確認**: 実機でサブパックが選ばれること、サブパックの entity・geometry・render controller が使われること。

## 4. メモリの予算 (RGBA 8bit、展開したあと)

1 コマのバイト数と、アドオンの予算に入るコマの数 (シートの隙間は数えない上限):

| 大きさ | 1 コマ | Tier 1 (150) | Tier 2 (225) | Tier 3 (300) | Tier 4 (600) | Tier 5 (800) |
|---|---|---|---|---|---|---|
| 854×480 | 1.56 MiB | 95 | 143 | 191 | 383 | 511 |
| 1280×720 | 3.52 MiB | 42 | 64 | 85 | 170 | 227 |
| 1920×1080 | 7.91 MiB | 18 | 28 | 37 | 75 | 101 |
| 2560×1440 | 14.06 MiB | 10 | 16 | 21 | 42 | 56 |
| 3840×2160 | 31.64 MiB | 4 | 7 | 9 | 18 | 25 |

30 fps なら、720p は Tier 5 でも 7.6 秒、1080p は 3.4 秒しか入らない。40 秒の踊りを 30 fps のまま入れると、Tier 2 で
1 コマ約 196 KB (約 256×190)、Tier 5 で約 700 KB (約 448×252) になる。PNG・JPEG のファイルの大きさは GPU のメモリを減らさない。

ストリーミングの方式の比較:

| 方式 | Bedrock でできるか | メモリ |
|---|---|---|
| 複数のアトラス (今の作り) | できる | 全部のコマ |
| 時間ごとのチャンク (エンティティを分ける) | できる | テクスチャを使うときだけ読むなら減るが、そう書いた文書は無い (未確認) |
| 決まった数のコマのバッファ / 要る所だけ読む / 先読みと解放 | できない (Molang・スクリプトからテクスチャを読み込む API が無い) | — |
| 同じコマの使い回し (dedup) | できる (今ある) | 踊りではほとんど減らない |

→ 今回は「全部読み込む」前提のまま、Tier ごとのサブパックと予算の計画で大きさを決める。チャンクの遅延読み込みは、実機で
メモリを測る試験が要る (11 章)。

## 5. 実装の順番とテスト

1. 人物の段 (`--figure-preset`) と計測のスクリプト (`scripts/bench_mmd_figure.py`)。
2. 描き手 (`mvcodec/mmd_render.py`)、コマンド (`scripts/render_mmd.py`)、再開・FFmpeg・確かめ。720p 30 fps から。
3. サブパック (`--subpack-tiers`) と、描いた動画からアドオンまで (`render_mmd.py --addon`)。
4. テスト: 描き手 (人形のモデルで形・色・表情・透明・輪郭線・カメラ)、再開、壊れたコマ、FFmpeg の失敗、
   音声と映像の長さ、サブパックの manifest と中身、人物の段。今のテストを全部通す。
5. 計測 (6 章)、文書。

## 6. 計測 (PC)

(計測の結果は docs/research/mmd_dual_path_benchmark_2026-11.md に書く)

## 11. 未確認のこと (実機が要る)

- Minecraft の中の fps・読み込み時間・長く流したときの安定。スマートフォン・コンソール。
- サブパックが Tier で選ばれること。
- テクスチャを使うときだけ読み込むか (チャンクで減らせるか)。
