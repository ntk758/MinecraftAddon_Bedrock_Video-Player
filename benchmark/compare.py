"""convert.py の出力 (FRAME_DATA v4) と元動画を同じ解像度・fps のフレーム列にそろえる。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from convert import ffmpeg_frame_generator  # noqa: E402
from mvcodec.decode import iter_decoded_frames, load_frame_data  # noqa: E402


def load_output(js_path):
    """出力ファイルを読み込み (data, resolution, fps) を返す。"""
    data = load_frame_data(js_path)
    return data, (data["width"], data["height"]), data.get("fps", 20.0)


def decode_frames_to_rgb(data):
    """ゲーム内と同じ規則で盤面を再構築し、各フレームの表示色 (H, W, 3) を返す。"""
    return [rgb for _index, rgb in iter_decoded_frames(data)]


def extract_video_frames(video_path, fps, resolution, ffmpeg="ffmpeg"):
    """変換時と同じ FFmpeg フィルタ (縮小+レターボックス) で元動画のフレームを取り出す。"""
    width, height = resolution
    return list(ffmpeg_frame_generator(video_path, width, height, fps, ffmpeg=ffmpeg))
