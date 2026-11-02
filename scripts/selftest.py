"""端到端自测：用两个 agent 的真实密钥，通过 MCP 调用正在运行的服务，走一遍演示场景。

用法（服务已在另一个终端运行；本终端也要激活 .venv）：
    python scripts/reset.py          # 建议先重置数据（需先停止服务，重置后再启动）
    python scripts/selftest.py

每一项检查打印 PASS 或 FAIL，全部通过时退出码为 0。
"""

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    import httpx2
    from mcp import Client
    from mcp.client.streamable_http import streamable_http_client
except ModuleNotFoundError as e:
    sys.exit(f"缺少依赖 {e.name}。请先激活虚拟环境：source .venv/bin/activate")

from server.config import Config, ConfigError, load_agents  # noqa: E402

QUERIES = {
    "zh": {"main": "Q3 华东区 退款率 上升", "intern": "华东区 退款率",
           "title": "自测：Q3 华东区退款率上升原因",
           "content": "退款率上升主要来自 9 月 12 日 v5.4 上线后的支付失败（扣款成功但订单显示失败），集中在华东区。",
           "reason": "自测：与工单数据一致"},
    "en": {"main": "Q3 East China refund rate increase", "intern": "East China refund rate",
           "title": "Self-test: why the Q3 East China refund rate rose",
           "content": "The rise mainly comes from payment failures after the v5.4 release on September 12 "
                      "(charged but order shown as failed), concentrated in East China.",
           "reason": "Self-test: consistent with the ticket data"},
}
DEMO_BLOCKS = {"b-101", "b-207", "b-318"}

failures = 0


def check(ok: bool, message: str, detail: object = None) -> bool:
    global failures
    print(f"  {'PASS' if ok else 'FAIL'}  {message}")
    if not ok:
        failures += 1
        if detail is not None:
            print(f"        实际返回：{json.dumps(detail, ensure_ascii=False)[:300]}")
    return ok


def payload(result) -> dict:
    """从 CallToolResult 中取出工具返回的 JSON 对象。"""
    if getattr(result, "is_error", False):
        texts = [getattr(c, "text", "") for c in result.content]
        raise RuntimeError(f"工具返回错误：{' '.join(texts)}")
    sc = getattr(result, "structured_content", None)
    if isinstance(sc, dict):
        # 有的版本会把返回值包在 {"result": ...} 里
        if set(sc) == {"result"} and isinstance(sc["result"], dict):
            return sc["result"]
        return sc
    for c in result.content:
        text = getattr(c, "text", None)
        if text:
            return json.loads(text)
    raise RuntimeError("无法解析工具返回值")


class Agent:
    """用某个 agent 的密钥连接服务。"""

    def __init__(self, url: str, key: str):
        self.url, self.key = url, key

    async def __aenter__(self):
        self._http = httpx2.AsyncClient(headers={"Authorization": f"Bearer {self.key}"},
                                        timeout=httpx2.Timeout(30.0, read=120.0))
        await self._http.__aenter__()
        self._client = Client(streamable_http_client(self.url, http_client=self._http))
        await self._client.__aenter__()
        return self

    async def __aexit__(self, *exc):
        await self._client.__aexit__(*exc)
        await self._http.__aexit__(*exc)

    async def call(self, tool: str, args: dict) -> dict:
        return payload(await self._client.call_tool(tool, args))

    async def tool_names(self) -> set[str]:
        return {t.name for t in (await self._client.list_tools()).tools}


async def main() -> int:
    try:
        cfg = Config.from_env()
        keys = {a["agent_id"]: a["key"] for a in load_agents(cfg.agents_file)}
    except ConfigError as e:
        sys.exit(f"配置错误：{e}")
    url = f"http://{cfg.host}:{cfg.port}/mcp"
    q = QUERIES[cfg.lang]
    print(f"服务地址：{url}（语言：{cfg.lang}）\n")

    # ------------------------------------------------------------ 财务 agent
    print("[1] 财务分析 agent（L2）")
    try:
        async with Agent(url, keys["finance"]) as finance:
            names = await finance.tool_names()
            check(names == {"memory_read", "memory_write", "memory_rate"}, f"只看到 3 个 memory 工具：{sorted(names)}")

            r = await finance.call("memory_read", {"query": q["main"]})
            found = {b["block_id"] for b in r.get("results", [])}
            check(DEMO_BLOCKS <= found, f"检索到三个演示 block：{sorted(found)}", r)
            check("b-412" not in found and "b-501" not in found, "检索结果中没有 b-412（L3）和 b-501（研发）")
            check("data_notice" in r, "结果带有 data_notice")
            if any(i.startswith("w-") for i in found):
                print("        提示：结果中有以前写入的 w- block，建议先运行 reset.py 重置数据")

            r = await finance.call("memory_read", {"block_id": "b-412"})
            check(r.get("reason_code") == "LEVEL_TOO_LOW" and r.get("required_level") == 3,
                  "按 ID 读 b-412 被拒：LEVEL_TOO_LOW", r)
            r = await finance.call("memory_read", {"block_id": "b-501"})
            check(r.get("reason_code") == "SCOPE_MISMATCH", "按 ID 读 b-501 被拒：SCOPE_MISMATCH", r)

            w = await finance.call("memory_write", {"title": q["title"], "content": q["content"]})
            check(w.get("level") == 2 and w.get("scope") == "finance",
                  f"写入定级 L{w.get('level')} / {w.get('scope')}（预期 L2 / finance）", w)
            review = w.get("review", {})
            print(f"        审核结果：{review.get('action')}，理由：{review.get('reason')}，新 block：{w.get('block_id')}")
            new_id = w.get("block_id")

            ratings = [{"block_id": b, "accuracy": 5, "reason": q["reason"]} for b in sorted(DEMO_BLOCKS)]
            ratings.append({"block_id": "b-412", "accuracy": 5, "reason": q["reason"]})
            r = await finance.call("memory_rate", {"ratings": ratings})
            got = {x["block_id"]: x for x in r.get("results", [])}
            check(all(got.get(b, {}).get("accepted") for b in DEMO_BLOCKS), "三个演示 block 的评分被接收", r)
            check(got.get("b-412", {}).get("reason_code") == "NOT_READ_IN_SESSION", "没读过的 b-412 评分被拒收", r)
    except Exception as e:
        check(False, f"财务 agent 调用失败：{type(e).__name__}: {e}")
        new_id = None

    # ------------------------------------------------------------ 实习助手
    print("\n[2] 实习助手（L0）")
    try:
        async with Agent(url, keys["intern"]) as intern:
            if new_id:
                r = await intern.call("memory_read", {"block_id": new_id})
                check(r.get("reason_code") == "LEVEL_TOO_LOW", f"读财务刚写入的 {new_id} 被拒：LEVEL_TOO_LOW", r)
            else:
                check(False, "没有可供检查的新 block（写入失败或被审核拒绝）")
            r = await intern.call("memory_read", {"query": q["intern"]})
            levels = {b["level"] for b in r.get("results", [])}
            check(levels <= {0}, f"检索只返回公开内容：{[b['block_id'] for b in r.get('results', [])]}", r)
    except Exception as e:
        check(False, f"实习助手调用失败：{type(e).__name__}: {e}")

    # ------------------------------------------------------------ 错误密钥
    print("\n[3] 错误密钥")
    try:
        async with Agent(url, "0" * 32) as bad:
            await bad.call("memory_read", {"query": q["intern"]})
        check(False, "错误密钥居然调用成功了")
    except Exception as e:
        check(True, f"错误密钥被拒绝（{type(e).__name__}）")

    print(f"\n{'全部通过' if failures == 0 else f'{failures} 项失败'}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
