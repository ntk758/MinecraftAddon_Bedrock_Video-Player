"""GitHub のリリースの説明文を作る: CHANGELOG.md のその版の部分 + ダウンロードの案内 + 確かめたこと。
リリースのワークフロー (.github/workflows/release.yml) が使う。

    python scripts/release_notes.py v6.31.0 --checks checks.md > notes.md
"""
import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def changelog_section(version, changelog=None):
    """CHANGELOG.md の「## vX.Y.Z」から次の「## 」の前まで (見出しは除く)。無ければ ValueError。"""
    text = (changelog or (ROOT / "CHANGELOG.md")).read_text(encoding="utf-8")
    match = re.search(rf"^## {re.escape(version)}\s*\n(.*?)(?=^## |\Z)", text, re.S | re.M)
    if not match:
        raise ValueError(f"CHANGELOG.md に {version} の見出しがありません")
    return match.group(1).strip()


def downloads(version):
    return "\n".join([
        "## ダウンロード / Downloads",
        f"- `BlockVideoPlayer-{version}-windows-x64.zip`: Windows 用の変換ツール (Python 不要、FFmpeg は別に必要)",
        f"- `BlockVideoPlayer-sample-{version}.mcaddon` / `BlockVideoPlayer-sample-3d-{version}.mcaddon`: すぐ試せるサンプル",
        f"- `BlockVideoPlayer-{version}-showcase.mcaddon`: ショーケース",
        "- `SHA256SUMS.txt`: チェックサム",
    ])


def notes(version, checks="", changelog=None):
    parts = [changelog_section(version, changelog), downloads(version)]
    if checks.strip():
        parts.append("## 確認したこと\n" + checks.strip())
    return "\n\n".join(parts) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("version", help="vX.Y.Z")
    parser.add_argument("--checks", default=None, help="「確認したこと」に入れる Markdown のファイル")
    args = parser.parse_args(argv)
    checks = Path(args.checks).read_text(encoding="utf-8") if args.checks else ""
    sys.stdout.write(notes(args.version, checks))
    return 0


if __name__ == "__main__":
    sys.exit(main())
