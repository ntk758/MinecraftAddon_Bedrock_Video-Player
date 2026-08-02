"""配布パックに埋め込むリリース情報の唯一の管理場所。"""

PACK_VERSION = (1, 8, 0)
RELEASE_NOTES = [
    "全50+色マイクラブロック解析と照明不変発光ブロックパレット(39色/55色)を導入",
    "全5種類(Floyd-Steinberg/Atkinson/Burkes/Sierra/Ordered)のディザリングアルゴリズム選択",
    "1パックに複数動画を搭載可能なマルチ動画インデックス化(list/play/stopコマンド対応)",
    "Adaptive FPS + Delta VarInt Base64圧縮で1時間48MB / 1GBに21時間分(映画10本分)保存可能",
    "GitHub Actionsによる5%性能低下防止のCI自動性能回帰テストを導入",
]


def version_text() -> str:
    return ".".join(str(part) for part in PACK_VERSION)


def manifest_description() -> str:
    return f"Block Video Player v{version_text()}: {RELEASE_NOTES[0]}"


def changelog_markdown(pack_name: str) -> str:
    notes = "\n".join(f"- {note}" for note in RELEASE_NOTES)
    return f"# {pack_name} — 変更履歴\n\n## v{version_text()}\n\n{notes}\n"
