"""ブラウザ版の変換ツール (web/) を、そのまま公開できるフォルダに組み立てる。

web/ の画面のファイルに加えて、ブラウザの中の Python (Pyodide) が読み込む変換・組み立てのソース (convert.py、mvcodec、
pack_builder.py など)、ゲーム内の翻訳 (locales)、アドオンの中身 (main.js、codec.js、manifest.json) を app/ に置き、
その一覧を files.json に書く。GitHub Pages のワークフロー (.github/workflows/pages.yml) と、手元での確認に使う:

    python scripts/build_web.py --out web_dist
    python -m http.server --directory web_dist 8000   # http://localhost:8000/
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# ブラウザ版が使う、リポジトリのファイル (ルートからの相対パス)
APP_FILES = [
    "convert.py", "gui_i18n.py", "pack_builder.py", "pack_metadata.py", "main.js", "codec.js", "manifest.json",
    *[str(p.relative_to(ROOT)).replace("\\", "/") for p in sorted((ROOT / "mvcodec").glob("*.py"))],
    *[str(p.relative_to(ROOT)).replace("\\", "/") for p in sorted((ROOT / "locales").glob("*.json"))],
]


def build(out):
    out = Path(out)
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(ROOT / "web", out)
    for path in APP_FILES:
        target = out / "app" / path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / path, target)
    (out / "files.json").write_text(json.dumps(APP_FILES, indent=1), encoding="utf-8")
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default="web_dist")
    args = parser.parse_args(argv)
    out = build(args.out)
    print(f"{len(APP_FILES)} files -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
