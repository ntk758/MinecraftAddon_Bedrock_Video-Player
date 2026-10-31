"""テスト全体の共通の設定。"""
import pytest


@pytest.fixture(autouse=True)
def logs_in_a_temporary_folder(tmp_path_factory, monkeypatch):
    """GUI が人物の切り抜きのログを書く場所 (BVP_LOG_DIR) を、テストごとに一時フォルダにする。
    実機の %LOCALAPPDATA%/BlockVideoPlayer/logs に、テストのログを作らない・消さないため。"""
    monkeypatch.setenv("BVP_LOG_DIR", str(tmp_path_factory.mktemp("logs")))
