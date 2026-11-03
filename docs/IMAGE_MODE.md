# 画像モード (テクスチャ再生方式、試作)

ブロックモードとは別のアドオンを作ります。動画の全コマを PNG のアトラス (シート) にしてリソースパックに入れ、板状のスクリーンのエンティティに表示します。コマの切り替えはクライアントの Molang が自分の時計で行うので、30 fps で再生できます (ブロックモードは 20 fps まで)。ブロックモードの変換・再生・データ形式は変えていません。**実機ではまだ確かめていません。**

```bash
python convert_image_mode.py video.mp4 -o video_image.mcaddon --width 256 --height 144 --fps 30 --start 0 --end 20 --method both
```

ゲーム内: `/bvpimg:setup [動画ID] [a|c]` → `/bvpimg:start`。`pause`・`resume`・`stop`・`seek <秒>`・`method <a|c>`・`list` (`/scriptevent bvpimg:<操作>` でも可)。

## 方式 A と方式 C

`--method both` (既定) で両方のエンティティを入れ、`/bvpimg:method a|c` で同じ場所・同じ再生位置のまま切り替えて見比べられます。テクスチャは共有します。

- **方式 A** (既定): アトラス + textures 配列 + 1 マス 1 ボーン + part_visibility。マテリアルを変えず、文書にある機能だけを使います。マスの数だけボーンがあり、その負荷は未確認です。
- **方式 C**: `USE_UV_ANIM` を足した自作マテリアル (`bvp_uv_anim:entity_alphatest`) + uv_anim。ボーンは 1 本です。
  **⚠ 将来の Minecraft の更新で壊れるリスクがあります。** Microsoft Learn は自作マテリアルについて「将来、非推奨または削除される可能性がある」と書いており、Vibrant Visuals で動くかも未確認です。

## テクスチャメモリ

テクセル数 × 4 バイトで数えます (Microsoft Learn の Texture Budgets)。変換時に Tier ごとのアドオンの予算 (Tier 1 150 MB 〜 Tier 5 800 MB) と比べた表を出し、Tier 2 (多くのスマートフォン・PS4・Xbox One) を超えると警告、Tier 5 を超えると `--allow-over-budget` が無ければ止めます。例: 256×144・30 fps・20 秒で約 97 MiB。

## 同期と限界

スクリプトは 1 秒ごとに「今のコマ」と通し番号をエンティティのプロパティに書き、クライアントはそこから自分の時計で進めます。チャンクの読み直しやワールドの開き直しの後は、最後に書いたコマから進め直し、次の書き直し (1 秒以内) で戻ります。時計は `world.getAbsoluteTime()` なので、ワールドを閉じている間は止まります。音声は 4 秒ごとのチャンクで、途中からは鳴らせないため、一時停止・シーク・途中参加の後は次の境目まで無音です。

詳しい取り決めは [image_mode/SPEC.md](../image_mode/SPEC.md)、調査の根拠はフェーズ 1 の報告にあります。
