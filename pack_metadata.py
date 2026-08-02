"""配布パックに埋め込むリリース情報の唯一の管理場所。"""

PACK_VERSION = (2, 7, 0)
RELEASE_NOTES = [
    "Phase 1: Zero-copy FFmpeg パイプラインを導入。数万枚の画像ファイル書き出しを廃止し、メモリ上での超高速ストリーミング変換に対応",
    "Phase 1: UTF-16生バイナリエンコーディングを導入。Base64と比較してアドオン容量を大幅に削減し、Script APIのデコード速度を向上",
    "FFmpegハードウェアアクセラレーション時にYUV形式が強制され色が緑色等に破損する問題を修正",
    "Script API での音声再生処理において、Bedrock 1.21 以降の厳格な引数仕様 (location必須化) に対応し、音が鳴らない問題を修正",
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
