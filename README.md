# Block Video Player — 統合版(Bedrock)ブロック動画プレイヤー

[umbreonben/mc-cushion-bad-apple](https://github.com/umbreonben/mc-cushion-bad-apple)
(Java版 datapack)を Minecraft統合版(Bedrock Edition) の Behavior Pack + Script API で
再実装したもの。**v1.1.0でBad Apple専用から任意のカラー動画に対応した汎用プレイヤーに
なった。現在のパック版は v1.3.4。**

## バージョン履歴

- **v1.0.0**: 11段階の発光ブロック(copper bulb系等)で白黒Bad Appleを再現しようとしたが、
  ブロックの色相がバラバラで実機では「絵に見えない」結果になった(反省点は下記参照)
- **v1.1.0**: Minecraft公式マップカラー定義由来の16色concreteパレットへの
  最近傍色マッチング方式に全面変更。カラー動画全般に対応
- **v1.2.0**: 動画フレームから`pack_icon.png`を生成してパック一覧のサムネイルに使用。
  変更履歴をパック内の`CHANGELOG.md`にも同梱
- **v1.3.0**: 高解像度プリセット、concrete + terracottaの33色パレット、
  Floyd-Steinbergディザリングを追加し、色と解像感を向上
- **v1.3.1**: `minecraft`は予約済みのため、GUIの実行コマンド接頭辞として指定できないよう修正
- **v1.3.2**: 無色テラコッタの統合版IDを`minecraft:hardened_clay`へ修正。
  旧パックで生成済みの`minecraft:terracotta`データにも再生時の互換対応を追加
- **v1.3.3**: 再生開始前にtickingareaを再確保してロード待ちを追加。
  ゲーム内の案内文もGUIで設定したコマンド接頭辞を表示
- **v1.3.4**: コードレビューで見つかった軽微な問題を修正。
  `TICKING_AREA_NAME`定数が使用箇所より後ろで定義されていた行儀の悪さを解消、
  `startPlayback`内の`intervalId`がrunTimeout→runIntervalへ切り替わる挙動に
  説明コメントを追加、READMEにGUIでのコマンド接頭辞変更時の注意書きを追加。
  実際の機能・データ形式に変更はなし

**運用ルール**: プログラムを変更した場合は、`pack_metadata.py` の `PACK_VERSION` と
`RELEASE_NOTES` を必ず更新する。GUIはこの情報を`manifest.json`の表示名・説明文と、
パック内の`CHANGELOG.md`へ自動反映する。

## Java版との根本的な違い

Java版はdatapackの`.mcfunction`をフレーム数だけ大量生成し、`schedule function`で
1tickごとに連鎖実行していた。統合版にはdatapackという概念がないため、
以下のように置き換えている。

| Java版 | 統合版(本実装) |
|---|---|
| `.mcfunction`をフレーム数だけ生成 | 差分データを`frames_data.js`1本にまとめてimport |
| `schedule function frame_N 1t` | `system.runInterval(callback, 1)` |
| `/execute at @e[...] run setblock` | `dimension.setBlockPermutation()` |
| `/function bad_apple:start` 等のコマンド | `/scriptevent badapple:start` 等 |
| 11段階の発光ブロックで白黒濃淡を表現 | 16色concreteへの最近傍色マッチングでカラー動画を表現 |

## 色変換の仕組み(v1.1.0)

各フレームの各ピクセルのRGB値を、以下の16色concreteパレットの中から
色距離(ユークリッド距離)が最も近いものに変換する。

パレットの基準RGB値は、Minecraft公式のマップカラー定義
(Java Edition Wiki「Map item format」ページのConcreteエントリ。
Spigotソースの`MaterialMapColor.java`が一次情報源)から取得した。
ブロックIDは Bedrock公式の Default Minecraft Block Listings
(learn.microsoft.com)で実在確認済み。

| 色名 | ブロックID | 基準RGB |
|---|---|---|
| white | `minecraft:white_concrete` | (255,255,255) |
| orange | `minecraft:orange_concrete` | (216,127,51) |
| magenta | `minecraft:magenta_concrete` | (178,76,216) |
| light_blue | `minecraft:light_blue_concrete` | (102,153,216) |
| yellow | `minecraft:yellow_concrete` | (229,229,51) |
| lime | `minecraft:lime_concrete` | (127,204,25) |
| pink | `minecraft:pink_concrete` | (242,127,165) |
| gray | `minecraft:gray_concrete` | (76,76,76) |
| light_gray | `minecraft:light_gray_concrete` | (153,153,153) |
| cyan | `minecraft:cyan_concrete` | (76,127,153) |
| purple | `minecraft:purple_concrete` | (127,63,178) |
| blue | `minecraft:blue_concrete` | (51,76,178) |
| brown | `minecraft:brown_concrete` | (102,76,51) |
| green | `minecraft:green_concrete` | (102,127,51) |
| red | `minecraft:red_concrete` | (153,51,51) |
| black | `minecraft:black_concrete` | (25,25,25) |

**未検証**: この16色は「マップアイテムの色」の定義値であり、実際のブロック
テクスチャの色とわずかに異なる可能性がある(マップは近似色表示のため)。
また色数がわずか16のため、グラデーションの多い実写映像などでは
バンディング(色の縞)が目立つ可能性がある。実機で見た目を確認し、
気になる場合はパレットに terracotta 系や glazed_terracotta 系を追加して
色数を増やすことを検討する。

## 構成

```
BP/
  scripts/
    frames_data.js   … convert.pyで生成する変換済みフレームデータ
main.js               … 再生本体。GUIがパック内のscripts/へ配置する
manifest.json         … Behavior Pack定義。GUIがパック直下へ配置する
convert.py            … 動画フレームPNG連番 → frames_data.js への変換スクリプト
video_player_gui.py   … 動画選択から.mcpack作成までを行うGUI
```

## 使い方

### GUI（推奨）

`python video_player_gui.py` を実行すると、動画の選択から`.mcpack`の作成までをGUIで行える。
ffmpegがPATHから呼び出せること、およびPythonに`Pillow`と`numpy`が入っていることが前提。
出力される`.mcpack`には、`manifest.json`、本体スクリプト、変換済みフレームデータが正しい
Behavior Pack構成で格納される。

GUIでは、出力ファイル名・パック表示名・実行コマンド接頭辞・解像度・再生間隔を設定できる。
再生間隔は「1フレームあたりのゲームtick数」で、`1`なら20fps、`2`なら10fpsとなる。
コマンド接頭辞を`movie`にした場合、ゲーム内では`/scriptevent movie:setup`、
`/scriptevent movie:start`、`/scriptevent movie:stop`を使う。
`minecraft`は予約済みの名前空間なので、接頭辞には使用できない。
サムネイル秒数で指定した動画フレームは、パックの`pack_icon.png`として書き出され、
Minecraftのパック一覧に表示される。

画質プリセットは高いほどブロック数・生成データ量・ゲーム内負荷も大きくなる。まずは
「高画質 (128×128 / 10fps)」で短い動画を試し、負荷が高い場合は「標準」または「軽量」に下げる。

### コマンドライン

1. ffmpegでフレームを書き出す:
   ```
   ffmpeg -i yourvideo.mp4 -vf "fps=20,scale=64:64:flags=lanczos" frames/output_%04d.png
   ```
   (短い動画で試す場合は `-t 10` 等で秒数を絞るとよい)
2. `python3 convert.py` を実行 → `BP/scripts/frames_data.js` が生成される
3. GUIを使わず手動でパッケージ化する場合は、空のフォルダに`manifest.json`を置き、
   その直下の`scripts/`に`main.js`と`frames_data.js`を置いてから、その**中身**を
   `.zip`化して拡張子を`.mcpack`に変更してインポートする
4. ワールド設定で「ベータAPI」実験を有効化した上でBehavior Packを適用
5. ワールド内で:
   - `/scriptevent badapple:setup` … 自分の足元を起点に原点座標を保存
   - `/scriptevent badapple:start` … 再生開始
   - `/scriptevent badapple:stop` … 停止して盤面クリア

   **注意**: 上記の`badapple`は`main.js`内の`EVENT_NAMESPACE`のデフォルト値。
   GUIで「コマンド接頭辞」を変更した場合、実際に使うコマンドはその接頭辞に
   置き換わる(例: 接頭辞を`movie`にした場合は`/scriptevent movie:setup`等)。
   GUIでの作成完了時にも、実際に使うべきコマンドがログとダイアログに表示される。

## 実機で判明した事実(修正済み)

- `world.afterEvents.worldLoad` は現行の依存バージョンでは **`undefined`** であり、
  `.subscribe()`を呼ぶと`TypeError`でスクリプト全体が停止することを実機で確認した。
  このイベントは起動ログ出力用のおまけ機能だったため、該当コードは削除して対応済み。
- **`minecraft:marker`というエンティティは統合版に存在しない。**
  `dimension.spawnEntity("minecraft:marker", ...)`を呼ぶと
  `InvalidArgumentError: 'minecraft:marker' is not a valid entity type` で実機エラーになることを確認した。
  Java版のMarkerエンティティはdatapack/マップ制作専用の仕組みであり、統合版に同名の
  対応物は存在しない。対応として、原点座標の保持方式をエンティティから
  `world.setDynamicProperty("badapple:anchor", {x,y,z})` に変更した(エンティティを
  一切使わない設計に変更)。実機で座標の保存・取得の往復動作も確認済み。
- **`LocationInUnloadedChunkError`(実機で頻発)**
  64x64の盤面はチャンク換算で最大4x4チャンク分の広さがあり、プレイヤーの通常の
  読み込み範囲だけではみ出す部分が発生し、該当ブロックの描画に失敗することを
  実機で確認した。対応として`setup`実行時に`/tickingarea add`を
  `dimension.runCommand()`経由で呼び、盤面全域を常時ロード状態にするよう変更した。
  - Script APIには`tickingarea`専用メソッドが存在しないため、コマンド文字列を
    直接実行する`runCommand()`を使っている
  - **ワールドに登録できるtickingareaは最大10個**という制約があるため、
    本実装は`setup`のたびに同名エリアを`remove`してから`add`し直す設計にして
    上限に達しないようにしている
- **v1.0.0の色再現性の問題(v1.1.0で全面対応)**
  11段階の発光ブロック(copper_bulb系、crying_obsidian、sculk_catalyst、magma、
  glowstone、sea_lantern等)を「発光レベルの数値」だけを基準に選んでいたため、
  実機で見ると質感・色相がバラバラで映像として成立していなかった。原因は、
  Java版が銅の警告灯(copper bulb)の風化段階4種という単一ブロックファミリーで
  色相を統一していたのに対し、無関係な複数ブロックファミリーを混在させて
  しまった設計ミス。対応として、16色concreteへの最近傍色マッチング方式に
  全面的に切り替えた(上記「色変換の仕組み」参照)。

## 意図的に採用しなかった設計

- Java版の`armor_stand + marker + NBT`によるクッション表示部分は、統合版でNBTを
  直接指定する`/summon`構文がJavaと異なる可能性が高いため、今回のバージョンでは
  **クッション(座布団ブロック)の見た目部分は未実装**。ブロックの明滅のみで
  映像を表現する形にとどめている
- 発光ブロックによる「暗闇でも見える」演出は、色相統一を優先するため
  v1.1.0では採用していない(要望により優先順位を変更)

## 動作確認手順(推奨)

1. まず数秒〜十数秒分のフレームだけで`convert.py`を試し、`frames_data.js`が
   問題なく読み込まれるか確認する
2. 自分のメインPCのMinecraft統合版で動作確認する
   ([[bluestacks-minecraft-host]]は既に低fps問題を抱えているため、パフォーマンス検証には使わない)
3. 問題なければ徐々にフレーム数・解像度を上げていく
4. 色の再現性が気になる場合は、実機のスクリーンショットと元動画を見比べて、
   パレットの拡張(terracotta系の追加等)を検討する
