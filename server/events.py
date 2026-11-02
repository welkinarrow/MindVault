"""结构化事件日志 logs/events.jsonl（I-6，见 docs/interfaces_en.md 第 8.1 节）。

每个事件一行 JSON，与文本日志 server.log 一一对应。供前端事件流和 HW7 评分脚本读取。
每条记录都有 time、event、agent、block、level、scope、result 七个字段（不适用时为 null），
另加 run_id 和 label_mode；其余字段按事件类型附加。

只记录 agent_id，绝不记录密钥。写入失败只打印警告，不影响工具调用。
"""

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger("memory")

BASE_FIELDS = ("agent", "block", "level", "scope", "result")


class EventLog:
    def __init__(self, path: Optional[Path], run_id: Optional[str] = None, label_mode: str = "taint"):
        """path 为 None 时不写文件（单元测试默认如此）。"""
        self.path = Path(path) if path is not None else None
        self.run_id = run_id or None
        self.label_mode = label_mode
        self._lock = threading.Lock()
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, event: str, **fields: object) -> Optional[dict]:
        if self.path is None:
            return None
        record = {
            "time": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "event": event,
            **{k: fields.pop(k, None) for k in BASE_FIELDS},
            "run_id": self.run_id,
            "label_mode": self.label_mode,
            **fields,
        }
        line = json.dumps(record, ensure_ascii=False, default=str)
        try:
            with self._lock, open(self.path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError as e:
            log.warning("%-10s %s", "[EVENTS]", f"写入 {self.path} 失败：{e}")
        return record


NULL_EVENTS = EventLog(None)
