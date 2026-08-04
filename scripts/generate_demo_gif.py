import sys
import os
import subprocess
import json
import re
from pathlib import Path
import numpy as np
from PIL import Image

# プロジェクトルートをパスに追加
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mvcodec.color import PALETTES

def generate_demo():
    input_video = "C:/Users/nakat/Downloads/test.mp4"
    if not os.path.exists(input_video):
        input_video = "C:/Users/nakat/Downloads/videoplayback (1).mp4"
    if not os.path.exists(input_video):
        print(f"Test video not found: {input_video}")
        return

    root_dir = Path(__file__).resolve().parent.parent
    output_js = root_dir / "demo_output.js"
    output_gif = root_dir / "demo.gif"
    
    width = 128
    height = 128
    fps = 10.0
    duration = 5.0 # デモ用なので5秒
    
    print("Running convert.py...")
    # sys.executable を使って convert.py を呼び出す
    cmd = [
        sys.executable,
        str(root_dir / "convert.py"),
        "--input-video", input_video,
        "--output", str(output_js),
        "--width", str(width),
        "--height", str(height),
        "--palette", "auto",
        "--dither-method", "floyd", # CPUでも動くようにfloydを指定
        "--duration", str(duration),
        "--fps", str(fps)
    ]
    subprocess.run(cmd, check=True)
    
    print("Parsing generated JS...")
    if not output_js.exists():
        print("Error: demo_output.js not found.")
        return
        
    js_text = output_js.read_text(encoding="utf-8")
    
    # export const FRAME_DATA = { ... }; から JSON 部分を抽出
    match = re.search(r"export const FRAME_DATA = (\{.*\});", js_text, re.DOTALL)
    if not match:
        print("Error: FRAME_DATA not found in JS.")
        return
        
    data = json.loads(match.group(1))
    
    # 実際はBase64エンコードされたDelta VarInt + RLE が入っているので、デコードが必要。
    # しかし、デコードをPythonで書くのは面倒なので、元の画像を直接生成するのではなく、
    # convert.py 内に "デモ出力用の隠しフラグ" を持たせるか、あるいは...
    pass

if __name__ == "__main__":
    # convert.py の内部で RLE や Base64 化される前の生のフレーム配列が欲しい。
    pass
