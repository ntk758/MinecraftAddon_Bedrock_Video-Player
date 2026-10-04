import subprocess
import sys
import time


def measure_conversion_time(video_path, output_path, fps, convert_script="convert.py", extra_args=()):
    start_time = time.time()
    cmd = [sys.executable, convert_script, "--input-video", video_path, "--output", output_path,
           "--fps", str(fps), *extra_args]
    print(f"Running conversion: {' '.join(cmd)}")
    subprocess.run(cmd, check=True)
    return time.time() - start_time
