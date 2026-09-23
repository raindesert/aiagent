"""pytest 共享 fixture: 配置、工具注册表、临时 SQLite store、本地模型可用性探测。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent import MemoryStore, ToolRegistry, load_config, load_models_config  # noqa: E402


def _reachable(url: str) -> bool:
    try:
        return requests.get(url, timeout=2).status_code < 500
    except Exception:
        return False


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return ROOT


@pytest.fixture(scope="session")
def cfg():
    """解析后的 agent.yaml, 不触发任何网络调用。"""
    return load_config(ROOT / "agent.yaml")


@pytest.fixture(scope="session")
def models_cfg():
    return load_models_config(ROOT / "models.yaml")


@pytest.fixture(scope="session")
def default_entry(models_cfg):
    return next(m for m in models_cfg.models if m.name == models_cfg.default)


@pytest.fixture(scope="session")
def registry(cfg):
    return ToolRegistry(cfg.tools)


@pytest.fixture
def store(tmp_path):
    return MemoryStore(tmp_path / "nested" / "test.db")


@pytest.fixture(scope="session")
def local_model(default_entry):
    """默认模型后端可用时才跑, 否则跳过依赖它的用例。"""
    if not _reachable(default_entry.base_url.rstrip("/") + "/models"):
        pytest.skip(f"模型服务不可用: {default_entry.base_url}")
    return default_entry


@pytest.fixture
def safe_cfg(cfg):
    """只保留只读工具的副本, 用于端到端跑真实 agent 循环。"""
    import copy

    c = copy.deepcopy(cfg)
    read_only = {"get_current_time", "search_files", "grep_search", "read_file"}
    c.tools = [t for t in c.tools if t.name in read_only]
    c.loop.max_iterations = 4
    return c
