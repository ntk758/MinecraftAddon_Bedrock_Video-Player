"""ブラウザ版の変換ツール (web/) を、そのまま公開できるフォルダに組み立てる。

web/ の画面のファイルに加えて、ブラウザの中の Python (Pyodide) が読み込む変換・組み立てのソース (convert.py、mvcodec、
pack_builder.py など)、ゲーム内の翻訳 (locales)、アドオンの中身 (main.js、codec.js、manifest.json) を app/ に置き、
その一覧を files.json に書く。GitHub Pages のワークフロー (.github/workflows/pages.yml) と、手元での確認に使う:

    python scripts/build_web.py --out web_dist
    python -m http.server --directory web_dist 8000   # http://localhost:8000/

--goatcounter <コード> (既定は環境変数 BVP_GOATCOUNTER_CODE) を付けると、config.js に GoatCounter のコードを書き、
公開したサイトで利用数を数える (web/usage.js)。空なら数えない。
"""
import argparse
import json
import os
import re
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


# GoatCounter のコード (https://<コード>.goatcounter.com のサブドメイン)
GOATCOUNTER_CODE = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")


def config_js(goatcounter=""):
    """ブラウザ版の設定 (web/config.js と同じ形)。"""
    if goatcounter and not GOATCOUNTER_CODE.fullmatch(goatcounter):
        raise ValueError(f"GoatCounter のコードは英小文字・数字・ハイフンだけです: {goatcounter!r}")
    return ("// scripts/build_web.py が書いたブラウザ版の設定 (web/usage.js を参照)\n"
            f"self.BVP_CONFIG = {json.dumps({'goatcounter': goatcounter})};\n")


def build(out, goatcounter=""):
    out = Path(out)
    config = config_js(goatcounter)  # 書き始める前に確かめる
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(ROOT / "web", out)
    (out / "config.js").write_text(config, encoding="utf-8")
    for path in APP_FILES:
        target = out / "app" / path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / path, target)
    (out / "files.json").write_text(json.dumps(APP_FILES, indent=1), encoding="utf-8")
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default="web_dist")
    parser.add_argument("--goatcounter", default=os.environ.get("BVP_GOATCOUNTER_CODE", ""),
                        help="利用数を数える GoatCounter のコード (空なら数えない)")
    args = parser.parse_args(argv)
    code = args.goatcounter.strip().lower()
    try:
        out = build(args.out, code)
    except ValueError as e:
        parser.error(str(e))
    print(f"{len(APP_FILES)} files -> {out} (usage counter: {code or 'off'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
