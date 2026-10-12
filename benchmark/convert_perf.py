"""変換速度・メモリのベンチマーク (条件をそろえて計り、実行環境と一緒に記録する)。

入力は毎回同じものを ffmpeg で作る: testsrc2 1280×720 30 fps 30 秒 (動きと細かい模様のある合成映像)。
これを下の設定でそれぞれ convert.py で変換し、次を計る:
  - 時間: 変換全体 (動画の読み込み・減色・符号化・書き出し) の秒数と、1 秒あたりの出力フレーム数
  - 最大メモリ: 変換プロセスの最大の物理メモリ (Windows はピークのワーキングセット、Linux / macOS は ru_maxrss)
  - 最大 VRAM: Windows のパフォーマンスカウンター「GPU Process Memory」の専用メモリ (GPU を使ったときだけ。
    メーカーを問わず計れる)。それ以外の OS では計らない
  - 出力: .js の大きさ
実行環境 (OS・CPU・コア数・RAM・GPU・Python / PyTorch / ffmpeg の版) も記録する。

使い方: python benchmark/convert_perf.py --out bench_out [--seconds 30] [--skip-gpu]
結果は <out>/results_convert.json と .md。ゲーム内の再生性能は別 (アドオンの /scriptevent <接頭辞>:stats、
docs/PERFORMANCE.md を参照)。
"""
import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# (名前, convert.py の引数)。fps はすべて 10 (30 秒 → 300 フレーム)
CONFIGS = [
    ("screen 128x72 blue_noise CPU", ["--width", "128", "--height", "72", "--dither-method", "blue_noise", "--device", "cpu"]),
    ("screen 128x72 blue_noise GPU", ["--width", "128", "--height", "72", "--dither-method", "blue_noise", "--device", "auto"]),
    ("screen 256x144 blue_noise GPU", ["--width", "256", "--height", "144", "--dither-method", "blue_noise", "--device", "auto"]),
    ("screen 128x72 none CPU", ["--width", "128", "--height", "72", "--dither-method", "none", "--device", "cpu"]),
    ("relief 64x48x16 none CPU", ["--width", "64", "--height", "48", "--mode", "relief", "--depth", "16",
                                  "--dither-method", "none", "--device", "cpu"]),
]
FPS = 10


def _windows_cpu_name():
    try:
        import winreg

        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
        return winreg.QueryValueEx(key, "ProcessorNameString")[0].strip()
    except OSError:
        return None


def _total_ram_gb():
    try:
        import psutil

        return round(psutil.virtual_memory().total / 2 ** 30, 1)
    except ImportError:
        if hasattr(os, "sysconf") and "SC_PHYS_PAGES" in os.sysconf_names:
            return round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2 ** 30, 1)
    return None


def _gpu_name():
    try:
        from mvcodec.device import detect_gpu

        gpu = detect_gpu("auto")
        return gpu.describe() if gpu else None
    except Exception as error:  # GPU が無い・ライブラリが無いときは記録しない
        return f"(none: {type(error).__name__})"


def _version(command):
    try:
        out = subprocess.run(command, capture_output=True, text=True, timeout=30)
        return (out.stdout or out.stderr).splitlines()[0].strip()
    except (OSError, subprocess.SubprocessError, IndexError):
        return None


def environment():
    try:
        import torch

        torch_version = torch.__version__
    except ImportError:
        torch_version = None
    cpu = _windows_cpu_name() if sys.platform == "win32" else None
    if sys.platform == "win32" and (not cpu or "0000" in cpu):
        cpu = _version(["powershell", "-NoProfile", "-Command", "(Get-CimInstance Win32_Processor).Name"]) or cpu
    if not cpu and Path("/proc/cpuinfo").exists():
        match = re.search(r"model name\s*:\s*(.+)", Path("/proc/cpuinfo").read_text(errors="replace"))
        cpu = match.group(1).strip() if match else None
    return {
        "os": platform.platform(),
        "cpu": cpu or platform.processor(),
        "cpu_threads": os.cpu_count(),
        "ram_gb": _total_ram_gb(),
        "gpu": _gpu_name(),
        "python": platform.python_version(),
        "torch": torch_version,
        "ffmpeg": _version(["ffmpeg", "-version"]),
    }


class PeakSampler:
    """変換プロセスとその子プロセスの最大メモリ (と Windows では専用 VRAM) を、終わるまで定期的に読む。

    Windows の venv の python.exe は本物の Python を子プロセスとして起動するので、子プロセスも合わせて数える。
    """

    def __init__(self, pid, vram):
        self.pid = pid
        self.pids = {pid}
        self.peak_ram = 0
        self.peak_vram = 0
        self._stop = threading.Event()
        self._threads = [threading.Thread(target=self._ram, daemon=True)]
        if vram and sys.platform == "win32":
            self._threads.append(threading.Thread(target=self._vram, daemon=True))
        for thread in self._threads:
            thread.start()

    def _ram(self):
        try:
            import psutil

            root = psutil.Process(self.pid)
        except Exception:
            return
        while not self._stop.is_set():
            try:
                processes = [root, *root.children(recursive=True)]
            except Exception:
                return
            self.pids = {process.pid for process in processes}
            total = 0
            for process in processes:
                try:
                    info = process.memory_info()
                    # Windows はピークのワーキングセット (プロセスごとの最大)、他は今の物理メモリ
                    total += max(getattr(info, "peak_wset", 0), info.rss)
                except Exception:
                    pass
            self.peak_ram = max(self.peak_ram, total)
            time.sleep(0.2)

    def _vram(self):
        # 約 0.5 秒ごとに、全プロセスの GPU 専用メモリを「インスタンス名 値」で出力し続ける
        script = ("while ($true) { (Get-Counter '\\GPU Process Memory(*)\\Dedicated Usage' -ErrorAction SilentlyContinue)"
                  ".CounterSamples | ForEach-Object { $_.InstanceName + ' ' + $_.CookedValue }; Write-Output '--'; "
                  "Start-Sleep -Milliseconds 500 }")
        proc = subprocess.Popen(["powershell", "-NoProfile", "-Command", script], stdout=subprocess.PIPE, text=True,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        total = 0
        try:
            while not self._stop.is_set():
                line = proc.stdout.readline()
                if not line:
                    break
                line = line.strip()
                if line == "--":
                    self.peak_vram = max(self.peak_vram, total)
                    total = 0
                    continue
                match = re.match(r"pid_(\d+)_\S* ([\d.]+)", line)
                if match and int(match.group(1)) in self.pids:
                    total += int(float(match.group(2)))
        finally:
            proc.kill()

    def stop(self):
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout=5)


def make_input(path, seconds):
    if not path.exists():
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                        f"testsrc2=size=1280x720:rate=30:duration={seconds}", "-pix_fmt", "yuv420p", str(path)], check=True)


def run_one(name, extra, video, out_dir):
    out = out_dir / "convert" / (re.sub(r"[^a-z0-9]+", "_", name.lower()) + ".js")
    out.parent.mkdir(parents=True, exist_ok=True)
    argv = [sys.executable, str(ROOT / "convert.py"), "--input-video", str(video), "--output", str(out),
            "--fps", str(FPS), "--palette", "expanded", "--lang", "en", *extra]
    start = time.perf_counter()
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace", cwd=ROOT)
    sampler = PeakSampler(proc.pid, vram="GPU" in name)
    log = proc.stdout.read()
    code = proc.wait()
    seconds = time.perf_counter() - start
    sampler.stop()
    if code != 0:
        raise RuntimeError(f"{name}: convert.py failed\n{log[-2000:]}")
    if sys.platform != "win32" and sampler.peak_ram == 0:
        import resource

        sampler.peak_ram = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss * (1 if sys.platform == "darwin" else 1024)
    frames = int(re.search(r"TotalFrames: (\d+)", log).group(1))
    device = re.search(r"(?:減色デバイス|Quantization device|device)[^:]*: ([^\n]+)", log)
    return {
        "config": name,
        "device_used": device.group(1).strip() if device else None,
        "frames": frames,
        "seconds": round(seconds, 1),
        "frames_per_second": round(frames / seconds, 1),
        "peak_ram_mb": round(sampler.peak_ram / 2 ** 20) if sampler.peak_ram else None,
        "peak_vram_mb": round(sampler.peak_vram / 2 ** 20) if sampler.peak_vram else None,
        "output_kb": round(out.stat().st_size / 1024, 1),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default="bench_out")
    parser.add_argument("--seconds", type=int, default=30, help="入力の長さ (秒)")
    parser.add_argument("--skip-gpu", action="store_true", help="GPU の設定を飛ばす")
    args = parser.parse_args(argv)
    if shutil.which("ffmpeg") is None:
        parser.error("ffmpeg が必要です")
    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    video = out_dir / f"testsrc2_1280x720_{args.seconds}s.mp4"
    make_input(video, args.seconds)
    env = environment()
    print(json.dumps(env, ensure_ascii=False), flush=True)
    rows = []
    for name, extra in CONFIGS:
        if args.skip_gpu and "GPU" in name:
            continue
        row = run_one(name, extra, video, out_dir)
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    result = {"environment": env, "input": f"ffmpeg testsrc2 1280x720 30fps {args.seconds}s, converted at {FPS} fps",
              "results": rows}
    (out_dir / "results_convert.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    keys = list(rows[0])
    lines = [f"- {k}: {v}" for k, v in env.items()] + ["", f"Input: {result['input']}", "",
                                                        "| " + " | ".join(keys) + " |", "|" + "---|" * len(keys)]
    lines += ["| " + " | ".join(str(r[k]) for k in keys) + " |" for r in rows]
    (out_dir / "results_convert.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
