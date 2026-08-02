"""配布パックに埋め込むリリース情報の唯一の管理場所。"""

PACK_VERSION = (3, 0, 0)
RELEASE_NOTES = [
    "次世代レンダリング: 512x512 (Ultra-HD) に対応。Minecraftの限界を超える超解像度描画を実現",
    "最適化: バジェット(予算)ベースのピクセル更新(RDO/ME)と非同期ジェネレータによるインターレース分割描画を実装し、超巨大スクリーンでもクラッシュを防止",
    "最適化: 時間方向のディザリング (Temporal Dithering) を導入し、低ビットレート・低色数でのフリッカーを低減",
    "安定性向上: Minecraft Bedrock 1.21 以降の BlockVolume API 非互換・仕様変更を完全に回避する堅牢な描画ループへ刷新",
    "UI: 動画出力解像度に ウルトラ (256x256) および 極限 (512x512) プリセットを追加",
    "安定性向上: 動画シーク時に発生する高負荷クラッシュ問題を非同期ジョブで解決し、安定性を劇的に向上",
]


def version_text() -> str:
    return ".".join(str(part) for part in PACK_VERSION)


def manifest_description() -> str:
    return f"Block Video Player v{version_text()}: {RELEASE_NOTES[0]}"


def changelog_markdown(pack_name: str) -> str:
    notes = "\n".join(f"- {note}" for note in RELEASE_NOTES)
    return f"# {pack_name} — 変更履歴\n\n## v{version_text()}\n\n{notes}\n"
