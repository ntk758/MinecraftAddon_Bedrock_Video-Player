# 画像モード (テクスチャ再生方式) の取り決め

ブロックモード (`convert.py`・`main.js`・`codec.js`・`pack_builder.py`) とは別のアドオンを作る。ブロックモードのファイルとデータ形式は変えない。

## 作るアドオン (`convert_image_mode.py` が 1 つの .mcaddon にする)

```
BP/manifest.json                      @minecraft/server 2.6.0、min_engine_version 1.26.10、script の入口 scripts/image_main.js
BP/entities/screen_<id>_<m>.json      動画 × 方式ごとのエンティティ (m は a か c)
BP/scripts/image_main.js              再生制御 (メインセッションが書く。image_mode/image_main.js を写す)
BP/scripts/image_videos.js            export const IMAGE_VIDEOS = [...] と export const NAMESPACE = "<ns>"
RP/manifest.json
RP/entity/screen_<id>_<m>.entity.json
RP/models/entity/screen_<id>_<m>.geo.json
RP/render_controllers/screen_<id>_<m>.render_controllers.json
RP/materials/entity.material          方式 C を含むときだけ
RP/textures/bvp_image/<id>/sheet_<n>.png
RP/sounds/sound_definitions.json、RP/sounds/bvp_image/<id>/chunk_<n>.ogg
RP/texts/en_US.lang ほか (今の locales の addon.* は使わない。画像モードの案内は英語と日本語だけでよい)
```

方式: `a` = アトラス + textures 配列 + 1 マス 1 ボーン + part_visibility (マテリアルを変えない)。`c` = USE_UV_ANIM のマテリアル + uv_anim
(将来の更新で壊れるリスクがある)。`--method a|c|both` (既定 both)。both のときテクスチャは共有し、エンティティを 2 種類作る。

## 動画のメタデータ (Python の dict。image_videos.js にも同じ項目を JSON で書く)

```
id               動画 ID ([a-z0-9_]+)
title            表示名
fps              再生の fps (既定 30)
frame_count      コマ数
width, height    1 コマの画素数
sheet_width, sheet_height   シート 1 枚の画素数 (全シート同じ大きさ。最後のシートも縮めない)
cols, rows       1 枚のマスの数 (cells_per_sheet = cols * rows)
cells_per_sheet
sheets           シートの枚数
gutter           マスの間の画素 (既定 1。にじみ対策。マス i の左上は x = (i % cols) * (width + gutter), y = (i // cols) * (height + gutter))
screen_blocks    [幅, 高さ] スクリーンのブロック数 (既定は幅 16、高さは縦横比から)
texture_paths    ["textures/bvp_image/<id>/sheet_0", ...] (拡張子なし)
audio_chunks     音声のチャンク数 (0 なら無音)
audio_chunk_seconds  チャンクの秒数 (既定 4)
methods          ["a", "c"] など
memory           {"texels": 全シートの画素数, "bytes": texels * 4}
```

## 名前

- エンティティ: `<ns>:screen_<id>_<m>`
- geometry: `geometry.<ns>.screen_<id>_<m>`、render controller: `controller.render.<ns>.screen_<id>_<m>`
- プロパティ (BP の entity で client_sync: true、型はすべて int):
  `<ns>:state` [0, 2] (0 停止・1 再生・2 一時停止)、`<ns>:frame` [0, frame_count - 1]、`<ns>:seq` [0, 1023]
- 音: `<ns>.img.<id>.chunk_<n>` (category "ui"、stream true、load_on_low_memory true)

## クライアントのコマの計算 (client_entity の scripts.pre_animation)

スクリプトは「同期したときのコマ」(frame) と通し番号 (seq) を書く。クライアントは seq が変わった時刻を覚え、そこから自分の時計 (q.life_time) で進める。
q.life_time はチャンクの読み直しなどで 0 に戻るが、そのときは変数も消えるので、次の pre_animation で今の frame から進め直す。

```
v.seq_now = q.property('<ns>:seq');
v.changed = (v.seq_seen ?? -1) != v.seq_now;
v.base_time = v.changed ? q.life_time : v.base_time;
v.base_frame = v.changed ? q.property('<ns>:frame') : v.base_frame;
v.seq_seen = v.seq_now;
v.frame = q.property('<ns>:state') == 1 ? math.min(v.base_frame + math.floor((q.life_time - v.base_time) * <fps>), <frame_count - 1>) : v.base_frame;
v.sheet = math.floor(v.frame / <cells_per_sheet>);
v.cell = v.frame - v.sheet * <cells_per_sheet>;
```

- 方式 A: geometry にボーン `c0` 〜 `c<cells-1>` (どれも同じ位置・同じ大きさの板 1 枚。north の面の UV がマス i)。render controller は
  `textures: ["Array.sheets[v.sheet]"]`、`part_visibility: [{"*": false}, {"c0": "v.cell == 0"}, ...]` (1 要素 1 キー)。
- 方式 C: geometry はボーン 1 本・板 1 枚 (UV はマス 0)。render controller は
  `uv_anim: {offset: ["math.mod(v.cell, cols) * (width + gutter) / sheet_width", "math.floor(v.cell / cols) * (height + gutter) / sheet_height"], scale: [1, 1]}`、
  materials `{"*": "Material.default"}`、client_entity の materials `{"default": "bvp_uv_anim"}`。
  `RP/materials/entity.material` に `"bvp_uv_anim:entity_alphatest": {"+defines": ["USE_UV_ANIM"]}`
  (Microsoft Learn「Improving performance and resource usage」の手順。同じ Learn が自作マテリアルは将来削除されうると書いているので、壊れるリスクをコメントに書く)。
- 状態が 0 (停止) のときは、方式 A は全ボーンを隠し、方式 C も板を隠す (part_visibility で `q.property('<ns>:state') != 0`)。

## スクリプトとの約束 (image_main.js)

- 時計は `world.getAbsoluteTime()` (tick)。再生中は 1 秒ごとに frame と seq (1 ずつ増やして 1024 で 0 に戻す) を書き直す。
- 状態は world の dynamic property `<ns>:image_state` に JSON で保存し、ワールドを開き直したら続きから戻す。
- コマンド: `/<ns>:setup [動画ID] [a|c]`、`/<ns>:start`、`/<ns>:pause`、`/<ns>:resume`、`/<ns>:stop`、`/<ns>:seek <秒>`、`/<ns>:method <a|c>`、
  `/<ns>:list`。`/scriptevent <ns>:<操作>` でも同じ。
