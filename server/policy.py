"""权限模块：所有权限判断只在这里做。

全部是纯函数：不访问数据库、不调用 LLM、不读环境变量，便于穷举测试。
规则依据 docs/interfaces.md 第 2.5、4.2、5.3、9 节，v2 的部分依据第 13 节。
"""

import re
from typing import Optional

LEVELS = (0, 1, 2, 3)
REVIEW_ACTIONS = ("pass", "quarantine", "reject")
MAX_REASON = 200

# v2（interfaces.md 第 13.1 节）。缺省值保证旧数据、旧密钥的行为和 v0.9 完全一样。
EXPOSURES = ("external", "internal")          # 顺序即敏感程度：internal 高于 external
MODEL_LOCATIONS = ("local", "cloud")
ROLES = ("business", "manager", "admin")
HIDDEN_STATUSES = ("quarantined", "pending")  # 任何 agent 都读不到，按 NOT_FOUND 处理
MAX_CITATIONS = 10
MIN_SNIPPET = 4                               # 降级证据片段的最短长度（去掉空白后），防止用“的”“是”凑数


def exposure_of(block: dict) -> str:
    """block 的内外部。没写这个字段的（旧数据、种子）按 external。"""
    return block.get("exposure") or "external"


def location_of(agent: dict) -> str:
    """agent 登记的模型位置。没写的（旧密钥）按 cloud。"""
    return agent.get("model_location") or "cloud"


def role_of(agent: dict) -> str:
    """agent 的角色。没写的按 business。"""
    return agent.get("role") or "business"

# 拒绝时告诉 agent 向谁申请（按 block 所属范围）
SCOPE_OWNERS = {
    "zh": {
        "public": "任意负责人",
        "finance": "财务负责人",
        "hr": "人力资源负责人",
        "support": "客服负责人",
        "release": "发布负责人",
        "rnd": "研发负责人",
        "mgmt": "管理层负责人",
        "sales": "销售负责人",
    },
    "en": {
        "public": "any owner",
        "finance": "the finance owner",
        "hr": "the HR owner",
        "support": "the support owner",
        "release": "the release owner",
        "rnd": "the R&D owner",
        "mgmt": "the management owner",
        "sales": "the sales owner",
    },
}


# ---------------------------------------------------------------- 读取


def can_read(agent: dict, block: dict) -> Optional[str]:
    """判断 agent 能否读取 block。

    返回 None 表示允许；否则返回拒绝原因代码。检查顺序：
    1. 已隔离或待人工审批 → NOT_FOUND（与“不存在”返回同一代码，不暴露它的存在）
    2. 级别不足 → LEVEL_TOO_LOW（与范围同时不满足时只报这一条）
    3. 范围不匹配 → SCOPE_MISMATCH
    4. 内部 block 且 agent 用云端模型 → EXPOSURE_INTERNAL（v2，放在最后，旧的拒绝码不受影响）
    """
    if block["status"] in HIDDEN_STATUSES:
        return "NOT_FOUND"
    if block["level"] > agent["level"]:
        return "LEVEL_TOO_LOW"
    if block["scope"] not in agent["read_scopes"]:
        return "SCOPE_MISMATCH"
    if exposure_of(block) == "internal" and location_of(agent) != "local":
        return "EXPOSURE_INTERNAL"
    return None


def can_correct(agent: dict, block: dict) -> Optional[str]:
    """An agent may correct a block it can read that belongs to its own department (its write scope).
    Returns None if allowed, else a reason code."""
    code = can_read(agent, block)
    if code is not None:
        return code
    if block["scope"] != agent["write_scope"]:
        return "SCOPE_MISMATCH"
    return None


def denial(reason_code: str, block_id: str, block: Optional[dict] = None, lang: str = "zh") -> dict:
    """生成按 block_id 读取被拒时返回给 agent 的 JSON（interfaces.md 第 4.2 节）。

    NOT_FOUND 只返回三个字段，不透露任何关于 block 的信息。
    """
    result = {"denied": True, "reason_code": reason_code, "block_id": block_id}
    if reason_code == "NOT_FOUND" or block is None:
        return result

    scope = block["scope"]
    owner = SCOPE_OWNERS[lang].get(scope) or (f"{scope}负责人" if lang == "zh" else f"the {scope} owner")
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
    elif reason_code == "EXPOSURE_INTERNAL":
        result["message"] = (
            "这条记忆标为内部，只能由使用本地模型的 agent 读取。"
            if lang == "zh"
            else "This memory is internal and can only be read by agents on a local model."
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


# ---------------------------------------------------------------- v2：写入的默认标签（interfaces.md 13.4）


def default_label(
    agent: dict, taint_level: int, internal_read: bool, mode: str = "taint", requested_level: object = None
) -> dict:
    """写入的默认标签：级别和范围沿用 label_for_write，内外部看污点里是否读过 internal。

    internal_read 来自 agent_taint 表（跨会话保存）。读过任何 internal，写出的就是 internal；
    cloud agent 读不到 internal，所以它的写入一定是 external。
    """
    if type(internal_read) is not bool:
        raise ValueError(f"internal_read 必须是 True 或 False，收到 {internal_read!r}")
    level, scope = label_for_write(agent, taint_level, mode, requested_level)
    return {"level": level, "scope": scope, "exposure": "internal" if internal_read else "external"}


def label_at_most(label: dict, ceiling: dict) -> bool:
    """label 是否不比 ceiling 更敏感：级别不更高、内外部不更高、范围相同。"""
    return (
        label["scope"] == ceiling["scope"]
        and label["level"] <= ceiling["level"]
        and EXPOSURES.index(label["exposure"]) <= EXPOSURES.index(ceiling["exposure"])
    )


# ---------------------------------------------------------------- v2：管理接口（interfaces.md 13.7）


def can_manage(agent: dict) -> Optional[str]:
    """manager_fetch、manager_suggest 只对登记为 local 的 manager 密钥开放。

    不是 local → NOT_LOCAL_MODEL；是 local 但角色不是 manager → NOT_MANAGER。
    """
    if location_of(agent) != "local":
        return "NOT_LOCAL_MODEL"
    if role_of(agent) != "manager":
        return "NOT_MANAGER"
    return None


# ---------------------------------------------------------------- v2：引用（interfaces.md 13.6）


def check_citations(
    agent: dict, cited: object, session_reads: set, blocks: dict
) -> tuple[Optional[str], list]:
    """校验一组引用。返回 (None, []) 表示通过；否则返回 (原因代码, 出问题的 block_id 列表)。

    cited：按重要性排好序的 block_id 列表。
    session_reads：本会话成功读过的 block_id 集合。
    blocks：block_id → block，至少包含 cited 里的全部编号（找不到的视为不存在）。

    检查顺序：格式 → 数量 → 重复 → 本会话读过 → 现在仍然有权读。任何一项不通过，整组拒绝。
    最后一项返回 can_read 的原因代码（例如 block 读过之后被隔离，返回 NOT_FOUND）。
    """
    if (not isinstance(cited, list) or not cited
            or not all(isinstance(b, str) and b.strip() for b in cited)):
        return "INVALID_INPUT", []
    if len(cited) > MAX_CITATIONS:
        return "TOO_MANY_CITATIONS", []
    seen, dupes = set(), []
    for block_id in cited:
        if block_id in seen and block_id not in dupes:
            dupes.append(block_id)
        seen.add(block_id)
    if dupes:
        return "DUPLICATE_CITATION", dupes
    unread = [b for b in cited if b not in session_reads]
    if unread:
        return "NOT_READ_IN_SESSION", unread
    for block_id in cited:
        block = blocks.get(block_id)
        code = "NOT_FOUND" if block is None else can_read(agent, block)
        if code is not None:
            return code, [block_id]
    return None, []


# ---------------------------------------------------------------- v2：降级核对（interfaces.md 13.7）

_SENTENCE_END = re.compile(r"(?<=[。！？!?；;])|\n|(?<=\.)\s+")
_TRAILING_PUNCT = "。！？!?；;，,.、 "

DOWNGRADE_TEXT = {
    "zh": {
        "format": "降级建议格式不对",
        "above_default": "建议标签不能高于默认标签",
        "no_evidence": "第 {n} 句没有证据：{sentence}",
        "not_read": "{block_id} 不在写入者读过的记录里",
        "short_snippet": "{block_id} 的片段太短（至少 {min} 个字符）",
        "snippet_missing": "{block_id} 的原文里找不到这个片段：{snippet}",
        "label_mismatch": "建议标签 {suggested} 与按来源算出的 {computed} 不一致",
    },
    "en": {
        "format": "The downgrade suggestion is malformed",
        "above_default": "The suggested label cannot be above the default label",
        "no_evidence": "Sentence {n} has no evidence: {sentence}",
        "not_read": "{block_id} is not among the blocks the writer read",
        "short_snippet": "The snippet for {block_id} is too short (at least {min} characters)",
        "snippet_missing": "This snippet is not in the text of {block_id}: {snippet}",
        "label_mismatch": "The suggested label {suggested} does not match {computed}, computed from the sources",
    },
}


def _normalize(text: str) -> str:
    """去掉多余空白：连续空白合成一个空格，首尾去掉。"""
    return " ".join(text.split())


def _squeeze(text: str) -> str:
    """去掉全部空白。中文换行处会被插进空格，所以片段比对时不看空白。"""
    return "".join(text.split())


def _sentence_key(text: str) -> str:
    """比较句子时用：去多余空白和句末标点。"""
    return _normalize(text).rstrip(_TRAILING_PUNCT)


def split_sentences(content: str) -> list[str]:
    """按中英文句末标点和换行切句，去掉空句。"""
    parts = (_sentence_key(p) for p in _SENTENCE_END.split(content))
    return [p for p in parts if p]


def _fmt_label(label: dict) -> str:
    return f"L{label['level']}/{label['scope']}/{label['exposure']}"


def _valid_label(label: object) -> bool:
    return (
        isinstance(label, dict)
        and type(label.get("level")) is int and label["level"] in LEVELS
        and isinstance(label.get("scope"), str) and bool(label["scope"])
        and label.get("exposure") in EXPOSURES
    )


def check_downgrade(
    default: dict, suggestion: object, content: str, writer_reads: dict, lang: str = "zh"
) -> tuple[str, str, Optional[dict]]:
    """核对记忆管理 agent 的降级建议。返回 (结果, 理由, 算出的标签)。

    default：默认标签 {"level", "scope", "exposure"}。
    suggestion：manager_suggest 的 downgrade 参数，None 表示没提降级。
    content：待审写入的正文。
    writer_reads：block_id → block，写入时记下的来源（只有这些可以当证据）。

    结果是 "none"（没提降级）、"passed" 或 "void"（作废）。检查顺序：
    1. 格式；2. 建议标签不高于默认标签；3. 正文每一句都有证据；
    4. 证据引用的 block 都在 writer_reads 里；5. 片段去掉全部空白后，是该 block 标题或正文（同样去掉空白）的一部分；
    6. 用所引 block 算出标签（级别取最高，有一条 internal 就是 internal，范围同默认标签），
       必须等于建议标签。
    标签由代码算出，不采信模型的说法；只有 passed 才返回算出的标签。
    """
    t = DOWNGRADE_TEXT[lang]
    if suggestion is None:
        return "none", "", None

    # 1. 格式
    if not isinstance(suggestion, dict) or not _valid_label(suggestion.get("label")):
        return "void", t["format"], None
    evidence = suggestion.get("evidence")
    if not isinstance(evidence, list) or not evidence or not all(
        isinstance(e, dict) and all(isinstance(e.get(k), str) and e[k].strip()
                                    for k in ("sentence", "block_id", "snippet"))
        for e in evidence
    ):
        return "void", t["format"], None
    label = suggestion["label"]

    # 2. 不高于默认标签（范围也必须相同）
    if not label_at_most(label, default):
        return "void", t["above_default"], None

    # 3. 每一句都有证据
    covered = {_sentence_key(e["sentence"]) for e in evidence}
    for n, sentence in enumerate(split_sentences(content), start=1):
        if sentence not in covered:
            return "void", t["no_evidence"].format(n=n, sentence=sentence[:60]), None

    # 4–5. 证据来自写入者读过的 block，片段确实在原文里
    cited = []
    for e in evidence:
        block_id = e["block_id"].strip()
        block = writer_reads.get(block_id)
        if block is None:
            return "void", t["not_read"].format(block_id=block_id), None
        snippet = _squeeze(e["snippet"])
        if len(snippet) < MIN_SNIPPET:
            return "void", t["short_snippet"].format(block_id=block_id, min=MIN_SNIPPET), None
        if snippet not in _squeeze(f"{block.get('title', '')}\n{block.get('content', '')}"):
            return "void", t["snippet_missing"].format(block_id=block_id, snippet=_normalize(e["snippet"])[:60]), None
        cited.append(block)

    # 6. 用所引 block 算标签，必须和建议一致
    computed = {
        "level": max(b["level"] for b in cited),
        "scope": default["scope"],
        "exposure": "internal" if any(exposure_of(b) == "internal" for b in cited) else "external",
    }
    if computed != {k: label[k] for k in ("level", "scope", "exposure")}:
        return "void", t["label_mismatch"].format(
            suggested=_fmt_label(label), computed=_fmt_label(computed)), None
    if not label_at_most(computed, default):  # 理论上不会发生：来源都在污点里
        return "void", t["above_default"], None
    return "passed", "", computed
