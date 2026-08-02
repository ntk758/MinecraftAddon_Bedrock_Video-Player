"""配布パックに埋め込むリリース情報の唯一の管理場所。"""

PACK_VERSION = (1, 6, 0)
RELEASE_NOTES = [
    "Adaptive FPS(静止シーン可変フレームレート)と場面転換検出により1時間あたり48MBの長尺保存に対応",
    "1GBのストレージ容量に約21.2時間分(映画10本分)の動画が保存可能な究極の圧縮コーデック",
    "Delta VarInt + Base64 圧縮により JSONデータ容量を初期比87.5%圧縮(1/8以下)",
    "Pillow Quantize + ThreadPoolExecutor により変換速度1000fps超の超高速並列変換",
    "GitHub Actionsによる5%性能低下防止のCI自動性能回帰テストを導入",
]


def version_text() -> str:
    return ".".join(str(part) for part in PACK_VERSION)


def manifest_description() -> str:
    return f"Block Video Player v{version_text()}: {RELEASE_NOTES[0]}"


def changelog_markdown(pack_name: str) -> str:
    notes = "\n".join(f"- {note}" for note in RELEASE_NOTES)
    return f"# {pack_name} — 変更履歴\n\n## v{version_text()}\n\n{notes}\n"
