"""PyInstaller を使って Python・Node未インストール環境用の単体実行 EXE (dist/BlockVideoPlayer.exe) を作成するスクリプト。"""

import os
import sys
import subprocess
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent

def build_standalone_exe():
    print("==================================================")
    print("  Block Video Player スタンドアロン EXE ビルド")
    print("==================================================")

    # PyInstaller チェック
    try:
        import PyInstaller
        print("[1/3] PyInstaller 検出 OK")
    except ImportError:
        print("[1/3] PyInstaller をインストールしています...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])

    main_gui = APP_DIR / "video_player_gui.py"
    if not main_gui.is_file():
        raise FileNotFoundError(f"{main_gui} が見つかりません。")

    # PyInstaller コマンド構築
    # アプリ内で参照する必須スクリプト・データファイルを同梱
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--onedir",
        "--windowed",
        "--name", "BlockVideoPlayer",
        "--add-data", f"{APP_DIR / 'convert.py'}{os.pathsep}.",
        "--add-data", f"{APP_DIR / 'main.js'}{os.pathsep}.",
        "--add-data", f"{APP_DIR / 'manifest.json'}{os.pathsep}.",
        "--add-data", f"{APP_DIR / 'pack_metadata.py'}{os.pathsep}.",
        str(main_gui)
    ]

    print("[2/3] 単体 EXE のビルドを開始します...")
    subprocess.check_call(cmd)

    dist_dir = APP_DIR / "dist" / "BlockVideoPlayer"
    print("--------------------------------------------------")
    print(f"[3/3] ビルド完了!")
    print(f"  出力ディレクトリ: {dist_dir}")
    print(f"  実行ファイル: {dist_dir / 'BlockVideoPlayer.exe'}")
    print("  ※ ポータブル用 ffmpeg.exe を上記ディレクトリに配置すると、PATH未設定の環境でも動作します。")
    print("==================================================")

if __name__ == "__main__":
    build_standalone_exe()
