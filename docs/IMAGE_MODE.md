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

マルチプレイでは 1 つのエンティティを全員が見るので、サーバーが書く値は 1 動画あたり 1 回/tick です。

## 作ったファイル

| ファイル | 内容 |
|---|---|
| `image_mode/image_poc.py` | フレームの PNG を ffmpeg で書き出し、`.mcaddon` を組み立てる（解像度・fps・枚数は引数） |
| `image_mode/scripts/image_poc_main.js` | 画面のエンティティを出す・再生・停止・1 フレーム表示のスクリプト（ブロック方式の `main.js` は使わない） |
| `tests/test_image_mode_poc.py` | `.mcaddon` の中身の形（JSON の整合）のテスト |

生成されるパックの中身（`dist/` は `.gitignore` の対象なのでコミットしません）:

```
BP/manifest.json            @minecraft/server のみ依存 (server-ui は使わない)
BP/entities/screen.json     エンティティプロパティの宣言
BP/scripts/main.js          PoC のスクリプト
RP/manifest.json
RP/entity/screen.json       フレームの PNG を短い名前 (f0..fN) で並べる
RP/render_controllers/screen.json  テクスチャの配列を q.property で選ぶ
RP/models/screen.geo.json   画面の平面 (aspect ratio はフレームと同じ)
RP/textures/bvpimg/frame_0.png ... frame_N.png
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

**この手順を実機で確認する前は「動く」とは言いません。** 自動テストはパックの JSON の形しか確かめていません。

## 確認済みの事項（このセッションで実際に確かめたもの）

- `python image_mode/image_poc.py` が 12 枚の PNG を書き出し、`.mcaddon` を作れる（SHA-256 が 12 枚すべて違う＝フレームが実際に違う内容になっている）
- `python -m pytest tests/test_image_mode_poc.py` が通る（BP のプロパティの range と `client_sync`、RP の textures のキーと render controller の配列の対応、ジオメトリの `texture_width`/`texture_height` と UV の大きさ、manifest の BP↔RP の依存、スクリプトにプレースホルダが残っていないこと）

## 未確認の事項

- 実機でフレームが実際に切り替わるか、どのくらいの fps で見えるか
- テクスチャの配列の枚数の上限（公式ドキュメントに記載がない）
- 多くの PNG をエンジンが同時に読み込むか（必要なメモリ）、ローカル適用でテクスチャ予算が強制されるか
- 画面のエンティティがチャンクの未読込や描画距離で消えるか
- エンティティプロパティの client_sync が BDS・マルチプレイでどのタイミングで同期されるか
- `format_version` の値（BP のエンティティ 1.21.0 / RP のクライアントエンティティ 1.10.0 / render controller 1.8.0 / ジオメトリ 1.16.0）が現行のエンジンで受理されるか

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

## 次の段階（本実装）の候補（まだ実装していません）

- フレームの PNG を 1 枚のアトラスにまとめてテクスチャの枚数を減らす（テクスチャの枚数の上限が不明なので、枚数が多いときのリスクを下げる）
- 複数動画に対応する（動画ごとに 1 エンティティ、`<ns>:frame` と `<ns>:video` のプロパティで選ぶ。32 個の上限に収める）
- 音声（ブロック方式は RP の sounds を chunk で鳴らす。この PoC は音声を見ていません）
- 20fps を超える滑らかな再生（Molang の `q.life_time` / `q.frame_alpha` で描画側が自律進行する方式。承認は取れていません）
