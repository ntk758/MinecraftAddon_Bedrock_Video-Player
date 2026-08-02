"""配布パックに埋め込むリリース情報の唯一の管理場所。"""

PACK_VERSION = (1, 5, 0)
RELEASE_NOTES = [
    "Delta VarInt + Base64 圧縮により JSONデータ容量をさらに79%削減(初期比87.5%圧縮)",
    "Pillow Quantize + ThreadPoolExecutor により変換速度を約23倍(1000fps超)に爆速化",
    "main.jsにBase64/VarIntデコーダー、Permutation一括キャッシュ、座標オブジェクト再利用を導入",
    "再生タイマーの安全管理化(timeoutId/intervalId独立化)によるゴースト再生バグ防止",
    "GitHub Actionsによる5%性能低下防止のCI自動性能回帰テストを導入",
]


def version_text() -> str:
    return ".".join(str(part) for part in PACK_VERSION)


def manifest_description() -> str:
    return f"Block Video Player v{version_text()}: {RELEASE_NOTES[0]}"


def changelog_markdown(pack_name: str) -> str:
    notes = "\n".join(f"- {note}" for note in RELEASE_NOTES)
    return f"# {pack_name} — 変更履歴\n\n## v{version_text()}\n\n{notes}\n"
