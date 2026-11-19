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
views            (立体だけ) 向きの数。マス = コマ × views (コマ f・向き k はマス f * views + k)。cells にマスの数
loop             (立体だけ) true なら終わりで頭へ戻る (スクリプトもクライアントも)
glass            (ガラスケースの立体だけ) {"box": [幅, 奥行き, 高さ] ブロック, "master": "figure" か "wall_video",
                 "figure": {frames, fps, loop, per_sheet, sheets, sheet_width, sheet_height, texture_paths, quads,
                            rig: {animation, seconds, bones} (骨で動かす MMD の人物だけ)},
                 "case": {texture_path, texture_size, uv: {部品: [x, y, 幅, 高さ]}, frame, frame_texel} (--case のとき),
                 "wall_video": {frame_count, fps, width, height, cols, rows, cells_per_sheet, sheets, gutter,
                                sheet_width, sheet_height, texture_paths, plane: [幅, 高さ, 下端] ブロック} (--case-video のとき)}
                 トップの fps・frame_count・audio_* は主 (master) の時計。width・height は壁の動画 (無ければ人物のシート) の大きさ
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
v.t = (math.floor(q.life_time * 20) + q.frame_alpha) / 20;   // vanilla と同じ、tick の間も進む時計
v.base_time = v.changed ? v.t : v.base_time;
v.base_frame = v.changed ? q.property('<ns>:frame') : v.base_frame;
v.seq_seen = v.seq_now;
v.frame = q.property('<ns>:state') == 1 ? math.min(v.base_frame + math.floor((v.t - v.base_time) * <fps>), <frame_count - 1>) : v.base_frame;
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
- 立体 (views ≥ 2、方式 A だけ): `v.rel = q.body_y_rotation - q.rotation_to_camera(1)` を 0〜360 にし、`v.view = math.mod(math.round(v.rel / (360 / views)), views)`、
  `v.slot = v.frame * views + v.view` からシートとマスを求める。geometry はマスのボーンの親にボーン `root` を置き、アニメーション
  `animation.<ns>.screen_<id>_a.face` が `root` を `[0, v.face, 0]` (`v.face = -v.rel`) 回してカメラへ向ける。マテリアルは `entity_alphatest`。

## ガラスケースの立体 (glass。image_mode/planes.py・glass.py・glass_rp.py)

- 人物: ボクセルの外に出ている面を、軸ごとの境目の平面の板 (厚み 0 の直方体の east・north・up の面の 1 つだけに UV) にする。
  ボーンはコマごとに `f<n>`、テクスチャ `Texture.fig_<n>` はシート 1 枚に `per_sheet` コマ。マテリアルは `entity_alphatest` (両面)。
  UV の向き (FaceMapping) は uv_size がいつも正になるよう、画像を反転してシートに置く。向きの既定 (1, -1, 1, -1) は実機で決めた (DECISIONS.md の A19。east の面は -x を向く)。
- ケース: geometry `geometry.<ns>.screen_<id>_a_case` のボーン `case_back`・`case_front`・`case_left`・`case_right`・`case_floor`
  (内向きの面)・`case_frame`。マテリアルは `entity` (client_entity の `opaque`)。手前の壁は part_visibility で隠す:
  `v.rel = q.body_y_rotation - q.rotation_to_camera(1)`、`v.flat = q.distance_from_camera * math.cos(q.rotation_to_camera(0))`、
  カメラの位置 `v.cam_x = v.flat * math.sin(v.rel)`・`v.cam_y = -v.flat * math.cos(v.rel)` (ボクセルの向き。A18 と同じ符号)。
  奥の壁は `v.cam_y < 奥行き/2` のときだけ、手前は `v.cam_y > -奥行き/2`、左は `v.cam_x > -幅/2`、右は `v.cam_x < 幅/2`。
- 壁の動画: geometry `..._wall` のボーン `c<i>` (方式 a のマス)、テクスチャ `Texture.vid_<n>`。奥の壁と同じ条件で見せる。
- 時計: 主が壁の動画なら、`v.secs` (主の時刻、終わりで止まる) から `v.fig = math.mod(math.floor(v.secs * 人物の fps + 0.0001), コマ数)`
  (浮き彫りの人物は math.min で最後に止める)。主が人物なら `v.fig = v.frame`。`v.fsheet = math.floor(v.fig / per_sheet)`。
- スクリプト: `setup` の `wall` は使わず、目の前の床に置く (`bvpimg.case_not_on_wall` で知らせる)。それ以外は今までの動画と同じ。

### 骨で動かす MMD の人物 (figure.rig。image_mode/rig.py・rig_anim.py)

- geometry: 部品ごとのボーン `b<PMX のボーンの番号>` (親は残したボーンの親、pivot は初期姿勢のボーンの位置で全体の座標)。
  踊りで動かないボーンは親にまとめてあり、geometry には入らない。移動のあるボーン (センターなど) は、移動だけを受け持つ
  親のボーン `m<番号>` (板なし、pivot は同じ) を持つ。テクスチャは 1 枚 (`Texture.fig_0`)、part_visibility は `{"*": state != 0}`。
- アニメーション: `RP/animations/screen_<id>_a.animation.json` の `animation.<ns>.screen_<id>_a.dance`。client_entity の
  `animations: {"dance": …}`、`scripts.animate: ["dance"]`。`loop: true`、`anim_time_update` は時計 `v.secs` (秒) で、
  繰り返すなら `math.mod(v.secs, 長さ)`、繰り返さないなら `math.clamp(v.secs, 0, 長さ − 0.001)`。キーは 30 Hz の時刻に置く。
- 回転: `b<番号>` の `rotation` (a, b, c) 度。ボクセルの軸 (x 右・y 奥・z 上) で R = Ry(c) · Rz(−b) · Rx(a)、子はローカル (DECISIONS.md の A20)。
- 移動: `m<番号>` の `position`。geometry の軸の移動そのまま (`POSITION_SIGNS` = (1, 1, 1)。実機で確かめた。DECISIONS.md の A21)。
- 時計: 主の時計 (人物が主なら fps 20、コマ数は長さ × 20 + 1) から、`v.secs = state == 1 ? math.min(v.base_frame / fps + (v.t − v.base_time), 長さ) : v.base_frame / fps`
  (繰り返すなら math.mod)。コマで刻まないので、動きは描画のたびになめらかに補間される。`v.fig = 0`。
- 曲 (`--audio`): 主の時計の長さは曲の長さ。踊りが短ければ最後の姿勢で止まり、長ければそこで切る。壁の動画があれば主は壁の動画のまま、音声だけ曲にする。
- 上限: 踊り・曲の長さは `mvcodec.mmd.MAX_MOTION_SECONDS` (20 分)、細かさは `image_mode.rig.MAX_VOXELS` (512 ボクセル)。超えたら作らない (ValueError)。

## スクリプトとの約束 (image_main.js)

- 時計はスクリプトが再生中に数える実時間 (`playMs`、tick ごとに `Date.now()` の差を足す。1 tick で足すのは 250 ms まで。1 秒ごとに保存し、古い版のために `playTicks` も書く)。v6.37.1 までは tick の数 (`playTicks`) で、TPS が 20 を下回るとサーバーの位置が実時間より遅れた。途中で入った人がいると、そのときにも frame と seq を書き直す。`world.getAbsoluteTime()` は昼夜の時刻なので使わない。再生中は 1 秒ごとに frame と seq (1 ずつ増やして 1024 で 0 に戻す) を書き直す。
- 状態は world の dynamic property `<ns>:image_state` に JSON で保存し、ワールドを開き直したら続きから戻す。
- コマンド: `/<ns>:setup [動画ID] [face] [wall] [align] [a|c]`、`/<ns>:start`、`/<ns>:play [動画ID]`、`/<ns>:pause`、`/<ns>:resume`、
  `/<ns>:stop`、`/<ns>:seek <秒>` (小数可。数の引数として登録する: 文字の引数だと数だけで構文エラーになった)、`/<ns>:volume <0〜100>`、
  `/<ns>:stats`、`/<ns>:method <a|c>`、`/<ns>:list`。`/scriptevent <ns>:<操作>` でも同じ。
- リモコン: 時計 (`minecraft:clock`) を使うか `/<ns>:remote` で開く (@minecraft/server-ui 2.0.0)。ブロックモードはコンパスなので、両方を入れても同時に開かない。
- スクリーンは `spawnEntity` の `initialRotation` で向きを渡して生む (その後 `setRotation` も呼ぶ)。BP のエンティティには `minecraft:body_rotation_always_follows_head` を付ける。
  どちらも無いと、頭だけが回って体 (板) は既定の向きのまま約 1 秒残り、0.4 秒ほどかけて回ってくる (`play` で置き直すたびに、再生の最初の約 1.5 秒が横向きに映った: 実機 2026-11-15)。
- setup の置き方: 何も付けなければプレイヤーの前にこちら向き。`face` は向きを東西南北にそろえる。`wall` は見ているブロックの横の面に
  0.02 浮かせて貼る (下端はそのブロックの下の面)。`align` は位置をブロックの格子にそろえる。
- 音量は state.volume (0〜100、保存する) を playSound の volume に。途中で入ってきた人には、チャンクが始まって 1 秒以内なら今のチャンクを鳴らす。
- メッセージは locales/*.json の `imgaddon.*` (10 言語) から RP/texts/*.lang の `bvpimg.*` を作る。
- 方式は既定で a だけを入れる (2026-11-12 の実機の確認で a に決めた。c は `--method c|both` のときだけ)。方式 a のマテリアルは `entity`
  (不透明)。`entity_alphatest` は裏の面も描くため、裏から鏡写しの絵が見えた。`entity` が裏を描かないことは実機で未確認。
