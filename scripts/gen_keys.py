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
sys.path.insert(0, str(ROOT))

from server.config import ConfigError, scopes_from, validate_agents  # noqa: E402


def validate(config: dict) -> None:
    """按 interfaces.md 第 2、6.1 节检查模板（范围以模板里的 scopes 为准），不合法直接退出。"""
    try:
        validate_agents(config["agents"], scopes_from(config), check_keys=False)
    except ConfigError as e:
        sys.exit(str(e))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="覆盖已有的 agents.json")
    args = parser.parse_args()

    if OUTPUT.exists() and not args.force:
        sys.exit(f"{OUTPUT.relative_to(ROOT)} 已存在。如需重新生成，加 --force（旧密钥会失效）。")

    config = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    validate(config)

    for a in config["agents"]:
        a["key"] = secrets.token_hex(16)  # 32 位十六进制

    OUTPUT.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"已生成 {OUTPUT.relative_to(ROOT)}，请私下发给需要的人：")
    for a in config["agents"]:
        print(f"  {a['agent_id']:<10} {a['key']}")


if __name__ == "__main__":
    main()
