"""配布パックに埋め込むリリース情報の唯一の管理場所。"""

PACK_VERSION = (1, 3, 4)
RELEASE_NOTES = [
    "TICKING_AREA_NAME定数の定義順序を修正(使用箇所より後ろで定義されていた行儀の悪さを解消)",
    "startPlayback内でintervalIdがrunTimeout→runIntervalの識別子に切り替わる挙動にコメントを追加",
    "READMEに、GUIでコマンド接頭辞を変更した場合の実行コマンドの違いを明記",
    "開始時にtickingareaを再確保してロード待ちを入れ、未ロードチャンクの描画エラーを軽減",
    "ゲーム内の案内文とログ接頭辞を、GUIで指定したコマンド名前空間へ自動追従",
    "無色テラコッタを統合版の正しいID minecraft:hardened_clay へ修正し、旧データにも互換対応",
    "予約済みのminecraft名前空間をGUIで指定できないように修正",
    "高解像度プリセット、33色拡張パレット、Floyd-Steinbergディザリングで画質を向上",
    "動画から切り出したフレームをpack_icon.pngとしてパックのサムネイルに設定",
    "GUIでパック表示名、出力ファイル名、実行コマンド接頭辞、再生間隔を設定可能に変更",
    "パック内にCHANGELOG.mdを同梱し、バージョンと変更内容を確認可能に変更",
]


def version_text() -> str:
    return ".".join(str(part) for part in PACK_VERSION)


def manifest_description() -> str:
    return f"Block Video Player v{version_text()}: {RELEASE_NOTES[0]}"


def changelog_markdown(pack_name: str) -> str:
    notes = "\n".join(f"- {note}" for note in RELEASE_NOTES)
    return f"# {pack_name} — 変更履歴\n\n## v{version_text()}\n\n{notes}\n"
