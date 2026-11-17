# 監査の進み具合 (Comprehensive Project Audit)

作業が途中で止まっても続きから始められるよう、ここに今の状態を書いておく。
再開するときは、このファイルと `git status` / `git diff` を見てから始める。

## 前提

| 項目 | 内容 |
|---|---|
| 監査する版 | `origin/main` の 3171f85 (#144 まで) |
| 作業場所 | 専用のワークツリー、ブランチ `audit/full-qa` (main から切った) |
| 対象外 | 実機 (Minecraft・スマホ・ゲーム機) での確認 |
| 別に見るもの | まだ main に入っていない PR #145 (立体のだまし絵の試作) と、別のワークツリーにあるコミットしていない Windows 版の画面の作り直し。どちらも読むだけにし、見つけた問題はここに書く (そのワークツリーは触らない) |
| 監査用の道具 | ruff・bandit・pip-audit・vulture・mypy をスクラッチの venv に入れて使う (リポジトリと Python 本体には入れない) |
| 守ること | 既定ブランチへ直接コミットしない / 未コミットの変更を捨てない / `git reset --hard`・`git clean -fd` をしない / 元に戻せない操作はせず「要確認」と書く / テストを消したり警告を隠したりしない |

## 現在の Phase

**完了 (最終報告は [AUDIT_REPORT.md](AUDIT_REPORT.md)。判定 READY WITH LIMITATIONS)**

## Phase の状態

| Phase | 状態 | メモ |
|---|---|---|
| 0 把握 | 済み | 構成・CI・依存・テストの基準を記録 |
| 1 コード監査 | 済み (一部は静的解析とテストで代替) | 変換・組み立て・符号化・main.js のコマンド・web の作成の流れは読んだ。数値処理の内部などは AUDIT_REPORT の U-1 |
| 2 セキュリティ | 済み | 注入・ダウンロード・依存・ワークフロー |
| 3 性能 | 済み | 前後 3 回ずつ。差は誤差、出力は同じ |
| 4 依存関係・環境 | 済み | pip-audit・npm audit・Pillow 13 で全テスト |
| 5 テスト | 済み | 30 件追加、Pyodide・ブラウザ・EXE も |
| 6 修正 | 済み | 13 件 |
| 7 再検証 | 済み | 全テスト・ruff・bandit・node・diff |
| 8 文書・報告 | 済み | CHANGELOG・README・TESTING・AUDIT_REPORT |

## 調査済みファイル

- `convert.py` (全部)、`pack_builder.py`、`pack_metadata.py`、`gui_i18n.py`、`legacy_target.py`、`conversion_profiles.py`、`build_standalone.py`、`mvcodec/encode.py`、`mvcodec/stream.py`
- `codec.js` の varint とランの復元 (32 bit を超える値の扱いは正しい)
- `mvcodec/volume.py` の .vox の読み込み、`mvcodec/mmd.py` の PMX の読み込み (先頭)、`mvcodec/person.py` のダウンロード (URL は定数・https・SHA-256 で確認)、`mvcodec/resources.py`
- `main.js` のコマンドの登録と `runAction`、保存した値の読み直し
- `web/app.js` (作成・完成・失敗)、`web/worker.js` (Python へは toPy・globals で渡し、文字列に埋め込まない)、HTML への書き込み (`innerHTML` は空にするだけ)
- `video_player_gui.py` のスレッド・一時フォルダ・メッセージの流れ、`gui_wizard.py` の外部プロセス
- Pillow 13.0.0 で全テスト: 1066 passed / 13 skipped (yaml が無い 2 件が増えただけ) / 3 xfailed
- CLI の境界値: convert.py 13 通り、convert_image_mode.py 15 通り (画像モードは検査がそろっている)

## 未調査ファイル

Python: `convert.py` `convert_image_mode.py` `video_player_gui.py` `gui_wizard.py` `gui_i18n.py` `pack_builder.py` `pack_metadata.py` `legacy_target.py` `conversion_profiles.py` `build_standalone.py` `mvcodec/*.py` (22) `image_mode/*.py` (4) `scripts/*.py` `benchmark/**/*.py`
JS: `main.js` `codec.js` `image_mode/image_main.js` `web/*.js` `web/ogg-worker.js` `web/worker.js` `scripts/vendor_web_libs.mjs` `benchmark/js/*.mjs` `tests/js/*.mjs`
設定: `manifest.json` `package.json` `package-lock.json` `requirements*.txt` `.github/workflows/*.yml` `.gitignore` `.gitattributes` `web/index.html` `web/style.css` `locales/*.json`
文書: `README.md` `ARCHITECTURE.md` `DECISIONS.md` `PROJECT_STATUS.md` `TODO.md` `CHANGELOG.md` `docs/*.md` `image_mode/SPEC.md`

## 発見した問題

| ID | 重要度 | ファイル | 問題 | 状態 |
|---|---|---|---|---|
| AUD-01 | Medium | web/app.js | ブラウザ版だけ、コマンドの接頭辞に `minecraft` を受け付ける (Windows 版・画像モードの CLI は断る)。`/minecraft:setup` は登録できず、アドオンが操作できない | 修正済み (再検証済み) |
| AUD-02 | Low | pack_builder.py | `build_addon` が動画 ID と接頭辞を検査しない。呼び出し側が検査し忘れると、`"` で videos.js / main.js が壊れ、`../` で zip の中のパスがフォルダの外を指す。main.js の `EVENT_NAMESPACE` の行が無いと、黙って接頭辞が badapple のまま | 修正済み (再検証済み) |
| AUD-03 | Low | convert.py | `--duration` 0 以下と `--extrude-threshold` の 0〜1 の外を断らない (-1 は ffmpeg の分かりにくいエラー、5 は空の立体を黙って作る) | 修正済み (再検証済み) |
| AUD-04 | Medium | requirements*.txt | `pillow>=10.0` が、13.0.0 で直った脆弱性 (細工した PNG・GIF・JPEG・TIFF などで範囲外の読み書き・DoS) のある版を許す。この PC の 12.3.0 も該当し、手元で作る EXE に同梱される (リリースの CI は新しく入れるので 13.x) | 修正済み (再検証済み) |
| AUD-05 | Low | mvcodec/stream.py | `payload_size` の説明と `* 2` が UTF-16 の頃のまま (今は base64 で 1 文字 1 バイト)。全部の GOP を閉じてから比べるので結果は変わらない | 修正済み (再検証済み) |
| AUD-06 | Low | docs/TESTING.md | 「`pytest -q` (約 20 秒)」とあるが、今は手元で約 190 秒 | 修正済み (再検証済み) |
| AUD-07 | Low | build_standalone.py | 説明の出力先が `dist/BlockVideoPlayer.exe` (実際は `dist/BlockVideoPlayer/BlockVideoPlayer.exe`)、置き換えの無い f 文字列、使わない import | 修正済み (再検証済み) |
| AUD-08 | Low | convert.py | パレットのキャッシュの鍵に `hashlib.md5()` を `usedforsecurity=False` なしで使う (FIPS の Python では例外になる。安全用途ではない) | 修正済み (再検証済み) |
| AUD-09 | Low | mvcodec/volume.py | .vox のシーンの `_anim_range` やキーフレームの `_f` がとても大きいと、フレーム番号の一覧を際限なく作る (細工したファイルで固まる・メモリを使い切る。ブラウザ版のタブも固まる) | 修正済み (再検証済み) |
| AUD-10 | Medium | pack_builder.py / convert_image_mode.py | `.mcaddon` を出力先へ直接書く。途中で失敗 (ディスクがいっぱいなど) すると、前のファイルを壊れたもので上書きする。`combine_addons` はブロックモードのファイルをその場で書き直すので、失敗したとき「別ファイルのまま残す」と知らせるのに、ブロックモードの方が壊れている | 修正済み (再検証済み) |
| AUD-11 | Low | main.js | `/scriptevent <接頭辞>:play constructor` などの組み込みの名前を、動画があると取り違える (`VIDEOS[videoId]` が Object の性質を拾う)。選んだ ID が保存され、再生でスクリプトのエラーになる | 修正済み (再検証済み) |
| AUD-13 | Low | .github/workflows/real-video-bench.yml・release.yml | 手動実行の入力 (`baseline`・`tag`) をスクリプトに直接埋め込む (`${{ }}`)。書き込み権限のある人の入力だけだが、GitHub の推奨は環境変数で渡すこと | 修正済み (再検証済み) |
| AUD-12 | Low | web/app.js | 作り終えたときのファイル名と画像モードの案内の接頭辞を、そのときの入力欄から読む。作っている途中に書き換えると、作ったものと違う名前・コマンドを出す | 修正済み (再検証済み) |

## 修正済み問題

| ID | 直したこと | テスト |
|---|---|---|
| AUD-01 | web/app.js: 作り始める前に接頭辞 `minecraft` を断り、Windows 版と同じ文 (error.namespace) を出す | test_audit_fixes (一覧が pack_builder と同じ・worker より前に断る) |
| AUD-02 | pack_builder.py: `check_names` で動画 ID と接頭辞を確かめ、main.js に接頭辞の行が無ければ断る | test_audit_fixes (ID 8 通り・接頭辞 5 通り・行なし・正しい名前) |
| AUD-03 | convert.py: `--duration` は 0 より大きく、`--extrude-threshold` は 0〜1 | test_audit_fixes (4 通り + 端の値) |
| AUD-04 | requirements.txt: `pillow>=13.0` (理由を書いた)。Pillow 13 で全テストが通ることを確かめた | Pillow 13.0.0 の venv で全テスト |
| AUD-05 | mvcodec/stream.py: `payload_size` を今の形 (base64 は 1 文字 1 バイト) に | 変換の出力が変わらないこと (md5) |
| AUD-06 | docs/TESTING.md: テストの件数と時間 | - |
| AUD-07 | build_standalone.py: 説明の出力先、f 文字列、使わない import (find_spec に) | ruff |
| AUD-08 | convert.py: `hashlib.md5(..., usedforsecurity=False)` | bandit |
| AUD-09 | mvcodec/volume.py: アニメの範囲が逆・`MAX_VOX_FRAMES` (100,000) 超なら作る前に ValueError | test_audit_fixes (直す前は 2,000,000,000 で固まった) |
| AUD-10 | pack_builder.py の `write_file_atomic` (同じフォルダの一時ファイル → os.replace)。build_addon・convert_image_mode の 2 か所で使う | test_audit_fixes (置き換えに失敗しても前のファイルのまま・.part が残らない・まとめる失敗でブロックモードが壊れない) |
| AUD-11 | main.js: `Object.prototype.hasOwnProperty.call(VIDEOS, videoId)` | test_custom_commands (直す前は失敗) |
| AUD-12 | web/app.js: 作り始めたときの設定 (`buildSettings`) で完成を表示 | test_audit_fixes |
| AUD-13 | 2 つのワークフローで、入力を env 経由に (`$BASELINE_INPUT`・`$env:TAG`) | YAML の読み込み・test_release_tools |

直す前のコード (3171f85) に新しいテストを流すと、正しい値を受け付けるテスト 3 件だけ通り、ほかは全部落ちた (大きなアニメの範囲は固まったので止めた)。

## 未修正問題

コードの問題は無し。残りの制約は AUDIT_REPORT.md の G (U-1〜U-8)。要確認: この PC の Pillow 12.3.0 を 13 以上に上げる (環境の変更なので実行していない)。

## 実行済みテスト

| いつ | コマンド | 結果 |
|---|---|---|
| Phase 0 (変更前の基準) | `python -m pytest -q -rsxX` (Python 3.13.14、torch・scikit-image なし) | 1068 passed / 11 skipped / 3 xfailed、193 秒。skip は torch (8) と skimage (2) が無いため、xfail は既知の投影の限界 (KNOWN_ISSUES) |
| Phase 0 | `node --check` (vendor 以外の全 .js / .mjs) | 全部通った |
| Phase 0 | ruff (F, E9, B, PLE, PLW) | 本体のコードに実害のある指摘は無し (未使用の import・変数、ループ変数の上書きなど。後で 1 件ずつ確かめる) |
| Phase 0 | bandit (tests・benchmark を除く) | High 1 (convert.py の MD5。キャッシュの鍵で安全用途ではない)、Medium 1 (person.py の urlopen)、Low は subprocess と try/except/pass |
| Phase 0 | pip-audit (requirements を新しく解決) | 脆弱性なし |
| Phase 0 | pip-audit (この PC に入っている版) | Pillow 12.3.0 に 13.0.0 で直った脆弱性が多数。aiohttp・dnspython・setuptools・uv もあるが、このプロジェクトの依存ではない |
| Phase 0 | `npm audit --package-lock-only` | 0 件 |
| Phase 4 | 全テスト (Pillow 13.0.0 の venv、直す前のコード) | 1066 passed / 13 skipped (PyYAML なしの 2 件) / 3 xfailed |
| Phase 5 | 新しいテストを直す前のコードに | 26 失敗 / 3 成功 (正しい値の 3 件)。巨大な .vox の範囲は固まったので止めた |
| Phase 7 | 全テスト (直した後) | 1098 passed / 11 skipped / 3 xfailed、164 秒 |
| Phase 7 | Pyodide のスモークテスト | 成功 |
| Phase 7 | ブラウザ版 (Chromium) で接頭辞 minecraft | 作り始める前にエラー、ボタンが戻る |
| Phase 7 | EXE のビルドと 1 枚の変換 | 成功 |
| Phase 7 | 速さ (前後の中央値) | ブロック 2.93 → 2.83 秒、画像 1.17 → 1.22 秒、メモリ同じ、出力の md5 同じ |
| Phase 7 | ruff・bandit・node --check・git diff --check | High 0、構文 OK、空白 OK |

## 失敗したテスト

無し (直した後)。直す前のコードで新しいテストが落ちるのは、問題を捕まえている確認として意図どおり。

## 変更したファイル

- `AUDIT_PROGRESS.md` (このファイル、新規)
- `pack_builder.py` `convert.py` `convert_image_mode.py` `mvcodec/volume.py` `mvcodec/stream.py` `main.js` `web/app.js` `build_standalone.py` `requirements.txt` `docs/TESTING.md`
- `.github/workflows/real-video-bench.yml` `.github/workflows/release.yml` `CHANGELOG.md`
- `AUDIT_REPORT.md` (最終報告、新規)
- `tests/test_audit_fixes.py` (新規)、`tests/test_custom_commands.py` (1 件追加)

## 次に実行する作業

(最新) 監査は完了し、ブランチ `audit/full-qa` にコミットして PR にした。残り: PR #145 とコミットしていない画面の作り直しとの重なり (convert_image_mode.py の書き込みの行・CHANGELOG) を、後から入れる方で直す

(以前の予定)

1. 変更前の基準として `pytest -q` を全部流す (結果をここに書く)
2. 文書 (PROJECT_STATUS・TESTING・KNOWN_ISSUES・DECISIONS) とテストの一覧を読む
3. ruff / bandit / vulture / pip-audit / npm audit / node --check を流して、Phase 1 で読む順番を決める
