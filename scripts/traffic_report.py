"""GitHub の閲覧数・参照元・クローン数と、リリースのダウンロード数を表示する (gh CLI でログイン済みであること)。

GitHub の Traffic は直近 14 日分しか残らないので、2 週間に 1 回くらい実行して記録しておく:
    python scripts/traffic_report.py >> traffic_log.md
(リポジトリへの push 権限が必要。結果は個人の記録用で、リポジトリには入れない)
"""
import json
import subprocess
import sys
from datetime import date

REPO = "ntk758/MinecraftAddon_Bedrock_Video-Player"


def gh(path):
    out = subprocess.run(["gh", "api", f"repos/{REPO}/{path}"], capture_output=True, text=True, encoding="utf-8")
    if out.returncode != 0:
        raise SystemExit(f"gh api {path} に失敗しました: {out.stderr.strip()}")
    return json.loads(out.stdout)


def main():
    views = gh("traffic/views")
    clones = gh("traffic/clones")
    referrers = gh("traffic/popular/referrers")
    paths = gh("traffic/popular/paths")
    releases = gh("releases?per_page=10")
    lines = [f"## {date.today().isoformat()} (直近 14 日)", "",
             f"- 閲覧: {views['count']} 回 / {views['uniques']} 人",
             f"- クローン: {clones['count']} 回 / {clones['uniques']} 人 (CI や自動のものを含む)",
             "- 参照元: " + (", ".join(f"{r['referrer']} {r['count']} ({r['uniques']} 人)" for r in referrers) or "なし"),
             "- よく見られたページ: " + (", ".join(f"{p['path']} {p['count']}" for p in paths[:5]) or "なし"),
             "", "| 版 | ダウンロード数 (ファイルごと) |", "|---|---|"]
    for release in releases:
        counts = ", ".join(f"{a['name']} {a['download_count']}" for a in release["assets"])
        lines.append(f"| {release['tag_name']} | {counts} |")
    print("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
