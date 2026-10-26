"""配置：环境变量与 agent 配置文件。见 docs/interfaces.md 第 3、6.1、10 节。"""

import hmac
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent

VALID_SCOPES = {"public", "finance", "support", "release", "rnd", "mgmt"}
VALID_LANGS = {"zh", "en"}


class ConfigError(Exception):
    """配置不合法。服务启动时遇到它应直接退出并打印原因。"""


def _path(env_name: str, default: str) -> Path:
    """相对路径按仓库根目录解析，与从哪个目录启动无关。"""
    p = Path(os.environ.get(env_name, default))
    return p if p.is_absolute() else ROOT / p


@dataclass(frozen=True)
class Config:
    host: str
    port: int
    db_path: Path
    agents_file: Path
    lang: str
    review_timeout: int
    session_idle_seconds: int

    @classmethod
    def from_env(cls) -> "Config":
        try:
            port = int(os.environ.get("MEMORY_PORT", "8000"))
            timeout = int(os.environ.get("REVIEW_TIMEOUT", "30"))
            idle = int(os.environ.get("SESSION_IDLE_SECONDS", "1800"))
        except ValueError as e:
            raise ConfigError(f"MEMORY_PORT、REVIEW_TIMEOUT、SESSION_IDLE_SECONDS 必须是整数：{e}") from None
        if idle <= 0 or timeout <= 0:
            raise ConfigError("REVIEW_TIMEOUT 和 SESSION_IDLE_SECONDS 必须是正数")

        lang = os.environ.get("MEMORY_LANG", "zh")
        if lang not in VALID_LANGS:
            raise ConfigError(f"MEMORY_LANG 只能是 zh 或 en，当前为 {lang!r}")

        return cls(
            host=os.environ.get("MEMORY_HOST", "127.0.0.1"),
            port=port,
            db_path=_path("MEMORY_DB", "data/memory.db"),
            agents_file=_path("AGENTS_FILE", "config/agents.json"),
            lang=lang,
            review_timeout=timeout,
            session_idle_seconds=idle,
        )


def load_agents(path: Path) -> list[dict]:
    """读取并校验 agents.json，返回 agent 列表。"""
    if not path.exists():
        raise ConfigError(f"找不到 {path}，请先运行 python3 scripts/gen_keys.py")
    try:
        agents = json.loads(path.read_text(encoding="utf-8"))["agents"]
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        raise ConfigError(f"{path.name} 格式不对：{e}") from None

    ids, keys = set(), set()
    for a in agents:
        aid = a.get("agent_id")
        key = a.get("key", "")
        if not aid or aid in ids:
            raise ConfigError(f"agent_id 缺失或重复：{aid!r}")
        if not isinstance(key, str) or key.startswith("<") or len(key) < 16:
            raise ConfigError(f"{aid} 的密钥无效，请运行 python3 scripts/gen_keys.py 生成")
        if key in keys:
            raise ConfigError(f"{aid} 的密钥与其他 agent 重复")
        if a.get("level") not in (0, 1, 2, 3) or type(a.get("level")) is not int:
            raise ConfigError(f"{aid} 的 level 必须是 0–3 的整数")
        if not set(a.get("read_scopes", [])) <= VALID_SCOPES or not a.get("read_scopes"):
            raise ConfigError(f"{aid} 的 read_scopes 不合法")
        if a.get("write_scope") not in VALID_SCOPES:
            raise ConfigError(f"{aid} 的 write_scope 不合法")
        ids.add(aid)
        keys.add(key)
    return agents


def find_agent(agents: list[dict], key: Optional[str]) -> Optional[dict]:
    """按密钥查找 agent，找不到返回 None。

    用 hmac.compare_digest 做定长时间比较，避免通过响应时间猜出密钥。
    返回的字典不含密钥，防止密钥被写进日志或返回给 agent。
    """
    if not key:
        return None
    for a in agents:
        if hmac.compare_digest(a["key"].encode(), key.encode()):
            return {k: v for k, v in a.items() if k != "key"}
    return None
