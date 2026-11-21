"""PyInstaller を使って Python・Node未インストール環境用の単体実行 EXE (dist/BlockVideoPlayer/BlockVideoPlayer.exe) を作成するスクリプト。"""

import importlib.util
import os
import sys
import subprocess
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(APP_DIR))


def bundled_data() -> list[tuple[Path, str]]:
    """EXE に入れるデータファイル (元の場所, EXE の中の置き場所)。

    アドオンを組み立てるときに読むファイルは、組み立てる側 (pack_builder・convert_image_mode) の一覧から作る。
    v6.41.0 では手で書いた一覧に screen_system.js・screen_ui.js を足し忘れ、EXE の GUI で .mcaddon を作れなかった (#195)。"""
    import convert_image_mode
    import pack_builder

    files = [(APP_DIR / name, ".") for name in pack_builder.ADDON_SCRIPTS]
    files.append((pack_builder.MANIFEST, "."))
    # 画像モード (convert_image_mode.py、GUI が import する) のスクリプト。IMAGE_MAIN_SCRIPT の場所に置く
    files.append((convert_image_mode.IMAGE_MAIN_SCRIPT, convert_image_mode.IMAGE_MAIN_SCRIPT.parent.relative_to(APP_DIR).as_posix()))
    # GUI の翻訳ファイル
    files.append((APP_DIR / "locales", "locales"))
    return files

def build_standalone_exe():
    print("==================================================")
    print("  Block & Image Video Player スタンドアロン EXE ビルド")
    print("==================================================")

    # PyInstaller チェック
    if importlib.util.find_spec("PyInstaller") is not None:
        print("[1/3] PyInstaller 検出 OK")
    else:
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
        "--paths", str(APP_DIR),
        # 変換器は EXE 内で `--run-converter` として import 実行されるため、モジュールとして同梱する
        "--hidden-import", "convert",
        "--collect-submodules", "mvcodec",
        "--hidden-import", "convert_image_mode",
    ]
    for source, target in bundled_data():
        if not source.exists():
            raise FileNotFoundError(f"{source} が見つかりません。")
        cmd += ["--add-data", f"{source}{os.pathsep}{target}"]
    cmd.append(str(main_gui))
    # DirectML (AMD / Intel GPU) 版 PyTorch がビルド環境にあれば DLL ごと同梱する
    try:
        import torch_directml  # noqa: F401
        cmd[-1:-1] = ["--collect-all", "torch_directml"]
        print("      torch-directml を同梱します")
    except ImportError:
        pass
    # アニメの人物の切り抜き (mvcodec/person.py の AnimeSegmenter) に使う onnxruntime (DirectML 版なら GPU も使える)
    try:
        import onnxruntime  # noqa: F401
        cmd[-1:-1] = ["--collect-all", "onnxruntime"]
        print("      onnxruntime を同梱します")
    except ImportError:
        pass

    print("[2/3] 単体 EXE のビルドを開始します...")
    subprocess.check_call(cmd)

    dist_dir = APP_DIR / "dist" / "BlockVideoPlayer"
    print("--------------------------------------------------")
    print("[3/3] ビルド完了!")
    print(f"  出力ディレクトリ: {dist_dir}")
    print(f"  実行ファイル: {dist_dir / 'BlockVideoPlayer.exe'}")
    print("  ※ ポータブル用 ffmpeg.exe を上記ディレクトリに配置すると、PATH未設定の環境でも動作します。")
    print("==================================================")

if __name__ == "__main__":
    build_standalone_exe()
