"""配布パックに埋め込むリリース情報の唯一の管理場所。"""

PACK_VERSION = (1, 7, 0)
RELEASE_NOTES = [
    "1パックに複数動画を搭載可能なマルチ動画アーキテクチャ(list/play/stopコマンド対応)",
    "39色拡張パレット(Concrete 16色+Terracotta 17色+自発光ブロック6種)による色再現性の最大化",
    "Floyd-Steinberg / Ordered (Bayer) ディザリング選択によるグラデーション品質向上",
    "GUIで複数動画の追加・削除・一括ビルドに対応(Treeview管理UI)",
    "Adaptive FPS + Delta VarInt Base64圧縮で1時間48MB / 1GBに21時間保存可能",
]


def version_text() -> str:
    return ".".join(str(part) for part in PACK_VERSION)


def manifest_description() -> str:
    return f"Block Video Player v{version_text()}: {RELEASE_NOTES[0]}"


def changelog_markdown(pack_name: str) -> str:
    notes = "\n".join(f"- {note}" for note in RELEASE_NOTES)
    return f"# {pack_name} — 変更履歴\n\n## v{version_text()}\n\n{notes}\n"
