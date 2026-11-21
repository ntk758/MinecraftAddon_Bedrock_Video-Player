"""ブラウザ版が使う Pyodide (ブラウザの中の Python) と、numpy・Pillow を、公開するサイトに置く。

以前は worker が jsDelivr (CDN) から読み込んでいたので、「動画は端末から出ない」ことが CDN の中身に頼っていた (#175)。
ここで決まった版のファイルを取ってきて SHA-256 を確かめ、サイト (scripts/build_web.py の出力) の pyodide/ に置く。
GitHub Pages のワークフロー (.github/workflows/pages.yml) が使う:

    python scripts/build_web.py --out web_dist
    python scripts/fetch_pyodide.py --out web_dist/pyodide [--cache <フォルダ>]

版を上げるときは PYODIDE_VERSION・CORE_FILES・PACKAGES を一緒に直す (web/worker.js の PYODIDE_VERSION も)。
"""
import argparse
import hashlib
import json
import shutil
import sys
import urllib.request
from pathlib import Path

PYODIDE_VERSION = "0.26.4"
BASE_URL = f"https://cdn.jsdelivr.net/pyodide/v{PYODIDE_VERSION}/full/"
# Pyodide の本体 (名前 → SHA-256)。pyodide-lock.json の中に、パッケージの SHA-256 がある
CORE_FILES = {
    "pyodide.js": "c0069107621d5b942a659e737a12e774cc0451feaa2256f475d72e071d844ec7",
    "pyodide.asm.js": "919560652ed3dad3707cb3a394785da1e046fb13dc0defa162058ff230cb7eed",
    "pyodide.asm.wasm": "b7e66a19427a55010ac3367c1b6c64b893f9826f783412945fdf0c3337f3bc94",
    "python_stdlib.zip": "72894522b791858b9d613ac786b951d8b5094035dcf376313ea24a466810f336",
    "pyodide-lock.json": "cd50b49de944c579045e122fe8628b31f9ce446379f032f36c05e273d38766e0",
}
# worker.js が loadPackage で読むパッケージ (依存も pyodide-lock.json からたどる)
PACKAGES = ("numpy", "pillow")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch(name: str, cache: Path | None) -> bytes:
    if cache is not None and (cache / name).is_file():
        return (cache / name).read_bytes()
    request = urllib.request.Request(BASE_URL + name, headers={"User-Agent": "block-video-player-build"})
    with urllib.request.urlopen(request, timeout=120) as response:
        data = response.read()
    if cache is not None:
        cache.mkdir(parents=True, exist_ok=True)
        (cache / name).write_bytes(data)
    return data


def package_files(lock: dict, names=PACKAGES) -> dict[str, str]:
    """パッケージ (と依存) の {ファイル名: SHA-256}。"""
    files, todo, seen = {}, list(names), set()
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        entry = lock["packages"][name]
        files[entry["file_name"]] = entry["sha256"]
        todo.extend(entry.get("depends", []))
    return files


def build(out, cache=None) -> list[str]:
    out = Path(out)
    cache = Path(cache) if cache else None
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    written = []
    for name, expected in CORE_FILES.items():
        data = fetch(name, cache)
        if sha256(data) != expected:
            raise ValueError(f"{name} の SHA-256 が違います (取ってきたものが変わっている): {sha256(data)}")
        (out / name).write_bytes(data)
        written.append(name)
    lock = json.loads((out / "pyodide-lock.json").read_text(encoding="utf-8"))
    for name, expected in package_files(lock).items():
        data = fetch(name, cache)
        if sha256(data) != expected:
            raise ValueError(f"{name} の SHA-256 が pyodide-lock.json と違います: {sha256(data)}")
        (out / name).write_bytes(data)
        written.append(name)
    return written


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", required=True, help="置くフォルダ (サイトの pyodide/)")
    parser.add_argument("--cache", default=None, help="取ってきたファイルを置いておくフォルダ (2 回目から取りに行かない)")
    args = parser.parse_args(argv)
    try:
        written = build(args.out, args.cache)
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"Pyodide {PYODIDE_VERSION}: {len(written)} files -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
