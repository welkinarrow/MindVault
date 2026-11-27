"""备用脚本化客户端（A4 的兜底）：不用模型，按固定顺序调用工具，演示两个场景。

本地模型在台上不稳定时用它代替 local_agent.py。调用的是同一个服务、同样的密钥和工具，
权限检查、内外部、引用、来源链都是真的；只有“下一步调哪个工具、回答写什么”是预先写好的。
上台时如实说明：这是脚本化的客户端。

用法（服务已运行，种子已按场景 reset；激活 .venv）：
    python scripts/scripted_client.py scenario1          # 小王：云端 vs 本地，同一个问题
    python scripts/scripted_client.py scenario2          # 小周：拼出客户 B 的折扣来龙去脉，提交引用
    python scripts/scripted_client.py scenario2 --write  # 再写入 b-610（续约结果），带来源链

开始前会对用到的 agent 跑 reset_taint，保证 b-610 的来源链只有这一轮读过的记忆。
输出格式和 local_agent.py 一样；转录写入 runs/<时间>-scripted-<场景>.jsonl。
"""

import argparse
import asyncio
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]

from local_agent import DIM, GREEN, RED, RESET, run_tool, summarize  # noqa: E402
from selftest import Agent  # noqa: E402
from server.config import Config, ConfigError, load_agents  # noqa: E402

SCENARIO1_ANSWER_DEPLOY = "部署前要在跳板机上执行 release.sh，并通知值班工程师 [b-802]。"
SCENARIO1_ANSWER_ONBOARD = "第一周要完成账号开通和安全培训，周五参加部门介绍会 [b-801]。"
SCENARIO2_ANSWER = ("85 折是 2024 年故障后的补偿折扣，到 2026 年底到期 [b-602]，所以续约可以恢复原价。"
                    "例外是：如果采购量比上年增加 30%，按补充协议要继续给 85 折 [b-603]。"
                    "最初的 8 折签约优惠已经不再适用 [b-601]。建议先核对客户 B 今年的采购量。")

# 每一幕：(agent_id, 提问, [(工具, 参数), ...], 最终回答或 None)
# 工具名是 local_agent.py 给模型的 4 个工具，由 run_tool 映射到服务。
SCENARIOS = {
    "scenario1": [
        ("wang-cloud", "新员工第一周要做什么？",
         [("memory_search", {"query": "新员工 第一周"}), ("memory_read", {"block_id": "b-801"}),
          ("memory_cite", {"block_ids": ["b-801"], "answer": SCENARIO1_ANSWER_ONBOARD})],
         SCENARIO1_ANSWER_ONBOARD),
        ("wang-cloud", "怎么部署到生产环境？",
         [("memory_search", {"query": "部署 生产环境"}), ("memory_read", {"block_id": "b-802"})],
         "部署手册标为内部，我用的是云端模型，读不到。请改用本地助手查询。"),
        ("wang-local", "怎么部署到生产环境？",
         [("memory_search", {"query": "部署 生产环境"}), ("memory_read", {"block_id": "b-802"}),
          ("memory_cite", {"block_ids": ["b-802"], "answer": SCENARIO1_ANSWER_DEPLOY})],
         SCENARIO1_ANSWER_DEPLOY),
        ("wang-local", "把部署步骤整理成一份速查表记下来。",
         [("memory_write", {"title": "部署速查表", "content": "部署前先在跳板机上执行 release.sh，再通知值班工程师。"})],
         "速查表已写入，读过内部手册，所以默认是内部；等待管理员审批，批准前谁都读不到。"),
    ],
    "scenario2": [
        ("zhou-local", "客户 B 为什么是 85 折？续约时能恢复原价吗？",
         [("memory_search", {"query": "客户B 折扣"}), ("memory_read", {"block_id": "b-602"}),
          ("memory_read", {"block_id": "b-603"}), ("memory_read", {"block_id": "b-601"}),
          ("memory_cite", {"block_ids": ["b-602", "b-603", "b-601"], "answer": SCENARIO2_ANSWER})],
         SCENARIO2_ANSWER),
    ],
}
SCENARIO2_WRITE = ("zhou-local", "把这次续约的结果记下来。",
                   [("memory_write", {"title": "客户 B 2027 年续约",
                                      "content": "客户 B 2027 年续约：采购量比上年增加 35%，按 2025 年补充协议继续保持 85 折。"})],
                   "已写入，来源链是 b-601、b-602、b-603；等待管理员审批。")


async def play(acts: list, keys: dict, url: str, log) -> None:
    for agent_id, question, steps, answer in acts:
        print(f"\n{agent_id} 提问：{question}")
        log({"event": "question", "agent": agent_id, "question": question})
        errors = 0
        async with Agent(url, keys[agent_id]) as mcp:
            for step, (tool, args) in enumerate(steps, start=1):
                result = await run_tool(mcp, tool, args)
                errors += "error" in result   # 权限拒绝是演示内容；工具出错才是问题
                line, refused = summarize(tool, result)
                print(f"[{step}] {tool} {json.dumps(args, ensure_ascii=False)[:80]} → {RED if refused else GREEN}{line}{RESET}")
                log({"event": "tool", "agent": agent_id, "tool": tool, "args": args, "result": result})
        if errors:
            print(f"{RED}注意：有 {errors} 步工具出错，下面预先写好的回答可能和实际数据不符{RESET}")
        print(f"回答：{answer}")
        log({"event": "answer", "agent": agent_id, "content": answer})


def main() -> None:
    parser = argparse.ArgumentParser(description="备用脚本化客户端（不用模型）")
    parser.add_argument("scenario", choices=sorted(SCENARIOS))
    parser.add_argument("--write", action="store_true", help="场景二最后写入 b-610")
    parser.add_argument("--no-reset", action="store_true", help="不清污点（默认开始前清）")
    args = parser.parse_args()

    acts = SCENARIOS[args.scenario] + ([SCENARIO2_WRITE] if args.write and args.scenario == "scenario2" else [])
    try:
        cfg = Config.from_env()
        keys = {a["agent_id"]: a["key"] for a in load_agents(cfg.agents_file)}
    except ConfigError as e:
        sys.exit(f"配置错误：{e}")
    needed = sorted({a for a, *_ in acts})
    missing = [a for a in needed if a not in keys]
    if missing:
        sys.exit(f"agents.json 里缺少：{', '.join(missing)}（见 interfaces.md 13.9）")
    if not args.no_reset:
        for agent_id in needed:
            subprocess.run([sys.executable, str(ROOT / "scripts" / "reset_taint.py"), agent_id],
                           check=True, capture_output=True)
        print(f"{DIM}已清污点：{', '.join(needed)}{RESET}")

    runs = ROOT / "runs"
    runs.mkdir(exist_ok=True)
    path = runs / f"{datetime.now():%Y%m%d-%H%M%S}-scripted-{args.scenario}.jsonl"
    with path.open("w", encoding="utf-8") as f:
        def log(event: dict) -> None:
            f.write(json.dumps({"time": datetime.now().isoformat(timespec="seconds"), **event}, ensure_ascii=False) + "\n")

        print(f"{DIM}脚本化客户端：工具调用是真的，下一步做什么和回答是预先写好的{RESET}")
        asyncio.run(play(acts, keys, f"http://{cfg.host}:{cfg.port}/mcp", log))
    print(f"\n{DIM}转录：{path.relative_to(ROOT)}{RESET}")


if __name__ == "__main__":
    main()
