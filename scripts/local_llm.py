"""本地模型的公共调用模块：记忆管理 agent（B）和轻量本地 agent（A）都用它，不要各写一套。

只用 Python 标准库，走 Ollama 原生接口 /api/chat，固定关闭思考（think=false）、温度 0。
11/26 在 MacBook Air 上实测：关思考时 JSON 5/5 合法、约 1.2 秒；工具调用 3/3 选对、约 1.3 秒。
OpenAI 兼容接口 /v1 加 /no_think 关不掉思考（约 10 秒），所以不用它。

用法：
    import sys; sys.path.insert(0, "<仓库根目录>/scripts")
    from local_llm import chat, chat_json, tool_message

    reply = chat([{"role": "user", "content": "你好"}])
    print(reply.content, reply.seconds)

    result = chat_json(messages, validate=KeepAnswer.model_validate)   # pydantic 模型
    if result.ok:
        answer = result.value
    else:
        ...  # 输出不合法、超时或连不上：按“不确定”处理，例如不建议降级

命令行：
    python scripts/local_llm.py --check     # Ollama 是否在运行、模型是否就绪
    python scripts/local_llm.py --warmup    # 把模型载入内存并保持 24 小时

环境变量（都有默认值）：
    LOCAL_LLM_URL       http://localhost:11434
    LOCAL_LLM_MODEL     mv-qwen3（scripts/local_model_up.sh 创建；8K 上下文、温度 0）
    LOCAL_LLM_TIMEOUT   60（秒，单次请求）
    LOCAL_LLM_KEEP_ALIVE 24h（每次请求都续期，演示中途不会被卸载）
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

DEFAULT_URL = "http://localhost:11434"
DEFAULT_MODEL = "mv-qwen3"
DEFAULT_TIMEOUT = 60.0
DEFAULT_KEEP_ALIVE = "24h"


class LocalLLMError(RuntimeError):
    """连不上 Ollama、HTTP 出错或超时。"""


@dataclass
class Reply:
    content: str
    tool_calls: list = field(default_factory=list)   # [(工具名, 参数 dict)]
    message: dict = field(default_factory=dict)       # 模型原样返回的 message，下一轮对话直接追加它
    seconds: float = 0.0


@dataclass
class JsonResult:
    ok: bool
    value: Any = None          # validate 的返回值（例如 pydantic 对象）；没有 validate 时是解析后的 JSON
    raw: str = ""              # 最后一次的原始输出，排查用
    error: str = ""            # 不 ok 时的原因
    attempts: int = 0
    seconds: float = 0.0


def _settings() -> tuple[str, str, float, str]:
    return (
        os.environ.get("LOCAL_LLM_URL", DEFAULT_URL).rstrip("/"),
        os.environ.get("LOCAL_LLM_MODEL", DEFAULT_MODEL),
        float(os.environ.get("LOCAL_LLM_TIMEOUT", DEFAULT_TIMEOUT)),
        os.environ.get("LOCAL_LLM_KEEP_ALIVE", DEFAULT_KEEP_ALIVE),
    )


def _request(path: str, body: Optional[dict], timeout: float) -> dict:
    url = _settings()[0] + path
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        raise LocalLLMError(f"Ollama 返回 HTTP {e.code}：{detail}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise LocalLLMError(f"连不上 Ollama（{url}）：{e}。先运行 scripts/local_model_up.sh") from e
    except json.JSONDecodeError as e:
        raise LocalLLMError(f"Ollama 返回的不是 JSON：{e}") from e


def _parse_tool_calls(message: dict) -> list:
    calls = []
    for tc in message.get("tool_calls") or []:
        fn = tc.get("function") or {}
        args = fn.get("arguments")
        if isinstance(args, str):  # 个别版本把参数返回成字符串
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {"_unparsed": args}
        calls.append((fn.get("name"), args if isinstance(args, dict) else {}))
    return calls


def chat(
    messages: list,
    *,
    tools: Optional[list] = None,
    json_format: Any = None,
    model: Optional[str] = None,
    timeout: Optional[float] = None,
    temperature: float = 0.0,
) -> Reply:
    """调一次模型。json_format 为 "json" 或一个 JSON Schema dict 时要求输出 JSON。出错抛 LocalLLMError。"""
    _, default_model, default_timeout, keep_alive = _settings()
    body = {
        "model": model or default_model,
        "messages": messages,
        "stream": False,
        "think": False,
        "keep_alive": keep_alive,
        "options": {"temperature": temperature},
    }
    if tools:
        body["tools"] = tools
    if json_format is not None:
        body["format"] = json_format
    start = time.monotonic()
    out = _request("/api/chat", body, timeout or default_timeout)
    message = out.get("message") or {}
    return Reply(
        content=message.get("content") or "",
        tool_calls=_parse_tool_calls(message),
        message=message,
        seconds=time.monotonic() - start,
    )


def tool_message(name: str, result: Any) -> dict:
    """把工具结果包成下一轮对话要追加的 tool 消息。result 不是字符串时转成 JSON。"""
    content = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
    return {"role": "tool", "tool_name": name, "content": content}


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    return text.strip()


def chat_json(
    messages: list,
    *,
    validate: Optional[Callable[[Any], Any]] = None,
    schema: Optional[dict] = None,
    retries: int = 1,
    model: Optional[str] = None,
    timeout: Optional[float] = None,
) -> JsonResult:
    """要求模型输出 JSON，解析并校验；不合法就把错误告诉模型重试，最多 retries 次。

    validate：接收解析后的对象，返回校验后的值；不合法时抛异常（pydantic 的 model_validate 就是这样）。
    schema：可选的 JSON Schema，交给 Ollama 约束输出；不给时用 format="json"。
    这个函数不抛异常：连不上、超时、输出不合法都返回 ok=False，调用方按“不确定”处理。
    """
    history = list(messages)
    total, raw, error = 0.0, "", ""
    for attempt in range(1, retries + 2):
        try:
            reply = chat(history, json_format=schema or "json", model=model, timeout=timeout)
        except LocalLLMError as e:
            return JsonResult(ok=False, error=str(e), attempts=attempt, seconds=total)
        total += reply.seconds
        raw = reply.content
        try:
            value = json.loads(_strip_fences(raw))
            if validate is not None:
                value = validate(value)
            return JsonResult(ok=True, value=value, raw=raw, attempts=attempt, seconds=total)
        except Exception as e:  # 解析或校验失败
            error = f"{type(e).__name__}: {str(e)[:200]}"
        history += [
            {"role": "assistant", "content": raw},
            {"role": "user", "content": f"上面的输出不合格（{error}）。请只输出一个符合要求的 JSON 对象，不要任何其他文字。"},
        ]
    return JsonResult(ok=False, raw=raw, error=error, attempts=retries + 1, seconds=total)


# ---------------------------------------------------------------- 命令行


def check() -> bool:
    url, model, _, _ = _settings()
    try:
        version = _request("/api/version", None, 5).get("version", "?")
        names = [m.get("name", "") for m in _request("/api/tags", None, 5).get("models", [])]
    except LocalLLMError as e:
        print(f"FAIL  {e}")
        return False
    ready = any(n == model or n.startswith(model + ":") for n in names)
    print(f"{'OK  ' if ready else 'FAIL'}  Ollama {version} at {url}；模型 {model} {'已就绪' if ready else '不存在'}")
    if not ready:
        print("      运行 scripts/local_model_up.sh 创建它")
    return ready


def warmup() -> bool:
    _, model, _, keep_alive = _settings()
    try:
        reply = chat([{"role": "user", "content": "只回答 OK"}], timeout=180)
    except LocalLLMError as e:
        print(f"FAIL  预热失败：{e}")
        return False
    print(f"OK    {model} 已载入内存，用时 {reply.seconds:.1f} 秒，保持 {keep_alive}")
    try:
        quick = chat([{"role": "user", "content": "只回答 OK"}])
        print(f"OK    预热后一次短请求 {quick.seconds:.1f} 秒")
    except LocalLLMError as e:
        print(f"FAIL  预热后的短请求失败：{e}")
        return False
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="MindVault 本地模型调用模块")
    parser.add_argument("--check", action="store_true", help="检查 Ollama 和模型")
    parser.add_argument("--warmup", action="store_true", help="载入模型并保持常驻")
    args = parser.parse_args()
    if not (args.check or args.warmup):
        parser.print_help()
        return
    ok = True
    if args.check:
        ok = check() and ok
    if args.warmup and ok:
        ok = warmup()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
