"""根据 config/agents.example.json 生成带密钥的 config/agents.json。

用法：
    python scripts/gen_keys.py           # agents.json 已存在时拒绝覆盖
    python scripts/gen_keys.py --force   # 重新生成全部密钥（旧密钥立即失效）

生成的密钥由 A 私下发给 C，不要提交到仓库、不要出现在录屏中。
"""

import argparse
import json
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "config" / "agents.example.json"
OUTPUT = ROOT / "config" / "agents.json"

VALID_SCOPES = {"public", "finance", "support", "release", "rnd", "mgmt"}


def validate(agents: list[dict]) -> None:
    """按 interfaces.md 第 2、6.1 节检查模板，不合法直接退出。"""
    seen = set()
    for a in agents:
        aid = a.get("agent_id")
        if not aid or aid in seen:
            sys.exit(f"agent_id 缺失或重复：{aid!r}")
        seen.add(aid)
        if a.get("level") not in (0, 1, 2, 3):
            sys.exit(f"{aid}: level 必须是 0–3 的整数")
        scopes = set(a.get("read_scopes", []))
        if not scopes or not scopes <= VALID_SCOPES:
            sys.exit(f"{aid}: read_scopes 含非法取值 {scopes - VALID_SCOPES}")
        if a.get("write_scope") not in VALID_SCOPES:
            sys.exit(f"{aid}: write_scope 非法")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="覆盖已有的 agents.json")
    args = parser.parse_args()

    if OUTPUT.exists() and not args.force:
        sys.exit(f"{OUTPUT.relative_to(ROOT)} 已存在。如需重新生成，加 --force（旧密钥会失效）。")

    config = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    validate(config["agents"])

    for a in config["agents"]:
        a["key"] = secrets.token_hex(16)  # 32 位十六进制

    OUTPUT.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"已生成 {OUTPUT.relative_to(ROOT)}，请私下发给需要的人：")
    for a in config["agents"]:
        print(f"  {a['agent_id']:<10} {a['key']}")


if __name__ == "__main__":
    main()
