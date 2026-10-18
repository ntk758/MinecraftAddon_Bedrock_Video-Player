"""実映像の評価 (benchmark/real_video_bench.py、Phase 8.8) が使う映像の一覧のテスト。映像は取得しない。"""
import json
from pathlib import Path

DATASET = Path(__file__).resolve().parent.parent / "benchmark" / "datasets" / "real_videos.json"


def test_real_video_dataset_is_reproducible_and_licensed():
    videos = json.loads(DATASET.read_text(encoding="utf-8"))["videos"]
    assert len({v["id"] for v in videos}) == len(videos) >= 3
    for v in videos:
        assert v["url"].startswith("https://download.blender.org/"), v["id"]
        assert v["license"] == "CC-BY 3.0" and v["credit"], v["id"]
        assert v["duration"] > 0 and v["start"] >= 0
        # 全体を取得するものは SHA-256 で同じファイルか確かめる。一部だけ読むものは区間で決まる
        if not v["segment"]:
            assert len(v["sha256"]) == 64, v["id"]
