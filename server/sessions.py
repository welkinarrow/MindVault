"""会话记录：服务端按 agent 维护的活动会话。见 interfaces.md 第 3.1 节（v0.6 起）。

为什么不用 MCP 的会话：2026-07-28 版协议删除了握手和 Mcp-Session-Id，
服务端拿不到连接级的会话 ID。如果改用工具返回的“任务编号”，agent 可以换一个
新编号来重置污点。所以会话由服务端按 agent 身份维护：

- 同一个 agent 连续活动期间属于同一个会话；
- 距上次调用超过 idle_seconds 后，下一次调用自动开始新会话；
- 会话 ID 由服务端生成，agent 无法指定或重置。

代价是偏严：同一 agent 在空闲超时内做的多个任务共享污点（宁可多定级）。
只保存在内存中，服务重启后清空。
"""

import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class Session:
    session_id: str
    agent_id: str
    last_active: float
    max_level_read: int = 0
    read_block_ids: set[str] = field(default_factory=set)


class SessionStore:
    def __init__(self, idle_seconds: int = 1800, clock: Callable[[], float] = time.monotonic):
        if idle_seconds <= 0:
            raise ValueError("idle_seconds 必须是正数")
        self.idle_seconds = idle_seconds
        self._clock = clock
        self._by_agent: dict[str, Session] = {}
        self._lock = threading.Lock()

    def current(self, agent_id: str) -> tuple[Session, bool]:
        """返回该 agent 当前的会话和“是否新建”，并刷新最近活动时间。

        每次工具调用开始时调用一次。
        """
        with self._lock:
            now = self._clock()
            s = self._by_agent.get(agent_id)
            created = s is None or now - s.last_active > self.idle_seconds
            if created:
                s = Session(f"{agent_id}-{secrets.token_hex(4)}", agent_id, now)
                self._by_agent[agent_id] = s
            s.last_active = now
            return s, created

    def record_read(self, session: Session, block: dict) -> None:
        """记录一次成功的读取。只在 block 内容真正返回给 agent 时调用；被拒的读取不要调用。"""
        with self._lock:
            session.read_block_ids.add(block["block_id"])
            session.max_level_read = max(session.max_level_read, block["level"])

    def has_read(self, session: Session, block_id: str) -> bool:
        with self._lock:
            return block_id in session.read_block_ids

    def max_level(self, session: Session) -> int:
        with self._lock:
            return session.max_level_read
