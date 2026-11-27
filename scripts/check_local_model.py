#!/usr/bin/env python3
"""检查本地模型能否胜任 MindVault v2 的两类工作（A0 第 2 步）。

1. JSON 输出：记忆管理 agent 每次只要一个 {"keep": ..., "reason": ...}
2. 工具调用：轻量本地 agent 要从 4 个工具里选对一个、参数正确

只用 Python 标准库，不用装任何包。先确保 Ollama 在运行、模型已经 pull 下来。

用法：
    python scripts/check_local_model.py
    python scripts/check_local_model.py --model qwen3:8b --runs 5
"""

import argparse
import json
import time
import urllib.error
import urllib.request

BASE = "http://localhost:11434"

# ---------------------------------------------------------------- 测试材料

JSON_SYSTEM = (
    "你是公司记忆库的审核员。判断一条新写入的记忆是否值得保存。"
    '只输出一个 JSON 对象，格式是 {"keep": true 或 false, "reason": "一句中文理由"}，'
    "不要输出任何其他文字。"
)
JSON_USER = (
    "标题：客户 B 2027 年续约\n"
    "内容：客户 B 2027 年续约，采购量比上年增加 35%，按 2025 年补充协议继续保持 85 折。\n"
    "这条记忆值得保存吗？"
)

TOOLS = [
    {"type": "function", "function": {
        "name": "memory_search",
        "description": "按关键词搜索公司记忆，返回最相关的记忆编号和标题。",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "搜索关键词"}},
            "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "memory_read",
        "description": "按编号读取一条记忆的全文，例如 b-602。",
        "parameters": {"type": "object", "properties": {
            "block_id": {"type": "string", "description": "记忆编号"}},
            "required": ["block_id"]}}},
    {"type": "function", "function": {
        "name": "memory_write",
        "description": "把一条新记忆写入公司记忆库。",
        "parameters": {"type": "object", "properties": {
            "title": {"type": "string"}, "content": {"type": "string"}},
            "required": ["title", "content"]}}},
    {"type": "function", "function": {
        "name": "memory_cite",
        "description": "回答结束前提交引用：按重要性从高到低，最多 10 个记忆编号。",
        "parameters": {"type": "object", "properties": {
            "block_ids": {"type": "array", "items": {"type": "string"}}},
            "required": ["block_ids"]}}},
]
AGENT_SYSTEM = (
    "你是客户经理小周的助手。只能根据工具返回的公司记忆回答问题，"
    "先搜索，再按编号读取全文，回答时每句话后面用 [b-xxx] 标出处，"
    "最后调用 memory_cite 提交引用。"
)
AGENT_USER = "客户 B 为什么是 85 折？续约时能恢复原价吗？"
FAKE_SEARCH_RESULT = json.dumps({"results": [
    {"block_id": "b-601", "title": "客户 B 首年签约优惠"},
    {"block_id": "b-602", "title": "客户 B 故障补偿折扣"},
    {"block_id": "b-603", "title": "客户 B 补充协议"},
]}, ensure_ascii=False)


# ---------------------------------------------------------------- 工具函数

def call(path, body=None, timeout=300):
    """调 Ollama，返回 (结果, 秒数)。出错时返回 ({"_error": ...}, 秒数)。"""
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={"Content-Type": "application/json"})
    start = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            out = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        out = {"_error": f"HTTP {e.code}: {e.read().decode(errors='replace')[:300]}"}
    except Exception as e:  # 连不上、超时等
        out = {"_error": f"{type(e).__name__}: {e}"}
    return out, time.time() - start


def parse_keep(text):
    """严格解析：必须整段就是 JSON，且 keep 是布尔、reason 是非空字符串。"""
    try:
        obj = json.loads(text.strip())
    except Exception:
        return False, "不是合法 JSON"
    if not isinstance(obj, dict):
        return False, "不是 JSON 对象"
    if not isinstance(obj.get("keep"), bool):
        return False, "keep 不是 true/false"
    if not isinstance(obj.get("reason"), str) or not obj["reason"].strip():
        return False, "缺少 reason"
    return True, f"keep={obj['keep']}"


def tool_calls_native(msg):
    out = []
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function", {})
        args = fn.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except Exception:
                args = {"_raw": args}
        out.append((fn.get("name"), args))
    return out


def tool_calls_openai(resp):
    msg = (resp.get("choices") or [{}])[0].get("message", {})
    out = []
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function", {})
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except Exception:
            args = {"_raw": fn.get("arguments")}
        out.append((fn.get("name"), args))
    return out, msg


def line(title):
    print("\n" + "=" * 8 + " " + title + " " + "=" * 8)


# ---------------------------------------------------------------- 各项检查

def check_server(model):
    line("0. Ollama 和模型")
    tags, _ = call("/api/tags", timeout=10)
    if "_error" in tags:
        print("连不上 Ollama：", tags["_error"])
        print("请先打开 Ollama 应用（菜单栏有小羊驼图标），再重跑。")
        return False
    names = [m.get("name") for m in tags.get("models", [])]
    if model not in names:
        print(f"没找到模型 {model}。已有：{names or '无'}")
        print(f"请先运行：ollama pull {model}")
        return False
    ver, _ = call("/api/version", timeout=10)
    print(f"Ollama 版本 {ver.get('version', '?')}，模型 {model} 已就绪")
    print("预热（第一次要把模型载入内存，可能要 10–60 秒）……")
    out, secs = call("/api/chat", {"model": model, "stream": False, "think": False,
                                   "messages": [{"role": "user", "content": "你好"}],
                                   "options": {"temperature": 0}})
    if "_error" in out:
        print("预热失败：", out["_error"])
        if "think" in out["_error"]:
            print("可能是 Ollama 版本太旧，不认识 think 参数。请到 ollama.com 下载最新版。")
        return False
    print(f"预热完成，用时 {secs:.1f} 秒")
    return True


def check_json(model, runs):
    line(f"1. JSON 输出（每种方式 {runs} 次）")
    modes = [
        ("关思考 + format=json", {"think": False, "format": "json"}),
        ("关思考，只靠提示词", {"think": False}),
        ("开思考 + format=json", {"think": True, "format": "json"}),
    ]
    summary = []
    for label, extra in modes:
        ok, times, notes = 0, [], []
        for _ in range(runs):
            body = {"model": model, "stream": False, "options": {"temperature": 0},
                    "messages": [{"role": "system", "content": JSON_SYSTEM},
                                 {"role": "user", "content": JSON_USER}], **extra}
            out, secs = call("/api/chat", body)
            times.append(secs)
            if "_error" in out:
                notes.append(out["_error"][:80])
                continue
            good, note = parse_keep(out.get("message", {}).get("content", ""))
            ok += good
            notes.append(note)
        avg = sum(times) / len(times)
        print(f"{label}: 合法 {ok}/{runs}，平均 {avg:.1f} 秒  {sorted(set(notes))}")
        summary.append((label, ok, runs, avg))
    return summary


def check_tools(model, runs):
    line(f"2. 工具调用（每种方式 {runs} 次）")
    base_msgs = [{"role": "system", "content": AGENT_SYSTEM},
                 {"role": "user", "content": AGENT_USER}]
    summary = []

    # 方式 A：Ollama 原生接口，think=False
    ok, times, seen = 0, [], []
    for _ in range(runs):
        out, secs = call("/api/chat", {"model": model, "stream": False, "think": False,
                                       "options": {"temperature": 0},
                                       "messages": base_msgs, "tools": TOOLS})
        times.append(secs)
        if "_error" in out:
            seen.append(out["_error"][:80])
            continue
        calls = tool_calls_native(out.get("message", {}))
        seen.append(calls[0][0] if calls else "没调工具")
        ok += bool(calls) and calls[0][0] == "memory_search" and bool(calls[0][1].get("query"))
    avg = sum(times) / len(times)
    print(f"原生 /api/chat（关思考）: 第一步调对 memory_search {ok}/{runs}，平均 {avg:.1f} 秒  {seen}")
    summary.append(("原生接口", ok, runs, avg))

    # 方式 B：OpenAI 兼容接口，用 /no_think 关思考
    ok, times, seen = 0, [], []
    for _ in range(runs):
        msgs = [base_msgs[0], {"role": "user", "content": AGENT_USER + " /no_think"}]
        out, secs = call("/v1/chat/completions", {"model": model, "temperature": 0,
                                                 "messages": msgs, "tools": TOOLS})
        times.append(secs)
        if "_error" in out:
            seen.append(out["_error"][:80])
            continue
        calls, _ = tool_calls_openai(out)
        seen.append(calls[0][0] if calls else "没调工具")
        ok += bool(calls) and calls[0][0] == "memory_search" and bool(calls[0][1].get("query"))
    avg = sum(times) / len(times)
    print(f"OpenAI 兼容 /v1（/no_think）: 第一步调对 memory_search {ok}/{runs}，平均 {avg:.1f} 秒  {seen}")
    summary.append(("OpenAI 兼容接口", ok, runs, avg))

    # 第二步：给它假的搜索结果，看下一步是不是去读全文
    out, secs = call("/api/chat", {"model": model, "stream": False, "think": False,
                                   "options": {"temperature": 0}, "tools": TOOLS,
                                   "messages": base_msgs + [
                                       {"role": "assistant", "content": "",
                                        "tool_calls": [{"function": {"name": "memory_search",
                                                                     "arguments": {"query": "客户 B 折扣"}}}]},
                                       {"role": "tool", "content": FAKE_SEARCH_RESULT}]})
    if "_error" in out:
        print("第二步出错：", out["_error"][:120])
    else:
        calls = tool_calls_native(out.get("message", {}))
        nxt = ", ".join(f"{n}({json.dumps(a, ensure_ascii=False)})" for n, a in calls) or "没调工具，直接回答"
        print(f"第二步（拿到搜索结果后）: {nxt}，用时 {secs:.1f} 秒")
    return summary


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--model", default="qwen3:8b")
    ap.add_argument("--runs", type=int, default=5)
    args = ap.parse_args()

    if not check_server(args.model):
        return
    js = check_json(args.model, args.runs)
    tl = check_tools(args.model, min(args.runs, 3))

    line("结论")
    for label, ok, n, avg in js + tl:
        mark = "通过" if ok == n else ("基本可用" if ok >= n - 1 else "不稳定")
        print(f"{mark:6} {label}: {ok}/{n}，平均 {avg:.1f} 秒")
    print("\n把上面全部输出贴给 Claude 就行。")


if __name__ == "__main__":
    main()
