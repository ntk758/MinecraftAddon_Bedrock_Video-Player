# リリースの手順 / Release checklist

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

GitHub はリリースの各ファイルの SHA-256 を自動で表示する (`gh release view vX.Y.Z --json assets` の `digest`) ので、
`SHA256SUMS.txt` が無い古い版でも、利用者はそれと比べて確かめられます。
