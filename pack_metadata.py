"""配布パックに埋め込むリリース情報の唯一の管理場所。"""

PACK_VERSION = (2, 6, 0)
RELEASE_NOTES = [
    "GPU超高速変換の抜本的最適化: Ordered(Bayer)ディザリング時のGPUテンソル並列計算を実装し、高画質でも最高速の変換が可能に",
    "GPU(VRAM)メモリパンク防止(OOM対策): ミニバッチ処理とキャッシュ解放を導入し、数時間規模の動画でもGPU変換が安定稼働",
    "GPU非対応ディザリング選択時のCPUフォールバック警告表示を追加し、意図しない速度低下をユーザーへ通知",
    "音声再生の完全対応: パック構造を Behavior Pack (BP) と Resource Pack (RP) に分離し、.mcaddon 形式で出力するようアーキテクチャを刷新",
    "デコードストリーム途切れによるブロック座標崩壊バグを完全解消・修正",
]


def version_text() -> str:
    return ".".join(str(part) for part in PACK_VERSION)


def manifest_description() -> str:
    return f"Block Video Player v{version_text()}: {RELEASE_NOTES[0]}"


def changelog_markdown(pack_name: str) -> str:
    notes = "\n".join(f"- {note}" for note in RELEASE_NOTES)
    return f"# {pack_name} — 変更履歴\n\n## v{version_text()}\n\n{notes}\n"
