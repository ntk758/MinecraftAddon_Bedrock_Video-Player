# 画像（テクスチャ）再生方式 / Image (texture) playback mode

ブロック方式（`main.js` がブロックを並べて描く方式）とは**別の方式**として、フレームの PNG をテクスチャとして
Resource Pack に同梱し、ゲーム内で切り替えて映像のように見せる方式の実験を追加しました。
既存の `convert.py` / `main.js` / `codec.js` / `pack_builder.py` は変更していません（別方式として独立しています）。

## 方式（承認済みの 方式1a）

1. Behavior Pack のエンティティ定義に、フレーム番号の int の**エンティティプロパティ**を宣言する
   （`bvpimg:frame`, `range: [0, フレーム数-1]`, `client_sync: true`）
2. Script API の `Entity.setProperty("bvpimg:frame", n)` でフレーム番号を書く
   （1 フレーム = `ticksPerFrame` tick。20fps = 1 tick に 1 フレーム）
3. Resource Pack の render controller がテクスチャの配列から値で選ぶ
   （`arrays.textures.Array.frames` → `textures: ["Array.frames[q.property('bvpimg:frame')]"]`）
4. 画面のジオメトリは 1 枚の平面（前後の面に同じ画像）で、`texture_width` / `texture_height` をフレームの大きさに合わせる

本実装では**動画ごとに 1 つのエンティティ種別** `<namespace>:<動画ID>` を使い、それぞれ別の render controller とテクスチャのフォルダを持ちます。エンティティプロパティは 1 種別あたり 1 個だけ（`<namespace>:frame`）なので 32 個の上限に収まります。画面の位置はワールドの動的なプロパティ `<namespace>:screens`（動画 ID → 位置の JSON）、再生している動画の一覧は `<namespace>:playing`（動画 ID の配列の JSON）に保存します。複数の動画を同時に再生できます（動画ごとに 1 画面・1 本の `system.runInterval`）。

マルチプレイでは 1 つのエンティティを全員が見るので、サーバーが書く値は 1 動画あたり 1 回/tick です。

## 作ったファイル

| ファイル | 内容 |
|---|---|
| `image_mode/frame_extract.py` | ffmpeg で動画からフレームの PNG を書き出す（解像度・fps・枚数）。`ticks_per_frame` と PNG の大きさを読むヘルパーもここ |
| `image_mode/pack_docs.py` | BP のエンティティ定義・RP のクライアントエンティティ・render controller・ジオメトリ・テクスチャの配置を作る |
| `image_mode/image_poc.py` | 1 動画だけの PoC の `.mcaddon`（`pack_docs` / `frame_extract` を使う） |
| `image_mode/build_image_addon.py` | 複数動画対応の本実装の `.mcaddon`（動画ごとに 1 エンティティ・別のテクスチャフォルダ） |
| `image_mode/scripts/image_poc_main.js` | PoC のスクリプト（出す・再生・停止・1 枚表示） |
| `image_mode/scripts/image_main.js` | 本実装のスクリプト（動画の選択・再生・停止・次の動画・1 枚表示・除去） |
| `tests/test_image_mode_poc.py` | PoC の `.mcaddon` の中身の形のテスト |
| `tests/test_image_mode_addon.py` | 複数動画の `.mcaddon` の中身の形のテスト（ffmpeg が無い環境では実抽出のテストは skip） |

`convert.py` / `main.js` / `codec.js` / `pack_builder.py` は変更していません（画像方式は独立したモジュールで zip を組む）。

生成されるパックの中身（`dist/` は `.gitignore` の対象なのでコミットしません）。PoC は 1 動画 = `screen`:

```
BP/manifest.json            @minecraft/server のみ依存 (server-ui は使わない)
BP/entities/screen.json     エンティティプロパティの宣言
BP/scripts/main.js          PoC のスクリプト
RP/manifest.json
RP/entity/screen.json       フレームの PNG を短い名前 (f0..fN) で並べる
RP/render_controllers/screen.json  テクスチャの配列を q.property で選ぶ
RP/models/screen.geo.json   画面の平面 (aspect ratio はフレームと同じ)
RP/textures/bvpimg/screen/frame_0.png ... frame_N.png
```

本実装（複数動画）は動画 ID ごとに別のファイルを持つ:

```
BP/entities/<動画ID>.json        動画ごとに 1 エンティティ種別 bvpimg:<動画ID>
BP/scripts/main.js               画像方式のスクリプト
BP/scripts/image_videos.js       動画の一覧 IMAGE_VIDEO_LIST (id / title / frame_count / width / height / fps / ticks_per_frame)
RP/entity/<動画ID>.json
RP/render_controllers/<動画ID>.json
RP/models/<動画ID>.geo.json
RP/textures/bvpimg/<動画ID>/frame_0.png ... frame_N.png
```

## PoC の作り方

```
python image_mode/image_poc.py --frames 12 --width 192 --height 108 --fps 20 -o dist/ImagePoC.mcaddon
```

- `--frames` フレームの数（既定 12）
- `--width` / `--height` フレームの解像度（既定 192x108）
- `--fps` 再生速度。20 が上限（1 tick に 1 フレーム）。`--fps 10` なら 2 tick に 1 フレーム
- `--input <動画>` 指定するとその動画の最初のフレームを使う。無いときは ffmpeg の `testsrc`（動くテスト映像）
- `--keep-frames <フォルダ>` フレームの PNG を残す（パックの中身を見たいとき）

## 本実装の作り方（複数動画）

```
python image_mode/build_image_addon.py --video badapple=badapple.mp4 --video intro=intro.mp4 \
    --width 256 --height 144 --fps 20 --limit 600 -o dist/VideoPlayerImages.mcaddon
```

- `--video <ID>=<動画のファイル>` 繰り返すと複数動画になる。ID は小文字の英数字と `_`（1〜32 文字）。エンティティ名・テクスチャのフォルダ・コマンドの enum の値にそのまま使う
- `--sample <数>` 動画のファイルが無いときに ffmpeg の `testsrc` で試作する（`--video` とは併用できない）
- `--width` / `--height` フレームの解像度（既定 256x144）。`scale` は `force_original_aspect_ratio=decrease` なので、元の動画が縦長なら指定より小さくなる（そのときは stderr に警告を出す）
- `--fps` 再生速度。20 が上限（1 tick に 1 フレーム）。`--fps 6` なら 3 tick に 1 フレーム
- `--limit` 1 動画のフレーム数の上限（既定 600、このコードの上限 2000）。テキストの総量は出力の見積り MiB を表示し、Resource Pack の Tier3（600 MiB）を超えれば警告を出す
- `--screen-blocks` 画面の幅（ブロック数、既定 4）。高さはフレームの aspect ratio から決まる
- `--namespace` / `--pack-name` / `-o` は PoC と同じ考え方

ブロック方式のアドオンと同じ 1 つにまとめるには `pack_builder.py` の変更が必要なので、今は**別のアドオンとして並べる**（統合は大きい変更なので事前に相談します）。

## Windows 版（exe）での使い方

画面の設定に「画像（テクスチャ）再生方式も作る」のチェックボックスを追加した（既定は off。ブロック方式の作り方は変わらない）。

- チェックすると、ブロック方式のアドオンを作るの**同じ手順の後**に、画像方式のアドオンを `<出力の名前>_images.mcaddon` に作る（例: `VideoPlayer.mcaddon` → `VideoPlayer_images.mcaddon`）
- 接頭辞はブロック方式に `_img` を足す（`badapple` → `badapple_img`）。同じ接頭辞だと 2 つのアドオンの独自コマンドの名前が衝突する
- パック名は `<パック名>_images`、説明文は画面の言語の `pack.image_desc` を使う
- 対応するファイル: 動画（`.mp4` など）・1 枚の画像（`.png` / `.jpg`）。立体（`.vox`）と MMD（`.pmx`）は元の映像のコマが無いので使えず、ログに理由を書く
- フレーム数の上限: 動画の長さが分かっているときは `長さ × min(fps, 20) + 1` 枚、無いときは 600 枚（`IMAGE_MAX_FRAMES`）。アドオン側の上限は 2000 枚（`build_image_addon.py` の `MAX_FRAMES`）
- 画像方式が失敗しても、ブロック方式のアドオンは作られているので、失敗はログに理由だけ出す（作成を止めない）
- ffmpeg はブロック方式と同じものを使う（`ffmpeg.exe` を同梱 / 同階層 / PATH の探索はそのまま）

## ブラウザ版（web/）での使い方

同じチェックボックス（`画像（テクスチャ）再生方式も作る`）を足した。生成が終わると、ブロック方式のダウンロードのリンクの下に画像方式のリンクが出る（ファイル名は `<パック名>_images.mcaddon`）。

- ブラウザには ffmpeg が無いので、画面が描いたコマ（`web/app.js` の `canvasToPpm` の PPM）を Python の Pillow で PNG にする（`image_mode/frame_extract.py` の `png_from_ppm`）。Windows 版は ffmpeg が直接 PNG を出すので、この経路は使わない
- `.vox` のジョブは画像方式では使えない（ログに理由を書く）。1 枚の画像は 1 枚のアドオンになる
- 画像方式の生成が失敗しても、ブロック方式のアドオンのダウンロードはそのまま出る

## ゲームでの確認手順（実機であなたが確認する）

1. `dist/ImagePoC.mcaddon` を Minecraft のワールドに適用する（新しいワールドで作ってもよい）。
   行動パケットとリソースパックの両方が有効になっていることを確認する。
2. ワールドに入り、プレイヤーの前に画面が出るので `/bvpimg:place` を実行する
   （独自コマンド。チートは不要、オペレーター権限が必要）。
3. `/bvpimg:play` を実行する。1 tick に 1 フレームが進めば testsrc のカウンターが毎 tick 変わって見えます。
4. `/bvpimg:frame 5` で静止（5 枚目の画像だけが出れば、プロパティ → テクスチャの対応が正しい）。
5. `/bvpimg:stop` で止まる（最初のフレームに戻る）。`/bvpimg:remove` で画面のエンティティを消す。
6. うまくいかないときの切り分け:
   - 何も見えない → エンティティが出ていない（`/bvpimg:place` の出力の座標を見る）、または RP が読まれていない
   - 1 枚のまま変わらない → プロパティがクライアントに同期されていない（`client_sync` の設定）
   - 全面が黒・壊れた模様 → ジオメトリの UV とテクスチャの大きさの対応が合っていない
   - 模様が横向きに反転して見える → 平面の前後の面の UV の向き（この PoC はどちら側からも映るように両面へ貼っている）

### 複数動画のアドオンの確認手順（本実装）

1. `dist/VideoPlayerImages.mcaddon` をワールドに適用する（PoC と同時に有効にすると `bvpimg:` のコマンドが衝突するので、どちらか一方だけ使う）。
2. `/bvpimg:list` で動画の一覧と、その動画の画面の位置・再生中かどうかが出ることを確認する。
3. `/bvpimg:setup badapple` でその動画の画面がプレイヤーの前に出る。もう 1 動画 `/bvpimg:setup intro` を立てると 2 枚の画面が並ぶ。
4. `/bvpimg:play badapple` で再生、`/bvpimg:play intro` で別の画面も再生（2 つの再生が同時に走ることを確認する）。
5. `/bvpimg:frame badapple 5` で 1 枚だけ表示、`/bvpimg:stop badapple` で停止。
6. `/bvpimg:next` で「次の動画」が切り替わる（hidden の動画は出ない）。
7. `/bvpimg:remove badapple` でその画面だけ消す（もう 1 動画の画面は残る）。`/bvpimg:remove`（引数なし）で全部消す。
8. ワールドを出て入り直しても `/bvpimg:list` が位置を覚えていて、`/bvpimg:play badapple` が同じ位置の画面を再利用することを確認する（再生の状態はスクリプトのメモリと `bvpimg:playing` にあるので、入り直すと自動では再開しない）。
9. うまくいかないときの切り分け（PoC と同じ）に加えて:
   - `list` が空 · 位置を覚えていない → ワールドの動的なプロパティ（`bvpimg:screens` / `bvpimg:playing`）が読めていない
   - 2 つ目の動画で 1 枚目の画面が消える → 画面の位置を動画ごとに保存していない（`bvpimg:screens` は動画 ID ごとに持つ）
   - enum の引数を受け付けない → `registry.registerEnum` の登録と `enumName` の対応が合っていない

**この手順を実機で確認する前は「動く」とは言いません。** 自動テストはパックの JSON の形しか確かめていません。

## 確認済みの事項（このセッションで実際に確かめたもの）

- `python image_mode/image_poc.py` が 12 枚の PNG を書き出し、`.mcaddon` を作れる（SHA-256 が 12 枚すべて違う＝フレームが実際に違う内容になっている）
- `python -m pytest tests/test_image_mode_poc.py tests/test_image_mode_addon.py` が通る（19 passed）。確かめているのは BP のプロパティの range と `client_sync`、RP の textures のキーと render controller の配列の対応、ジオメトリの `texture_width`/`texture_height` と UV の大きさ・aspect ratio、動画ごとのファイルが別名前になっていること、`image_videos.js` の `ticks_per_frame` が fps と一致すること、manifest の BP↔RP の依存、スクリプトにプレースホルダが残っていないこと、ID の形・重複・空のフレームを弾くこと
- `python image_mode/build_image_addon.py --sample 2 --width 192 --height 108 --limit 20` が 2 動画の入った `.mcaddon` を作れる（見積 3.2 MiB、動画ごとに別の entity / render controller / ジオメトリ / テクスチャフォルダ）
- ffmpeg による実際のフレーム抽出（`testsrc` を 96x54 で 4 枚）は、このマシンではフレームの bytes が 4 枚すべて違うことを確認した（ffmpeg が無い環境ではテストは skip）
- Windows 版とブラウザ版への統合（このセッションで実際に確かめたもの）:
  - `python -m pytest tests/test_image_mode_tools.py tests/test_gui_image_mode.py` が通る（16 passed）。別アドオンの出力名（`VideoPlayer_images.mcaddon`）・接頭辞（`badapple_img`）・パック名（`_images`）・説明文・使えない動画（`.vox` / `.pmx`）の skip・失敗時にブロック方式を止めないこと・フレーム数の上限（長さが分からなければ 600、分かっていれば `長さ × min(fps,20)+1`）を確かめている
  - `python scripts/build_web.py --out web_dist` が `image_mode/` の `.py` と `image_mode/scripts/image_main.js` を `files.json` に入れて出力する（45 files）
  - `node tests/js/pyodide_smoke.mjs <web_dist>` が、ブラウザ版と同じ 32bit の Pyodide で PPM → PNG（`png_from_ppm`）と `build_image_addon_bytes` を通す（`image_frames` = 20、画像アドオン 22,384 bytes）。`web/worker.js` の `runPython` の text をそのまま実行しているので、global の名前の衝突と JS の `Uint8Array` → Python の `bytes` の変換も通っている
  - `node --check web/app.js web/worker.js image_mode/scripts/image_main.js tests/js/pyodide_smoke.mjs` が通る
  - `python -m pytest tests/test_i18n.py` が通る（52 passed。10 言語すべてに `settings.image_mode` / `build.image_*` / `web.image_*` のキーと同じプレースホルダがある）

## 未確認の事項

- 実機でフレームが実際に切り替わるか、どのくらいの fps で見えるか
- テクスチャの配列の枚数の上限（公式ドキュメントに記載がない）
- 多くの PNG をエンジンが同時に読み込むか（必要なメモリ）、ローカル適用でテクスチャ予算が強制されるか
- 画面のエンティティがチャンクの未読込や描画距離で消えるか
- エンティティプロパティの client_sync が BDS・マルチプレイでどのタイミングで同期されるか
- `format_version` の値（BP のエンティティ 1.21.0 / RP のクライアントエンティティ 1.10.0 / render controller 1.8.0 / ジオメトリ 1.16.0）が現行のエンジンで受理されるか
- 独自コマンドの enum の引数（`registry.registerEnum` と `CustomCommandParameter.enumName`）がゲームで受け付けられるか。`enumName` は公式の型に記載があるが、Learn のページの status（安定版か preview か）は見ていない
- ワールドの動的なプロパティ（`world.getDynamicProperty` / `setDynamicProperty`）に JSON の文字列を保存する形が現行のエンジンで動くか、ワールドを閉じた後に残るか
- `dimension.getEntities({ location, maxDistance })` で画面のエンティティを再取得できるか（検索のプロパティは公式に記載のある `location` / `maxDistance` だけ使い、動画のエンティティは `entity.typeId` で選んでいる。`EntityQueryOptions` の `type` は公式の例に出ていますがプロパティの一覧には記載がないので使っていません）
- 1 動画に 1 個の `system.runInterval` を複数同時に走らせることの実際の負荷、複数プレイヤー・BDS での同期のズレ
- テクスチャの予算の**ローカル適用での強制**（公式の FAQ は「MCTools で事前に確認できる」と書いていて、ゲーム内の自動の判定は記載がない）。サブパックの `memory_performance_tier` は manifest V3 の実験的な設定なのでこのパックには入れていない
- Windows 版の EXE の中で `image_mode` が読めるか。`BlockVideoPlayer.spec` に `collect_submodules('image_mode')` と `image_mode/scripts/image_main.js` の同梱を追加したが、**このマシンで EXE の再ビルドはしていない**（torch_directml を含むビルドは重い）
- Windows 版のチェックボックスから、実際の ffmpeg 経由で作る実行（テストは `extract_frames` を差し替えているので、画面の操作から通る経路は未実行）
- ブラウザ版の画面でチェックしてダウンロードのリンクが出る操作（Pyodide でアドオンを作る部分は上の確認を通っているが、画面の操作は未確認）
- ブラウザ版で多くのフレームを PPM → PNG にしたときのメモリと時間（Pyodide は 32bit。「動画の長さ」を空にすると最後まで描くので、1 動画 2000 枚を超えたときは画像方式の生成が失敗して理由を書く（アドオンの上限は `build_image_addon.py` の `MAX_FRAMES`。ブロック方式のアドオンはそのまま出せる））

## 上限と容量（公式の値）

- エンティティプロパティは 1 種別あたり **32 個まで**（この方式は 1 個だけ使う）
- テクスチャの容量は **テクセル数 × 4 バイト**で見積もる。Add-On は Tier1 150MB / Tier2 225MB / Tier3 300MB / Tier4 600MB / Tier5 800MB、Resource Pack は 300/450/600/1200/1600MB（サブパックの `memory_performance_tier` で機種別に分けられる）
- 例: 192x108 × 12 フレーム = 約 0.9 MiB（実際に作った PoC の見積り）。256x144 × 600 フレーム = 約 88 MiB

## 出典（確認した公式ドキュメント）

- Render Controllers（テクスチャの配列と index の数式）: https://learn.microsoft.com/en-us/minecraft/creator/documents/animations/animationrendercontroller?view=minecraft-bedrock-stable
- Entity Properties（型・range・`client_sync`・32 個の上限）: https://learn.microsoft.com/en-us/minecraft/creator/documents/introductiontoentityproperties?view=minecraft-bedrock-stable
- `Entity.setProperty` / `getProperty`（安定版の API）: https://learn.microsoft.com/en-us/minecraft/creator/scriptapi/minecraft/server/entity?view=minecraft-bedrock-stable
- Molang の `q.property` / `q.life_time` / `q.frame_alpha`: https://learn.microsoft.com/en-us/minecraft/creator/documents/molang/syntax-guide?view=minecraft-bedrock-stable
- クライアントエンティティの textures / materials / geometry / render_controllers の定義: https://learn.microsoft.com/en-us/minecraft/creator/reference/content/entityreference/examples/cliententitydocumentation/cliententitydocumentationintroduction?view=minecraft-bedrock-stable
- Material（ビルトインの entity material を使う理由、カスタム material の注意書き）: https://learn.microsoft.com/en-us/minecraft/creator/documents/material-files?view=minecraft-bedrock-stable
- ジオメトリの `texture_width` / `texture_height` / 面ごとの UV: https://learn.microsoft.com/en-us/minecraft/creator/reference/content/visualreference/geometry.v1.16.0?view=minecraft-bedrock-stable
- テクスチャの予算（Tier の数値）: https://learn.microsoft.com/en-us/minecraft/creator/documents/texturebudgets?view=minecraft-bedrock-stable
- 独自コマンドの引数の型（`CustomCommandParamType.Integer` / `Enum`）: https://learn.microsoft.com/en-us/minecraft/creator/scriptapi/minecraft/server/custom-command-param-type?view=minecraft-bedrock-stable
- `CustomCommandParameter` の `enumName`（enum の型と同じ名前を別の引数名にできる）: https://learn.microsoft.com/en-us/minecraft/creator/scriptapi/minecraft/server/customcommandparameter?view=minecraft-bedrock-stable
- ワールドの動的なプロパティ（`world.getDynamicProperty` / `setDynamicProperty`。`null` を代入するとその値が消える）: https://learn.microsoft.com/en-us/minecraft/creator/scriptapi/minecraft/server/world?view=minecraft-bedrock-stable
- `Dimension.getEntities` と `EntityQueryOptions`（このコードは `location` / `maxDistance` のみ使う): https://learn.microsoft.com/en-us/minecraft/creator/scriptapi/minecraft/server/dimension?view=minecraft-bedrock-stable と https://learn.microsoft.com/en-us/minecraft/creator/scriptapi/minecraft/server/entityqueryoptions?view=minecraft-bedrock-stable
- サブパックの `memory_performance_tier`（manifest V3 で実験中、値は 1〜5）: https://learn.microsoft.com/en-us/minecraft/creator/documents/buildingsubpacks?view=minecraft-bedrock-stable

## 次の段階の候補（まだ実装していません）

- フレームの PNG を 1 枚のアトラスにまとめてテクスチャの枚数を減らす（テクスチャの枚数の上限が公式に記載されていないので、枚数が多いときのリスクを下げる。`origin/feat/image-mode` の側には `image_mode/atlas.py` の実験があります＝参照のみ、未マージ）
- 音声（ブロック方式は RP の sounds を chunk で鳴らす。この方式は音声を見ていません。動画ごとに `sound_definitions.json` を別名で置く形が必要）
- 20fps を超える滑らかな再生（Molang の `q.life_time` / `q.frame_alpha` で描画側が自律進行する方式。承認は取れていません）
- ブロック方式と同じ 1 つのアドオンに統合する（`pack_builder.py` にテクスチャ・エンティティ・render controller を加える大きな変更なので、事前に相談します）
- フレーム数・解像度を大きくしたときの実機でのメモリと読み込みの時間（`--limit` と Tier の見積りの警告はコードに入っています。実機での上限は未知）
