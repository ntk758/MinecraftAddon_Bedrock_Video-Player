---
name: premium-vanilla-ui
description: Block & Image Video Player のブラウザ版 (web/ の index.html・style.css・ui.js) の画面を作る・直す・見直すときの決まり。暗いグラファイトに青から紫のアクセントのデザインシステム (色・文字・余白・配置)、ページの組み立て、Motion と Lucide の Vanilla JavaScript での使い方、アクセシビリティ、スマートフォン対応、表示速度、Playwright での見た目の確認、変換の機能を壊さないための約束。web/ の HTML・CSS・画面の JS を変えるとき、画面のデザイン・アニメーション・アイコン・レイアウト・多言語の文言を足すとき、ブラウザ版の見た目を確かめるときに使う。React・Vue・Next.js・Tailwind には移さない。
---

# Premium Vanilla UI (ブラウザ版の画面)

対象は `web/` だけ。変換と組み立ては `web/app.js`・`web/worker.js` (Pyodide で `convert.py`・`pack_builder.py` を動かす) が受け持ち、画面の見た目と動きは `web/ui.js` と `web/style.css` が受け持つ。この分け方を崩さない。

## 1. 変えてはいけないこと (最初に確かめる)

- **変換の処理・値・順番を変えない。** `app.js` の `settings()`・`extractFrames`・`extractAudio`・`build()`、`worker.js` は画面のために書き換えない。画面から app.js に足してよいのは `UI.*` を呼ぶ 1 行だけ。
- **要素の id を消さない・変えない。** app.js・preview.js・camera.js・mmd.js・usage.js・ui.js が `$("id")` で探す (`tests/test_web_ui.py` が確かめる)。`<select id="preset">` などは `tests/test_web_parity.py` が正規表現で読むので、`<select id="...">` の形 (属性を足さない) のまま。
- **設定の項目と選択肢を消さない。** 見せ方を変えても、元の `<input>`・`<select>` は残し、値を変えたら `input`・`change` を起こして app.js の処理を通す。
- **ブラウザの外に送らない。** 動画・画像をサーバーに送る仕組み、外部の CDN・フォント・解析を足さない (今ある外部の読み込みは、変換の本体 Pyodide の jsDelivr だけ。`usage.js` の利用数は設定したときだけ)。
- **GitHub Pages でそのまま動くこと。** ビルドの手順を足さない (`scripts/build_web.py` が web/ を写すだけ)。npm のライブラリとフォントは `web/vendor/` に写して使う。パスは相対。
- **架空の表示を出さない。** 進み具合は `<progress>` の実際の値と worker のログのキーだけ。数字・件数・評判・性能の作り物を置かない。ページの見本は本物の変換の出力 (6 章)。プライバシーの欄は実際の動きだけを書く。
- **多言語。** 画面の文はすべて `data-i18n` (と `data-i18n-title`・`data-i18n-placeholder`) で `locales/*.json` の 10 言語から入れる。新しいキーは 10 言語に同時に足し、使わなくなった `web.*` のキーは外す。`data-i18n` の要素の中には子の要素を入れない (アイコンは兄弟にする)。

## 2. デザインの方向

Premium Creative Technology × Dark Graphite × Modern Developer Tool。**主役は 1 つ**: 最初の画面の舞台 (元の映像と、本物の変換結果が同期して動く)。それ以外は静かに保つ。

| 種類 | 変数・値 |
|---|---|
| 背景 | `--bg #0c0d10`、交互の節 `--bg-alt #101116` |
| 面 | `--surface #14151b`、`--surface-2 #1a1c23`、`--surface-3 #23262f` (半透明にしない) |
| 線 | `--line` (白 8%)、`--line-strong` (白 15%) |
| 文字 | `--text #f2f3f7`、`--text-2 #c4c7d2`、`--muted #8e92a3` |
| アクセント | `--electric #2f6bff` → `--violet #8a5cff` (`--accent-grad`)。文字・輪・線には単色の `--accent #7d95ff` |
| 状態 | `--ok #46d9a0` (出来た・端末に残る)、`--warn`、`--danger` |
| 角 | `--radius-s 4px`・`--radius 8px`・`--radius-l 12px`。ブロックに合わせて小さく。丸い印・999px の丸薬は使わない |
| 影 | `--shadow` (浮かせる面だけ: 舞台・右の作る欄・流れ図の中央)、`--shadow-pop` (吹き出し・知らせ) |

- **グラデーションは** 主ボタン・手順の番号の印・進み具合・ナビの今いる節の線・舞台の下の光だけ。飾りには使わない。
- **すりガラスは** スクロールしたナビだけ。
- **同じカードを並べない。** 節ごとに形を変える: 見本 (絵 + 操作)、流れ図 (線でつなぐ)、手順 (上の線と大きな番号)、端末から出るもの (線で区切った 3 列)、変換ツール (面のパネル)。パネルの中に枠付きの箱を重ねない (設定は「左に名前・右に入力欄」の行を線で区切る)。
- 新しい色・影・角を直接書かない。足すなら :root に変数を作る。

### 文字

- 見出しは `--display` (Bricolage Grotesque。`web/vendor/fonts/`、ラテン文字だけ。日本語などはシステムのゴシックに落ちる)。本文は `--font` (システムのサンセリフ)。数字の表示は `--mono`。外部のフォントは読み込まない。
- 階層: 最初の見出し `clamp(2.5rem, …, 5.6rem)`・650・字間 −0.035em・行間 0.98 (日中韓は 700・行間 1.14) / 節の見出し `clamp(1.9rem, …, 3.1rem)`・600 / パネルの見出し 1.3rem / 本文 16px・行間 1.6 / ラベル 13px・550・`--muted` / 補足 13.5〜14px。
- 見出しの上に小さなラベル (全部大文字の eyebrow) を置かない。番号は本当に順番のある所だけ (手順・変換ツールの 1〜4)。
- 見出しに `text-wrap: balance`、説明に `text-wrap: pretty`。日本語の見出しは `word-break: auto-phrase`。

### 余白と配置

- 節の上下は `--section` (`clamp(72px, 10vw, 136px)`)、横は `--gutter`、中身の幅は `.wrap` の 1240px。左ぞろえ。
- 4px の倍数。パネルの内側 `clamp(18px, 2.4vw, 28px)`、パネルの間 20px、設定の行の上下 22px、入力欄の間 14px。
- 入力欄の高さ 40px、ボタン 40〜50px、タッチの的は 32px 以上。

## 3. ページの組み立て

ナビ (今いる節の印・狭い画面ではメニュー) → 最初の画面 (見出し、説明とボタン、舞台と床) → `#showcase` 設定の見本 → `#inputs` 入力と出力の流れ → `#how` 4 つの手順 → `#privacy` 端末から出るもの → `#convert` 変換ツール (左に 1 選ぶ・2 設定、右に張り付く 3 作る、下に 4 プレビュー) → フッター。

## 4. 動き

- 速さは 3 つ (`--dur-1 120ms`・`--dur-2 220ms`・`--dur-3 380ms`)、曲線は 2 つ (`--ease`・`--ease-spring`)。JS では `ui.js` の `animate(el, keyframes, options)` と `SPRING`・`EASE`・`QUICK` だけを使う。
- **自動で動くのは、読み込んだときの 1 回だけ** (見出し → 説明 → 舞台、境目がすべる) と、流れ図の線 (見えたときに 1 回)。節ごとに下から出す動きは付けない (中身をスクロールするまで隠さない)。
- それ以外は操作に応える動きだけ: 選んだ所へ動く下地、出る/消える (カード・知らせ・結果)、押した、落とした、段階が進んだ、開いた、絵が替わった。
- 動かすのは `opacity` と `transform` だけ (例外: 切り替えの帯の下地)。
- `prefers-reduced-motion: reduce` では `hasMotion()` が false。最初の画面を隠さず、舞台の映像は押すまで再生せず、CSS の動きは 1ms。新しい動きも必ずこの道を通す。
- Motion が無くても動くこと。Motion で動かす要素には CSS の同じ動きを用意し、`.has-motion` のときだけ CSS を止める。
- 5 秒より長く動き続けるもの (舞台の映像) には止めるボタンを付け、画面の外では止める。

## 5. Vanilla JavaScript の作り

- スクリプトは `<script src>` の順に読む普通のスクリプト。各ファイルは `const X = (() => { … })();` で包み、トップレベルに `const $` などを置かない (app.js の `$`・`t` とぶつかる)。
- 読む順: config → i18n → usage → mp4audio → block_preview → preview → camera → mmd → `vendor/lucide/icons.js` → ui → app。Motion は `<head>` で `defer`。
- 画面の部品を足すときは ui.js の中に `setupXxx()` を作り `init()` から呼ぶ。状態は DOM の属性で持つ (`data-state`・`aria-checked`・`aria-current`・`hidden`)。翻訳で作り直す部品は `I18n.onChange` に登録する。まとめて直す更新は `schedule()`。
- ライブラリ: 版は `package.json` / lock で固定し、`npm ci` → `npm run vendor:web` で `web/vendor/` に写す (README の SHA-256 をテストが確かめる)。**Motion** は `Motion.animate`・`inView`・`stagger`・`press` だけ (React 用は使わない。API は Context7 で確かめる)。**Lucide** は使うアイコンだけ (`<i data-lucide="名前" class="icon" aria-hidden="true"></i>` か `UI.icon("名前")`。足したら `ICONS` に足して写し直す。テストが両方向を確かめる)。ブランドのロゴは Lucide に無いので SVG をそのまま書く。絵文字や記号で UI を表さない。ほかのライブラリは足さない。

## 6. ページの見本は本物の出力

`scripts/make_web_media.py` が見本の映像を描き、実際の `convert.py` に通して `web/media/` に置く (元の映像・その出力の FRAME_DATA・設定ごとの 1 コマ)。舞台は `BlockPreview` とゲームと同じ `codec.js` で復元して描く。変換の既定や減色を変えたら動かし直し、説明の文 (`web.stage_caption`) の値と合わせる。似せた絵・手で描いた「変換後」を置かない。

## 7. アクセシビリティ・スマートフォン・速度

- 操作できるものは `<button>`・`<a>`・`<input>`・`<select>`。切り替えの帯は `role="radiogroup"`/`radio` + `aria-checked` + 矢印キー。舞台の境目は `role="slider"` + 矢印キー。アイコンだけのボタンには `aria-label`。`:focus-visible` の輪を消さない。ツールチップは focus でも出し Esc で消える。知らせは `aria-live`、失敗は `role="alert"`。先頭に変換ツールへ飛ぶリンク。
- 区切りは 1040px (ナビをメニューに、2 列を 1 列に、右の作る欄の張り付きをやめる) と 640px。横にはみ出さない (`document.documentElement.scrollWidth <= innerWidth`)。長い翻訳 (ドイツ語・ロシア語) でも折り返す。
- 最初に読むのは同じサイトの小さなファイルだけ。舞台の映像と FRAME_DATA は後から読み、読めないときは最初のコマの絵のまま。`web/media/` は合計 400 KB 未満 (テストが確かめる)。

## 8. 見た目の確認 (Playwright)

画面を変えたら、必ず実際に開いて確かめる。機能の確認と見た目の確認は分けて行う。

1. `python scripts/build_web.py --out <出力>` で組み立て、キャッシュしないサーバーで開く。
2. Playwright MCP で幅 1440・820・390 を撮る (`browser_resize` → `browser_navigate` → `browser_take_screenshot`、全体は `fullPage`)。撮った絵を実際に見て、問題を自分で挙げて直し、撮り直す。**少なくとも 3 回**くり返す。
3. 見る所: 最初の 1 画面の強さ、節ごとの形の違い、余白のそろい、文字の折り返し (日本語・英語・ドイツ語)、横のはみ出し、コンソールのエラー。
4. 操作: ナビ (今いる節・メニュー)、舞台 (境目・止める)、見本の切り替えと「この設定を使う」、ファイルを落とす (DataTransfer で `drop`)、作る → 段階 → できたファイル → プレビュー。
5. `browser_emulate_media` で `reducedMotion: "reduce"` にして開き直し、隠れた中身が無いこと・映像が自動で動かないことを確かめる。
6. できたアドオンは `python scripts/play_addon_mock.py <file>` でモックの再生まで確かめる。

## 9. 終える前に

- `python -m pytest -q tests/test_web_ui.py tests/test_web_i18n.py tests/test_web_parity.py tests/test_i18n.py` が通る (最後は全部のテスト)。
- 画面の変更は `CHANGELOG.md` の「未リリース」に書く。確かめていないブラウザ・端末は「未確認」と書く。
