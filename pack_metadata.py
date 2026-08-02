"""配布パックに埋め込むリリース情報の唯一の管理場所。"""

PACK_VERSION = (2, 4, 0)
RELEASE_NOTES = [
    "高画質(128x128)での重さ解消用1tickブロック設置数バッチ上限(800)と、player.playSound()による音楽再生の確実化を実装",
    "GUIクラス(_build_ui)の構造修復と安定性の強化",
    "キーフレーム(I/P Frame, GOP=30)方式を導入。画面乱れのない超高速14msシーク復元に対応",
    "Python・Node未インストール環境用の単体実行EXEビルド(build_standalone.py)とポータブルFFmpeg自動検出を構築",
    "GPU(PyTorch/CUDA テンソル演算 & FFmpeg HWAccel)によるフレーム減色・抽出の超高速化と自動フォールバックを搭載",
]


def version_text() -> str:
    return ".".join(str(part) for part in PACK_VERSION)


def manifest_description() -> str:
    return f"Block Video Player v{version_text()}: {RELEASE_NOTES[0]}"


def changelog_markdown(pack_name: str) -> str:
    notes = "\n".join(f"- {note}" for note in RELEASE_NOTES)
    return f"# {pack_name} — 変更履歴\n\n## v{version_text()}\n\n{notes}\n"
