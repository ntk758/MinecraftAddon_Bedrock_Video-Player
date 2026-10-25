"""動画から人物を切り抜き、人物の範囲の動画と、人物を消した背景の動画を作る (Phase 12.3)。

初回に AI の切り抜きモデルをダウンロードする: 実写向き (既定、torchvision の DeepLabV3 MobileNetV3-Large、BSD-3、
約 42 MB) か、`--model anime` でアニメ・イラスト向き (skytnt/anime-seg、Apache-2.0、約 176 MB、onnxruntime が必要)。
作った動画 (元の縦横比、長い辺 640 まで) で、人物をふくらませた立体を、背景の映像の前に立つだまし絵にできる:

    python scripts/make_person_videos.py in.mp4 mask.mp4 background.mp4 --fps 10 [--model anime]
    python convert.py --input-video background.mp4 --output bg.js --mode screen --fit cover \
        --width <後ろの壁の幅> --height <後ろの壁の高さ> --fps 10
    python convert.py --input-video in.mp4 --mask-video mask.mp4 --mode inflate --depth 16 --view anamorphic \
        --stage-background bg --output person.js --width 160 --height 90 --fps 10

Windows 版の GUI の表示モード「人物を切り抜いて立体に (AI)」「アニメの人物を切り抜いて立体に (AI)」が同じことをする。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mvcodec.person import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
