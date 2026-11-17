# 全体の監査の最終報告 (2026-11-17)

対象は `main` の 3171f85。作業はブランチ `audit/full-qa` で行った。
途中の記録は [AUDIT_PROGRESS.md](AUDIT_PROGRESS.md) にある。

## A. 総合評価

**作業前の状態**
- テストは 1068 件で、CI は 3 つのジョブ (Ubuntu・Windows・Pyodide) で回っていた。符号化と復元の往復、Minecraft のモックでの再生、画像モードの CLI の引数の検査は、どれも厚く作られていた。
- 弱かった所:
  - ブラウザ版・Windows 版・組み立ての処理で、入力の検査がそろっていない (ブラウザ版だけ接頭辞 `minecraft` を通す。組み立ては呼び出し側の検査に頼っていた)
  - `.mcaddon` を出力先へ直接書く
  - ファイルの中身を信じて読む所がある (.vox のアニメの範囲)
  - 依存の下限が古い (Pillow 10)
  - ゲーム内のコマンドで、Object の性質の名前を動画と取り違える

**作業後の状態**
- 13 件を直し、テストを 30 件足した (1098 件)。
- 変換の出力は直す前とバイト単位で同じ (md5 一致)。速さとメモリは誤差の範囲。
- Critical・High の問題は見つからなかった。
- 最終判定は **READY WITH LIMITATIONS** (I を参照)。

## B. 発見した問題

| ID | 重要度 | ファイル | 問題 | 根本原因 | 対応状況 |
|---|---|---|---|---|---|
| AUD-01 | Medium | web/app.js | ブラウザ版だけ、接頭辞 `minecraft` で作れてしまい、コマンドを登録できないアドオンになる | 予約された名前の検査が Windows 版と CLI にしか無かった | 修正済み |
| AUD-04 | Medium | requirements.txt | `pillow>=10.0` が、13.0.0 で直った脆弱性 (細工した画像での範囲外の読み書き・DoS) のある版を許す | 依存の下限を更新していなかった | 修正済み (`>=13.0`) |
| AUD-10 | Medium | pack_builder.py・convert_image_mode.py | `.mcaddon` を出力先へ直接書く。失敗すると前のファイルを壊す。まとめる処理は、ブロックモードを壊したまま「別のファイルで残す」と知らせていた | 一時ファイルを経由しない書き込み | 修正済み |
| AUD-02 | Low | pack_builder.py | 組み立てが動画 ID と接頭辞を検査しない。漏れると JS・zip のパスを壊す。接頭辞の行が無くても黙って `badapple` のまま作る | 検査が呼び出し側にだけあった | 修正済み |
| AUD-03 | Low | convert.py | `--duration` が 0 以下、`--extrude-threshold` が 0〜1 の外でも受け付ける | 引数の検査漏れ | 修正済み |
| AUD-05 | Low | mvcodec/stream.py | データの大きさの見積りが UTF-16 の頃のまま | 形式を変えたときの直し漏れ (結果は同じ) | 修正済み |
| AUD-06 | Low | docs/TESTING.md | テストの時間が「約 20 秒」(今は約 3 分) | 文書の更新漏れ | 修正済み |
| AUD-07 | Low | build_standalone.py | 説明の出力先が違う、置き換えの無い f 文字列、使わない import | 文書と実装のずれ | 修正済み |
| AUD-08 | Low | convert.py | `hashlib.md5()` を `usedforsecurity=False` なしで使う (FIPS の Python で例外) | 安全用途でないことを書いていなかった | 修正済み |
| AUD-09 | Low | mvcodec/volume.py | .vox のアニメの範囲が巨大・逆だと固まる (直す前のコードで 20 億フレームの範囲を読ませると実際に固まった) | ファイルの値でそのまま一覧を作っていた | 修正済み (上限 100,000) |
| AUD-11 | Low | main.js | `play constructor` などを動画と取り違え、保存し、再生でエラーになる | ふつうのオブジェクトを `obj[id]` で引いていた | 修正済み |
| AUD-12 | Low | web/app.js | 作っている途中に名前・接頭辞を変えると、完成の表示が違う値になる | 完成の時点で入力欄を読み直していた | 修正済み |
| AUD-13 | Low | 2 つのワークフロー | 手動実行の入力をスクリプトに直接埋め込む | GitHub の推奨 (env で渡す) に沿っていなかった | 修正済み |

## C. 実施した修正

| ファイル | 内容 | 理由 | 影響範囲 |
|---|---|---|---|
| pack_builder.py | `check_names`・`RESERVED_NAMESPACES`・`NAMESPACE_LINE` の確認、`write_file_atomic` | AUD-02・AUD-10 | Windows 版・ブラウザ版・サンプルの作り直し (scripts/repack_addon.py)。正しい名前の出力は同じ |
| convert_image_mode.py | 出力とまとめる処理を `write_file_atomic` に | AUD-10 | 画像モードの CLI・Windows 版 |
| convert.py | `--duration`・`--extrude-threshold` の検査、MD5 に `usedforsecurity=False` | AUD-03・AUD-08 | CLI。GUI はもともと 0 以下を渡さず、ブラウザ版は `--duration` を渡さない |
| mvcodec/volume.py | `MAX_VOX_FRAMES`、範囲が逆なら断る | AUD-09 | .vox の読み込み (CLI・GUI・ブラウザ版) |
| mvcodec/stream.py | `payload_size` の見積り | AUD-05 | 比べる順位は同じ (出力の md5 で確認) |
| main.js | `Object.prototype.hasOwnProperty.call(VIDEOS, id)` | AUD-11 | ゲーム内の `play` (旧版向けの出力も同じ行) |
| web/app.js | 作り始める前に予約された名前を断る、`buildSettings` | AUD-01・AUD-12 | ブラウザ版の作成 |
| requirements.txt | `pillow>=13.0` | AUD-04 | pip で入れる環境 (EXE のリリースはもともと最新を入れる) |
| .github/workflows/real-video-bench.yml・release.yml | 入力を env で渡す | AUD-13 | 手動実行のときだけ |
| build_standalone.py・docs/TESTING.md・README.md・CHANGELOG.md | 説明を実装に合わせた、変更履歴 | AUD-06・AUD-07・Phase 8 | 文書だけ |
| tests/test_audit_fixes.py (新規)・tests/test_custom_commands.py | 回帰テスト 30 件 | 全部の修正 | テストだけ |

## D. テスト結果

| テスト | 実行結果 | 件数 | 備考 |
|---|---|---|---|
| pytest 全部 (直す前) | 成功 | 1068 passed / 11 skipped / 3 xfailed | 193 秒 |
| pytest 全部 (直した後) | 成功 | 1098 passed / 11 skipped / 3 xfailed | 164 秒。skip は torch (8)・scikit-image (2) が入っていないため。xfail は既知の投影の限界 |
| pytest 全部 (Pillow 13.0.0 の venv) | 成功 | 1066 passed / 13 skipped / 3 xfailed | 直す前のコードで実行。skip が 2 件多いのは PyYAML が無いため |
| 新しい回帰テスト (直す前のコードに対して) | 期待どおり失敗 | 26 失敗 / 3 成功 | 成功の 3 件は正しい値を受け付けるテスト。巨大な .vox の範囲は固まったので止めた |
| Pyodide のスモークテスト (CI の web ジョブと同じ) | 成功 | 平面・relief・vox・殻・画像・平面版 | Node 24・pyodide 0.26.4 |
| ブラウザ版 (Chromium、内蔵ブラウザ) | 成功 | 1 | 接頭辞 `minecraft` で作ると、作り始める前にエラーの文が出て、ボタンが戻る |
| EXE のビルドと、EXE での 1 枚の変換 | 成功 | 1 | release.yml の確認と同じ手順。手元の Pillow は 12.3.0 |
| CLI の境界値・異常系 | 実行 | convert.py 13 通り、画像モード 15 通り | 見つけた問題は AUD-03・AUD-09 |
| ruff (F・E9・B・PLE・PLW) | 実行 | 本体の指摘は意図的な書き方だけ | テストの F811 は pytest のフィクスチャの読み込み |
| bandit | 実行 | High 0 (直す前は MD5 の 1) | 残りは subprocess・try/except/pass で、確認済み |
| vulture | 実行 | 確実な未使用は 1 (直した) | 60% の候補は、テスト・ブラウザ版・ベンチマークから使われている |
| pip-audit (requirements を解決) | 成功 | 0 件 | |
| pip-audit (この PC の環境) | 指摘あり | Pillow 12.3.0 ほか | G の U-2 |
| npm audit | 成功 | 0 件 | |
| node --check (vendor 以外の全 JS) | 成功 | 全ファイル | |
| YAML の読み込み (4 つのワークフロー) | 成功 | 4 | GitHub での実行は未実施 (G の U-4) |
| 変換の速さ (前後 3 回ずつ、中央値) | 実行 | 2 種類 | F を参照 |
| GPU (CUDA・ROCm・DirectML) の経路・torch の 8 件 | 未実施 | - | この PC の Python に torch が無い。CI は CPU 版の torch で実行している |
| スマートフォン・Safari・Firefox | 未実施 | - | 実機・他ブラウザが要る |
| Minecraft の実機 | 未実施 | - | 対象外 (H を参照) |

## E. セキュリティ

**見つけた脆弱性と、直したこと**
- 依存の Pillow の下限 (AUD-04): `>=13.0` にした。
- 組み立てでの注入の余地 (AUD-02): 動画 ID と接頭辞を組み立ての中で検査するようにした。今の呼び出し側はどれも検査しているので、実際に悪用できる経路は見つからなかった。
- 細工した .vox による DoS (AUD-09): 範囲に上限を設けた。
- ワークフローのスクリプトへの埋め込み (AUD-13): 入力を env 経由にした。書き込み権限のある人の入力だけが対象。

**問題が無かったもの**
- HTML への書き込み: `innerHTML` は空にするだけ。
- Python への値の受け渡し: `toPy`・globals で渡し、文字列に埋め込まない。
- モデルのダウンロード: https の固定の URL で、SHA-256 を確かめている。
- 秘密情報: ソースに書かれていない。
- 外部への送信: GoatCounter の回数だけで、既定は off。

**残っているリスク**: G の U-2 と U-6。

## F. パフォーマンス

| 計測 | 直す前 | 直した後 |
|---|---|---|
| ブロックモード (320×180・10 秒、recommended、CPU) | 2.93 秒・98 MB | 2.83 秒・98 MB |
| 画像モード (同じ動画、既定) | 1.17 秒・140 MB | 1.22 秒・140 MB |
| 全テスト | 193 秒 | 164 秒 |

- どちらも同じ PC での 3 回の中央値で、差は誤差の範囲。速くするための変更はしていない。
- 出力の .js は直す前とバイト単位で同じ (md5 `6f37ce5f…`)。
- 書き込みを一時ファイル経由にした分の費用は、出力の大きさ 1 回分の書き込みと置き換えで、計測では見えなかった。
- 未検証: GPU の変換、長い動画 (数分以上) のメモリ、ゲーム内の描画の速さ。

## G. 未解決事項

| # | 問題 | 原因 | 今後の対応 |
|---|---|---|---|
| U-1 | 1 行ずつ読み切っていないファイルがある | 量が多い。数値処理 (色・RDO・動き・区間符号・codec.js の復元)、GUI の部品、ブラウザ版の UI・プレビュー・カメラ、scripts、benchmark | 静的解析・1098 件のテスト・境界値の実行では確かめた。次の監査で、読んでいない順に見る (AUDIT_PROGRESS.md の「未調査」) |
| U-2 | この PC の Python の Pillow が 12.3.0 (脆弱性あり) | 環境を変える操作なので実行していない (**要確認**) | `python -m pip install -U "pillow>=13"`。手元で EXE を作る前に。リリースの CI は新しく入れるので影響なし |
| U-3 | torch・scikit-image のテスト 11 件を手元で実行していない | 依存が入っていない | CI (CPU 版 torch) で実行される |
| U-4 | ワークフローの 2 つの変更は、YAML の読み込みでしか確かめていない | GitHub でしか動かない | 次のリリースと、手動のベンチマークの実行で見る |
| U-5 | PR #145 と、コミットしていない画面の作り直しが、このブランチと同じ所を変えている (convert_image_mode.py の書き込みの行、CHANGELOG) | 並行した作業 | 2 つ目に入れる方で、書き込みを `write_file_atomic` にそろえて直す。画面の作り直しの小さな問題 (最初の作成時に `minsize` が DPI の倍率を使わない) も記録 |
| U-6 | ブラウザ版は Pyodide を jsDelivr から読み、SRI を付けていない | Worker の `importScripts` に SRI が無い | ページに明記済み。自分のサイトに置くかは別の判断 |
| U-7 | GUI の作成のスレッドが Tk の変数を読む | 標準の Python (スレッド対応の Tcl) では動く | 直すと動きが変わる恐れがあるので、今回は変えていない |
| U-8 | ブラウザ版の中の Python (Pyodide) で `.vox` の範囲の上限に達するときの表示は、手元のテストでしか見ていない | - | ブラウザ版で壊れた .vox を読み込んで、エラーの文を見る |

## H. 実機テストチェックリスト

1. **10 分のチェック**: docs/TESTING.md の 2 章 (B1〜B8・I1〜I7・C1〜C3) を、このブランチで作ったアドオンで行い、2.0 の表に書く。
2. **動画 ID の取り違え (AUD-11)**:
   1. チートを有効にしたワールドで `/scriptevent badapple:setup` を実行する。
   2. `/scriptevent badapple:play constructor` を実行し、「動画が見つかりません」が出ることを確かめる。
   3. `/badapple:start` を実行し、元の動画が最後まで再生されることを確かめる。
   4. コンテンツログにスクリプトのエラーが無いことを確かめる。
3. **書き込み (AUD-10)**:
   1. Windows 版で、同じ保存先に 2 回作る。画像モードを作る・まとめる設定にする。
   2. 2 回目の `.mcaddon` を取り込めることを確かめる。
   3. 保存先のフォルダに `.….part` のファイルが残っていないことを確かめる。
   4. 保存先を OneDrive の中にしても同じことを確かめる。
4. **ブラウザ版 (AUD-01・AUD-12)**:
   1. スマートフォン (iPhone の Safari・Android の Chrome) で、接頭辞を `minecraft` にして作る。変換が始まる前にエラーが出ることを確かめる。
   2. 正しい接頭辞で作り始め、途中で名前と接頭辞を書き換える。ダウンロードの名前と案内のコマンドが、作り始めたときの値であることを確かめる。
5. **Pillow 13 の EXE (AUD-04)**: Pillow 13 の環境 (またはリリースの CI) で作った EXE で C3 を行う。
6. **ワークフロー (AUD-13)**: 次のリリースで、EXE の zip の名前が `BlockVideoPlayer-vX.Y.Z-windows-x64.zip` になることを確かめる。real-video-bench を baseline を入れて手動で実行し、比べる版が入力どおりになることを確かめる。

## I. 最終判定

**READY WITH LIMITATIONS**

直せる問題はすべて直し、自動で確かめられることはすべて確かめた。ただし次の制約がある:
- U-1: 数値処理などの一部のファイルは、1 行ずつは読み切っていない。
- U-2: この PC の Pillow を上げるのは要確認。
- U-4: ワークフローの変更は GitHub で動かしていない。
- 実機での確認 (H) がまだ。
