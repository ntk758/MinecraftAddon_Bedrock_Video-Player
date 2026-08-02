"""配布パックに埋め込むリリース情報の唯一の管理場所。"""

PACK_VERSION = (2, 6, 4)
RELEASE_NOTES = [
    "Script API での音声再生処理において、Bedrock 1.21 以降の厳格な引数仕様 (location必須化) に対応し、音が鳴らない問題を修正",
    ".mcaddon 生成時に Script API の依存関係 (@minecraft/server 等) が消えてしまいマイクラ内でエラーになるバグを修正",
    "GUI(video_player_gui.py)起動時に AttributeError: 'quality_var' などが発生して開かなくなる不具合を修正",
    "READMEおよびGUIのテキストを整理。事実ベースの説明へ改修し、ディザリング時のGPU対応状況を明記",
    "GPU超高速変換の抜本的最適化: Ordered(Bayer)ディザリング時のGPUテンソル並列計算を実装し、高画質でも最高速の変換が可能に",
    "GPU(VRAM)メモリパンク防止(OOM対策): ミニバッチ処理とキャッシュ解放を導入し、数時間規模の動画でもGPU変換が安定稼働",
]


def version_text() -> str:
    return ".".join(str(part) for part in PACK_VERSION)


def manifest_description() -> str:
    return f"Block Video Player v{version_text()}: {RELEASE_NOTES[0]}"


def changelog_markdown(pack_name: str) -> str:
    notes = "\n".join(f"- {note}" for note in RELEASE_NOTES)
    return f"# {pack_name} — 変更履歴\n\n## v{version_text()}\n\n{notes}\n"
