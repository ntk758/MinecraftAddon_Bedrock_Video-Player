import re
import json
import cv2
import numpy as np

BASIC_COLOR_MAP = {
    0: (0, 0, 0),       1: (255, 255, 255), 2: (255, 0, 0),     3: (0, 255, 0),
    4: (0, 0, 255),     5: (255, 255, 0),   6: (0, 255, 255),   7: (255, 0, 255),
}

def parse_js_output(js_path):
    with open(js_path, 'r', encoding='utf-8') as f:
        content = f.read()

    fmt_match = re.search(r'(?:const|let|var)\s+FORMAT\s*=\s*(["\'])(.*?)\1', content)
    fmt = fmt_match.group(2) if fmt_match else "unknown"

    res_match = re.search(r'(?:const|let|var)\s+RESOLUTION\s*=\s*\[(\d+),\s*(\d+)\]', content)
    resolution = (int(res_match.group(1)), int(res_match.group(2))) if res_match else (64, 36)

    frames_match = re.search(r'(?:const|let|var)\s+(?:FRAMES|LEVEL_BLOCKS|level_blocks)\s*=\s*(\[.*\])\s*;', content, re.DOTALL)
    frames = []
    if frames_match:
        try:
            frames = json.loads(frames_match.group(1))
        except json.JSONDecodeError:
            print("Warning: Could not parse frames as strict JSON.")
    return fmt, resolution, frames

def decode_frames_to_rgb(frames, resolution, fmt):
    width, height = resolution
    decoded = []
    try:
        from mvcodec.color import ALL_BLOCKS
        color_map = {i: block.rgb for i, block in enumerate(ALL_BLOCKS)}
    except ImportError:
        color_map = BASIC_COLOR_MAP

    for frame_data in frames:
        img = np.zeros((height, width, 3), dtype=np.uint8)
        if isinstance(frame_data, list):
            idx = 0
            for y in range(height):
                for x in range(width):
                    if idx < len(frame_data):
                        val = frame_data[idx]
                        if isinstance(val, int):
                            img[y, x] = color_map.get(val, (0,0,0))
                        idx += 1
        decoded.append(img)
    return decoded

def extract_video_frames(video_path, fps, resolution):
    cap = cv2.VideoCapture(video_path)
    frames = []
    orig_fps = cap.get(cv2.CAP_PROP_FPS)
    if orig_fps == 0: orig_fps = fps
    frame_interval = max(1, int(round(orig_fps / fps)))
    width, height = resolution
    count = 0
    while True:
        ret, frame = cap.read()
        if not ret: break
        if count % frame_interval == 0:
            frame = cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            frames.append(frame)
        count += 1
    cap.release()
    return frames
