import time
import subprocess

def measure_conversion_time(video_path, output_path, fps, convert_script='convert.py'):
    start_time = time.time()
    cmd = ["python", convert_script, "--video", video_path, "--output", output_path, "--fps", str(fps)]
    print(f"Running conversion: {' '.join(cmd)}")
    subprocess.run(cmd, check=True)
    encode_time = time.time() - start_time
    return encode_time
