"""配布パックに埋め込むリリース情報の唯一の管理場所。"""

PACK_VERSION = (2, 1, 0)
RELEASE_NOTES = [
    "GPU(PyTorch/CUDA テンソル演算 & FFmpeg HWAccel)によるフレーム減色・抽出の超高速化と自動フォールバックを搭載",
    "全110色以上のウルトラブロックカラーパレット(112ブロック)を統合し、色再現性(ΔE=20.54)と映像クオリティを極限向上",
    "FFmpegによる10秒単位OGG音声抽出とトラック同期再生システムを搭載",
    "コンパス等のリモコンアイテム使用によるゲーム内GUI(ActionFormData)を導入(▶再生/⏸停止/⏭次/⏮前/音量/シーク)",
    "全5種類(Floyd-Steinberg/Atkinson/Burkes/Sierra/Ordered)のディザリングアルゴリズム選択",
]


def version_text() -> str:
    return ".".join(str(part) for part in PACK_VERSION)


def manifest_description() -> str:
    return f"Block Video Player v{version_text()}: {RELEASE_NOTES[0]}"


def changelog_markdown(pack_name: str) -> str:
    notes = "\n".join(f"- {note}" for note in RELEASE_NOTES)
    return f"# {pack_name} — 変更履歴\n\n## v{version_text()}\n\n{notes}\n"
