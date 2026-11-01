"""GitHub のリリースの説明文を作る: CHANGELOG.md のその版の部分 + ダウンロードの案内 + 確かめたこと。
リリースのワークフロー (.github/workflows/release.yml) が使う。

    python scripts/release_notes.py v6.31.0 --checks checks.md > notes.md

公開したリリースの説明文を作り直すとき (CHANGELOG を直した後など) は、今の説明文の「確認したこと」をそのまま使い、
実機の行だけ差し替えられる:

    python scripts/release_notes.py v6.32.0 --checks-from-notes old.md [--device-check "..."] > notes.md
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


CHECKS_HEADING = "## 確認したこと"
DEVICE_PREFIX = "- 実機"


def checks_of(notes_text):
    """説明文の「確認したこと」の部分 (見出しは除く)。無ければ空。"""
    match = re.search(rf"^{CHECKS_HEADING}\s*\n(.*?)(?=^## |\Z)", notes_text, re.S | re.M)
    return match.group(1).strip() if match else ""


def device_line(device_check):
    if device_check.strip():
        return f"{DEVICE_PREFIX}: {device_check.strip()}"
    return f"{DEVICE_PREFIX} (Minecraft) では **未確認**。"


def replace_device_line(checks, device_check):
    """「確認したこと」の実機の行を差し替える (無ければ最後に足す)。"""
    lines = [line for line in checks.splitlines() if not line.startswith(DEVICE_PREFIX)]
    return "\n".join(lines + [device_line(device_check)])


def notes(version, checks="", changelog=None):
    parts = [changelog_section(version, changelog), downloads(version)]
    if checks.strip():
        parts.append(f"{CHECKS_HEADING}\n" + checks.strip())
    return "\n\n".join(parts) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("version", help="vX.Y.Z")
    parser.add_argument("--checks", default=None, help="「確認したこと」に入れる Markdown のファイル")
    parser.add_argument("--checks-from-notes", default=None,
                        help="今の説明文のファイル。その「確認したこと」をそのまま使う (--checks の代わり)")
    parser.add_argument("--device-check", default=None,
                        help="--checks-from-notes のとき、実機の行をこれに差し替える (空なら「未確認」)")
    args = parser.parse_args(argv)
    checks = Path(args.checks).read_text(encoding="utf-8") if args.checks else ""
    if args.checks_from_notes:
        checks = checks_of(Path(args.checks_from_notes).read_text(encoding="utf-8"))
        if not checks:
            parser.error(f"{args.checks_from_notes} に「確認したこと」がありません")
        if args.device_check is not None:
            checks = replace_device_line(checks, args.device_check)
    sys.stdout.write(notes(args.version, checks))
    return 0


if __name__ == "__main__":
    sys.exit(main())
