# リリースの手順 / Release checklist

## 自動のリリース (v6.31.0 から)

版を上げる変更 (下の 1) を main に入れて CI が成功したら、版のタグを push するだけで
[.github/workflows/release.yml](../.github/workflows/release.yml) が次を行い、GitHub のリリースを作る。

- **EXE**: GitHub Actions の Windows (Python 3.12) で `build_standalone.py` (DirectML 版の PyTorch と onnxruntime を同梱)。
  EXE の変換モード (`--run-converter`) で画像 1 枚を変換できることを確かめてから zip にする。
- **サンプル**: 前のリリースの `sample`・`sample-3d`・`showcase` を、データ (変換した映像・音声・サムネイル) はそのままで、
  アドオンの部分 (main.js・codec.js・manifest・翻訳・版) だけを今の版にして作り直す (`scripts/repack_addon.py`)。
  パック名が同じなので UUID も同じで、上書きで更新できる。**データの形式を変えた版では使えない** (そのときは手で作る)。
- **CI の確認**: リリースするコミットの CI (`ci.yml`) が成功していなければ止まる (main の CI が終わってからタグを付ける)。
- **モックで再生**: サンプルの全部の動画を最後まで再生し、置いたブロックの数を参照デコーダと比べる (`scripts/play_addon_mock.py`)。
- **SHA256SUMS.txt** と、CHANGELOG のその版の部分から作った説明文 (`scripts/release_notes.py`)。説明文の「実機」は「未確認」と書く。

```bash
git tag v6.31.0 && git push origin v6.31.0
```

タグを push できない環境では、GitHub の Actions → Release → Run workflow で `tag` に版 (例: `v6.31.0`) を入れて起動する
(API なら `workflow_dispatch`)。起動したコミットにそのタグを付けてリリースを作る。`pack_metadata.py` の版と違えば止まる。

実機での確認 (下の 6) と CurseForge の更新 (下の 9) は手で行う。実機の結果は起動するときの `device_check` に入れると
説明文の「確認したこと」に書かれる。

公開した後に説明文を直すときは、CHANGELOG のその版の部分を直して `main` に入れ、`tag` に版を入れ、`notes_only` に
チェックを入れて起動する。EXE とサンプルは作り直さず、説明文だけを作り直す (「確認したこと」は今のものをそのまま使い、
`device_check` を入れたときだけ実機の行を差し替える)。

## 手で行う場合 / Manual steps

1. **版を上げる**: `pack_metadata.py` の `PACK_VERSION`、`manifest.json` の `version` (2 か所)、README の版のバッジ・
   「すぐ試す」のファイル名、`PROJECT_STATUS.md` の最新バージョン、[CHANGELOG.md](../CHANGELOG.md) の「未リリース」を版の見出しに。
2. **自動テスト**: `pytest -q` がすべて成功し、`main` の CI が成功していること。
3. **サンプルを作る**: GUI (またはビルド用のスクリプト) でサンプル (平面) と 3D サンプルの `.mcaddon` を作る。
4. **モックで再生**: `tests/js/drive_main.mjs` でサンプルを最後まで再生し、最終画面が参照デコーダと一致すること。
5. **EXE を作る**: Python 3.12 + DirectML の環境で `python build_standalone.py`、`dist/BlockVideoPlayer` を
   `BlockVideoPlayer-vX.Y.Z-windows-x64.zip` に圧縮。EXE を起動して 1 本変換できること。
6. **実機で確かめる**: [TESTING.md](TESTING.md) の「実機テスト」を少なくとも 1 端末で行い、「リリースごとの記録」に書く。
   確かめられなかったときは「未確認」と書く (確かめたことにしない)。
7. **チェックサム**: 成果物の SHA-256 を `SHA256SUMS.txt` に書き、リリースに一緒に添付する。
   ```bash
   sha256sum BlockVideoPlayer-*.zip BlockVideoPlayer-*.mcaddon > SHA256SUMS.txt
   ```
   (PowerShell: `Get-FileHash BlockVideoPlayer-* -Algorithm SHA256 | ForEach-Object { "$($_.Hash.ToLower())  $(Split-Path $_.Path -Leaf)" } | Set-Content SHA256SUMS.txt`)
8. **タグとリリース**: `git tag vX.Y.Z` → `git push origin vX.Y.Z` →
   `gh release create vX.Y.Z <zip> <sample> <sample-3d> SHA256SUMS.txt --title ... --notes-file ...`。
   リリースノートは CHANGELOG の該当部分 + ダウンロードの案内 + 確認の結果 (TESTING.md の記録) にする。
9. **配布サイト**: CurseForge にショーケース版の `.mcaddon` を更新 (MCPEDL は CurseForge から同期)。
   説明文は [docs/store/curseforge.md](store/curseforge.md) (英語) と [docs/store/crafterscolony_ja.md](store/crafterscolony_ja.md)
   (クラコロ、日本語) を直してから貼り付ける。ファイルの Changelog 欄は
   [docs/store/curseforge_file_changelog.md](store/curseforge_file_changelog.md) を、前に上げた版からの変更に書き換えて貼る。コマンド・必要な Minecraft の版・ショーケースの動画が、上げるファイルと合っていること。

GitHub はリリースの各ファイルの SHA-256 を自動で表示する (`gh release view vX.Y.Z --json assets` の `digest`) ので、
`SHA256SUMS.txt` が無い古い版でも、利用者はそれと比べて確かめられます。
