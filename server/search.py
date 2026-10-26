"""检索模块：关键词分词、打分、按权限过滤后排序。

纯函数，不访问数据库。规则依据 docs/interfaces.md 第 4.2 节（v0.3 起）。
memory_read 按问题检索时用 limit=5；memory_write 选审核上下文时用 limit=3。
"""

import re
from typing import NamedTuple

from server.policy import can_read

EN_STOPWORDS = frozenset(
    "the a an of in on for and or to is are was why what how did does".split()
)
# 先长后短，避免“为什么”被“什么”拆坏
ZH_STOPWORDS = ("为什么", "什么", "怎么", "如何", "是否", "哪些", "请问", "的", "了", "吗", "呢")

CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")
ALNUM = re.compile(r"[a-z0-9]+")


class SearchResult(NamedTuple):
    results: list[dict]               # 有权读的 block，已排序、已截断；每个附带 "_score"
    filtered: list[tuple[dict, str]]  # 命中关键词但被拒的 (block, reason_code)，用于日志
    tokens: frozenset[str]            # 分词结果，便于调试


def tokenize(query: str) -> frozenset[str]:
    """把查询拆成词。中文切二字组，英文切单词。"""
    text = query.lower()
    for word in ZH_STOPWORDS:
        text = text.replace(word, " ")

    tokens: set[str] = set()

    # 中文：每段连续汉字切成相邻二字组；单个汉字丢弃
    for run in CJK_RUN.findall(text):
        tokens.update(run[i : i + 2] for i in range(len(run) - 1))

    # 英文和数字：去停用词、去短词、去复数 s
    for word in ALNUM.findall(CJK_RUN.sub(" ", text)):
        if word in EN_STOPWORDS or len(word) < 2:
            continue
        if len(word) > 3 and word.endswith("s"):
            word = word[:-1]
        tokens.add(word)

    return frozenset(tokens)


def score(tokens: frozenset[str], block: dict) -> int:
    """命中的不同词数。英文词按词首前缀匹配，中文二字组按子串匹配。"""
    text = f"{block['title']} {block['content']}".lower()
    words = ALNUM.findall(text)
    hits = 0
    for token in tokens:
        if token.isascii():
            if any(w.startswith(token) for w in words):
                hits += 1
        elif token in text:
            hits += 1
    return hits


def search(query: str, blocks: list[dict], agent: dict, limit: int = 5) -> SearchResult:
    """在全部 block 中检索，只返回 agent 有权读的前 limit 条。

    排序：得分从高到低，得分相同按 block_id 升序。得分为 0 的不返回。
    命中关键词但无权读的 block 放进 filtered，不返回给 agent。
    """
    tokens = tokenize(query)
    if not tokens:
        return SearchResult([], [], tokens)

    allowed: list[tuple[int, str, dict]] = []
    filtered: list[tuple[dict, str]] = []
    for block in blocks:
        s = score(tokens, block)
        if s == 0:
            continue
        reason = can_read(agent, block)
        if reason is None:
            allowed.append((-s, block["block_id"], block))
        else:
            filtered.append((block, reason))

    allowed.sort(key=lambda x: (x[0], x[1]))
    results = [{**block, "_score": -neg} for neg, _, block in allowed[:limit]]
    filtered.sort(key=lambda x: x[0]["block_id"])
    return SearchResult(results, filtered, tokens)
