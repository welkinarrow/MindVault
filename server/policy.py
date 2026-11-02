"""权限模块：所有权限判断只在这里做。

全部是纯函数：不访问数据库、不调用 LLM、不读环境变量，便于穷举测试。
规则依据 docs/interfaces.md 第 2.5、4.2、5.3、9 节。
"""

from typing import Optional

LEVELS = (0, 1, 2, 3)
REVIEW_ACTIONS = ("pass", "quarantine", "reject")
MAX_REASON = 200

# 拒绝时告诉 agent 向谁申请（按 block 所属范围）
SCOPE_OWNERS = {
    "zh": {
        "public": "任意负责人",
        "finance": "财务负责人",
        "support": "客服负责人",
        "release": "发布负责人",
        "rnd": "研发负责人",
        "mgmt": "管理层负责人",
    },
    "en": {
        "public": "any owner",
        "finance": "the finance owner",
        "support": "the support owner",
        "release": "the release owner",
        "rnd": "the R&D owner",
        "mgmt": "the management owner",
    },
}


# ---------------------------------------------------------------- 读取


def can_read(agent: dict, block: dict) -> Optional[str]:
    """判断 agent 能否读取 block。

    返回 None 表示允许；否则返回拒绝原因代码。检查顺序：
    1. 已隔离 → NOT_FOUND（与“不存在”返回同一代码，不暴露隔离内容的存在）
    2. 级别不足 → LEVEL_TOO_LOW（与范围同时不满足时只报这一条）
    3. 范围不匹配 → SCOPE_MISMATCH
    """
    if block["status"] == "quarantined":
        return "NOT_FOUND"
    if block["level"] > agent["level"]:
        return "LEVEL_TOO_LOW"
    if block["scope"] not in agent["read_scopes"]:
        return "SCOPE_MISMATCH"
    return None


def denial(reason_code: str, block_id: str, block: Optional[dict] = None, lang: str = "zh") -> dict:
    """生成按 block_id 读取被拒时返回给 agent 的 JSON（interfaces.md 第 4.2 节）。

    NOT_FOUND 只返回三个字段，不透露任何关于 block 的信息。
    """
    result = {"denied": True, "reason_code": reason_code, "block_id": block_id}
    if reason_code == "NOT_FOUND" or block is None:
        return result

    owner = SCOPE_OWNERS[lang][block["scope"]]
    if reason_code == "LEVEL_TOO_LOW":
        result["required_level"] = block["level"]
        result["message"] = (
            f"需要 L{block['level']} 权限，可向{owner}申请。"
            if lang == "zh"
            else f"L{block['level']} access required. You can request it from {owner}."
        )
    elif reason_code == "SCOPE_MISMATCH":
        result["required_scope"] = block["scope"]
        result["message"] = (
            f"需要 {block['scope']} 范围的权限，可向{owner}申请。"
            if lang == "zh"
            else f"Access to the {block['scope']} scope is required. You can request it from {owner}."
        )
    return result


# ---------------------------------------------------------------- 写入


LABEL_MODES = ("taint", "self")


def label_for_write(
    agent: dict, session_max_level: int, mode: str = "taint", requested_level: object = None
) -> tuple[int, str]:
    """计算写入内容的级别和范围。

    mode="taint"（默认，系统本身）：级别 = 本会话成功读取过的最高级别（会话污点），
        requested_level 被忽略，agent 无法通过参数或内容影响结果。
    mode="self"（HW7 基线）：级别 = agent 自己声明的 requested_level；
        缺省或不是 0–3 的整数时按 0 处理。
    两种模式下范围都是 agent 的可写范围。
    """
    if type(session_max_level) is not int or session_max_level not in LEVELS:
        raise ValueError(f"session_max_level 必须是 0–3 的整数，收到 {session_max_level!r}")
    if mode not in LABEL_MODES:
        raise ValueError(f"mode 必须是 taint 或 self，收到 {mode!r}")
    if mode == "taint":
        return session_max_level, agent["write_scope"]
    # type() 而非 isinstance()：排除 True/False（bool 是 int 的子类）
    level = requested_level if type(requested_level) is int and requested_level in LEVELS else 0
    return level, agent["write_scope"]


# ---------------------------------------------------------------- 审核


def check_review_action(result: object, lang: str = "zh") -> tuple[str, str]:
    """校验审核函数的返回值，返回 (action, reason)。

    只接受 {"action": "pass" | "quarantine" | "reject", "reason": str}。
    任何不合规的返回都按 quarantine 处理（宁可误拦，不可放过）。
    """
    fallback = "审核结果无效，已自动隔离" if lang == "zh" else "Invalid review result; quarantined automatically"

    if not isinstance(result, dict):
        return "quarantine", f"{fallback}（返回值不是字典）" if lang == "zh" else f"{fallback} (not a dict)"

    action = result.get("action")
    if action not in REVIEW_ACTIONS:
        detail = f"（action={action!r}）" if lang == "zh" else f" (action={action!r})"
        return "quarantine", (fallback + detail)[:MAX_REASON]

    reason = result.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        reason = "未提供理由" if lang == "zh" else "No reason given"
    return action, reason.strip()[:MAX_REASON]
