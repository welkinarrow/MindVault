"""校验种子数据并重建数据库。

用法：
    python scripts/reset.py            # 校验 → 重建数据库 → 导入种子数据
    python scripts/reset.py --check    # 只校验，不改动数据库（B 改完数据后用）

环境变量（见 docs/interfaces.md 第 10 节）：
    SEED_FILE   默认 data/seed_blocks.json；录屏前改为 data/seed_blocks_en.json
    MEMORY_DB   默认 data/memory.db

校验不通过时直接退出，现有数据库保持不动。新数据库先写到临时文件，
全部成功后才替换旧文件。运行前请先停止 MCP 服务。
"""

import argparse
import json
import os
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = ROOT / "server" / "schema.sql"

VALID_SCOPES = {"public", "finance", "support", "release", "rnd", "mgmt"}
SEED_STATUSES = {"verified", "unverified"}           # 种子数据不允许 quarantined
REQUIRED_FIELDS = {"block_id", "title", "content", "level", "scope", "status", "author"}
DEMO_BLOCKS = {"b-101", "b-207", "b-318", "b-412"}   # 演示必需
MAX_TITLE, MAX_CONTENT = 100, 2000                   # 与 memory_write 的限制一致


def resolve(env_name: str, default: str) -> Path:
    """环境变量里的相对路径按仓库根目录解析，与从哪个目录运行无关。"""
    path = Path(os.environ.get(env_name, default))
    return path if path.is_absolute() else ROOT / path


def validate(blocks: object) -> list[str]:
    """按 interfaces.md 第 6.2 节校验，返回全部错误（不在第一个错误处停下）。"""
    if not isinstance(blocks, list) or not blocks:
        return ["种子文件必须是非空的 JSON 数组"]

    errors: list[str] = []
    seen: set[str] = set()
    for i, b in enumerate(blocks):
        where = f"第 {i + 1} 个 block"
        if not isinstance(b, dict):
            errors.append(f"{where}：不是 JSON 对象")
            continue

        bid = b.get("block_id")
        if isinstance(bid, str):
            where = bid

        missing, extra = REQUIRED_FIELDS - b.keys(), b.keys() - REQUIRED_FIELDS
        if missing:
            errors.append(f"{where}：缺少字段 {sorted(missing)}")
        if extra:
            errors.append(f"{where}：多余字段 {sorted(extra)}")

        if not isinstance(bid, str) or not bid.startswith("b-"):
            errors.append(f"{where}：block_id 必须是以 b- 开头的字符串")
        elif bid in seen:
            errors.append(f"{where}：block_id 重复")
        else:
            seen.add(bid)

        # type() 而非 isinstance()：排除 True/False（bool 是 int 的子类）
        if type(b.get("level")) is not int or not 0 <= b["level"] <= 3:
            errors.append(f"{where}：level 必须是 0–3 的整数，当前为 {b.get('level')!r}")
        if b.get("scope") not in VALID_SCOPES:
            errors.append(f"{where}：scope 非法：{b.get('scope')!r}")
        if b.get("status") not in SEED_STATUSES:
            errors.append(f"{where}：status 必须是 verified 或 unverified，当前为 {b.get('status')!r}")

        for field, limit in (("title", MAX_TITLE), ("content", MAX_CONTENT)):
            if field not in b:
                continue  # 已在“缺少字段”中报告
            value = b[field]
            if not isinstance(value, str) or not value.strip():
                errors.append(f"{where}：{field} 不能为空")
            elif len(value) > limit:
                errors.append(f"{where}：{field} 超过 {limit} 字符（{len(value)}）")
        if "author" in b and (not isinstance(b["author"], str) or not b["author"].strip()):
            errors.append(f"{where}：author 不能为空")

    missing_demo = DEMO_BLOCKS - seen
    if missing_demo:
        errors.append(f"缺少演示必需的 block：{sorted(missing_demo)}")
    return errors


def rebuild(db_path: Path, blocks: list[dict]) -> None:
    """在临时文件中建库并导入，成功后原子替换旧库。"""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = db_path.with_name(db_path.name + ".tmp")
    tmp_path.unlink(missing_ok=True)

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn = sqlite3.connect(tmp_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(SCHEMA.read_text(encoding="utf-8"))
        conn.executemany(
            "INSERT INTO blocks (block_id, title, content, level, scope, status, author, created_at) "
            "VALUES (:block_id, :title, :content, :level, :scope, :status, :author, :created_at)",
            [{**b, "created_at": now} for b in blocks],
        )
        conn.commit()
    except Exception:
        conn.close()
        tmp_path.unlink(missing_ok=True)
        raise
    conn.close()
    os.replace(tmp_path, db_path)


def summarize(blocks: list[dict]) -> str:
    levels = Counter(f"L{b['level']}" for b in blocks)
    scopes = Counter(b["scope"] for b in blocks)
    statuses = Counter(b["status"] for b in blocks)
    fmt = lambda c: ", ".join(f"{k}={v}" for k, v in sorted(c.items()))
    return f"  级别：{fmt(levels)}\n  范围：{fmt(scopes)}\n  状态：{fmt(statuses)}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="只校验种子数据，不改动数据库")
    args = parser.parse_args()

    seed_path = resolve("SEED_FILE", "data/seed_blocks.json")
    db_path = resolve("MEMORY_DB", "data/memory.db")

    try:
        blocks = json.loads(seed_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        sys.exit(f"找不到种子文件：{seed_path}")
    except json.JSONDecodeError as e:
        sys.exit(f"{seed_path.name} 不是合法的 JSON：第 {e.lineno} 行第 {e.colno} 列，{e.msg}")

    errors = validate(blocks)
    if errors:
        print(f"{seed_path.name} 校验失败（{len(errors)} 处），数据库未改动：", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        sys.exit(1)

    print(f"{seed_path.name} 校验通过：{len(blocks)} 个 block")
    print(summarize(blocks))
    if args.check:
        return

    rebuild(db_path, blocks)
    print(f"已重建 {db_path.relative_to(ROOT) if db_path.is_relative_to(ROOT) else db_path}")


if __name__ == "__main__":
    main()
