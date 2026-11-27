"""policy.py v2 部分的单元测试（interfaces.md 第 13 节）。

运行（在仓库根目录）：
    python -m unittest tests.test_policy_v2 -v
"""

import itertools
import unittest

from server.policy import (
    can_correct,
    can_manage,
    can_read,
    can_use_tool,
    check_citations,
    check_downgrade,
    default_label,
    denial,
    label_at_most,
    split_sentences,
)


def agent(level=1, scopes=("public", "sales"), write_scope="sales", location=None, role=None):
    a = {"agent_id": "a", "level": level, "read_scopes": list(scopes), "write_scope": write_scope}
    if location is not None:
        a["model_location"] = location
    if role is not None:
        a["role"] = role
    return a


def block(block_id="b-1", level=1, scope="sales", exposure=None, status="verified", title="", content=""):
    b = {"block_id": block_id, "level": level, "scope": scope, "status": status,
         "title": title, "content": content}
    if exposure is not None:
        b["exposure"] = exposure
    return b


CLOUD = agent(location="cloud")
LOCAL = agent(location="local")


class TestCanReadExposure(unittest.TestCase):
    def test_matrix(self):
        """云端或本地 × 内部或外部 × 级别 L0–L3 × 范围对或不对，逐一核对原因码。"""
        for location, exposure, level, scope_ok in itertools.product(
            ("cloud", "local"), ("external", "internal"), range(4), (True, False)
        ):
            a = agent(level=1, location=location)
            b = block(level=level, scope="sales" if scope_ok else "rnd", exposure=exposure)
            if level > 1:
                expected = "LEVEL_TOO_LOW"
            elif not scope_ok:
                expected = "SCOPE_MISMATCH"
            elif exposure == "internal" and location == "cloud":
                expected = "EXPOSURE_INTERNAL"
            else:
                expected = None
            with self.subTest(location=location, exposure=exposure, level=level, scope_ok=scope_ok):
                self.assertEqual(can_read(a, b), expected)

    def test_level_too_low_wins_over_exposure(self):
        self.assertEqual(can_read(CLOUD, block(level=3, exposure="internal")), "LEVEL_TOO_LOW")

    def test_scope_mismatch_wins_over_exposure(self):
        self.assertEqual(can_read(CLOUD, block(scope="rnd", exposure="internal")), "SCOPE_MISMATCH")

    def test_defaults_keep_v09_behaviour(self):
        """没写 exposure 的 block 按 external，没写 model_location 的 agent 按 cloud。"""
        self.assertIsNone(can_read(agent(), block()))
        self.assertEqual(can_read(agent(), block(exposure="internal")), "EXPOSURE_INTERNAL")

    def test_pending_is_not_found_for_everyone(self):
        boss = agent(level=3, location="local")
        self.assertEqual(can_read(boss, block(status="pending")), "NOT_FOUND")
        self.assertEqual(can_read(boss, block(status="pending", exposure="internal")), "NOT_FOUND")

    def test_cloud_cannot_correct_internal(self):
        self.assertEqual(can_correct(CLOUD, block(exposure="internal")), "EXPOSURE_INTERNAL")
        self.assertIsNone(can_correct(LOCAL, block(exposure="internal")))

    def test_denial_message(self):
        for lang in ("zh", "en"):
            with self.subTest(lang=lang):
                d = denial("EXPOSURE_INTERNAL", "b-802", block(exposure="internal"), lang=lang)
                self.assertEqual(d["reason_code"], "EXPOSURE_INTERNAL")
                self.assertTrue(d["message"])
                self.assertNotIn("required_level", d)

    def test_denial_unknown_scope_has_owner_fallback(self):
        d = denial("SCOPE_MISMATCH", "b-601", block(scope="sales"), lang="zh")
        self.assertIn("sales", d["message"])


class TestDefaultLabel(unittest.TestCase):
    def test_external_when_no_internal_read(self):
        self.assertEqual(default_label(LOCAL, 1, False), {"level": 1, "scope": "sales", "exposure": "external"})

    def test_internal_after_internal_read(self):
        self.assertEqual(default_label(LOCAL, 1, True), {"level": 1, "scope": "sales", "exposure": "internal"})

    def test_level_and_scope_follow_label_for_write(self):
        self.assertEqual(default_label(LOCAL, 2, True)["level"], 2)
        self.assertEqual(default_label(LOCAL, 2, True, mode="self", requested_level=0)["level"], 0)

    def test_rejects_non_bool(self):
        with self.assertRaises(ValueError):
            default_label(LOCAL, 1, 1)

    def test_label_at_most(self):
        default = {"level": 1, "scope": "sales", "exposure": "internal"}
        self.assertTrue(label_at_most({"level": 0, "scope": "sales", "exposure": "external"}, default))
        self.assertTrue(label_at_most(default, default))
        self.assertFalse(label_at_most({"level": 2, "scope": "sales", "exposure": "external"}, default))
        self.assertFalse(label_at_most({"level": 1, "scope": "public", "exposure": "internal"}, default))
        external = {"level": 1, "scope": "sales", "exposure": "external"}
        self.assertFalse(label_at_most({"level": 1, "scope": "sales", "exposure": "internal"}, external))


class TestCanManage(unittest.TestCase):
    def test_local_manager_allowed(self):
        self.assertIsNone(can_manage(agent(location="local", role="manager")))

    def test_cloud_manager_refused(self):
        self.assertEqual(can_manage(agent(location="cloud", role="manager")), "NOT_LOCAL_MODEL")

    def test_old_key_refused(self):
        self.assertEqual(can_manage(agent()), "NOT_LOCAL_MODEL")

    def test_local_business_refused(self):
        self.assertEqual(can_manage(agent(location="local")), "NOT_MANAGER")
        self.assertEqual(can_manage(agent(location="local", role="business")), "NOT_MANAGER")


class TestCanUseTool(unittest.TestCase):
    BUSINESS = ("memory_read", "memory_write", "memory_correct", "memory_rate", "memory_cite", "memory_trace")
    MANAGER = ("manager_fetch", "manager_suggest")

    def test_business_agent(self):
        """业务 agent（含没写角色的旧密钥）能用全部业务工具，不能用管理工具。"""
        for a in (agent(), agent(location="local"), agent(location="local", role="business")):
            for tool in self.BUSINESS:
                with self.subTest(agent=a, tool=tool):
                    self.assertIsNone(can_use_tool(a, tool))
            for tool in self.MANAGER:
                with self.subTest(agent=a, tool=tool):
                    self.assertIn(can_use_tool(a, tool), ("NOT_LOCAL_MODEL", "NOT_MANAGER"))

    def test_manager_only_manager_tools(self):
        manager = agent(location="local", role="manager")
        for tool in self.MANAGER:
            self.assertIsNone(can_use_tool(manager, tool))
        for tool in self.BUSINESS:
            with self.subTest(tool=tool):
                self.assertEqual(can_use_tool(manager, tool), "ROLE_NOT_ALLOWED")

    def test_cloud_manager_gets_nothing(self):
        manager = agent(location="cloud", role="manager")
        for tool in self.MANAGER:
            self.assertEqual(can_use_tool(manager, tool), "NOT_LOCAL_MODEL")
        for tool in self.BUSINESS:
            self.assertEqual(can_use_tool(manager, tool), "ROLE_NOT_ALLOWED")

    def test_admin_and_unknown_role_get_nothing(self):
        for role in ("admin", "Business", "superuser"):
            a = agent(location="local", role=role)
            for tool in self.BUSINESS:
                with self.subTest(role=role, tool=tool):
                    self.assertEqual(can_use_tool(a, tool), "ROLE_NOT_ALLOWED")
            for tool in self.MANAGER:
                with self.subTest(role=role, tool=tool):
                    self.assertEqual(can_use_tool(a, tool), "NOT_MANAGER")

    def test_unknown_tool_raises(self):
        with self.assertRaises(ValueError):
            can_use_tool(agent(), "memory_delete")


class TestCheckCitations(unittest.TestCase):
    def setUp(self):
        ids = [f"b-{600 + i}" for i in range(12)]
        self.blocks = {i: block(i, exposure="internal") for i in ids}
        self.reads = set(ids)

    def check(self, cited, who=LOCAL, reads=None):
        return check_citations(who, cited, self.reads if reads is None else reads, self.blocks)

    def test_valid(self):
        self.assertEqual(self.check(["b-602", "b-603", "b-601"]), (None, []))

    def test_exactly_ten(self):
        self.assertEqual(self.check([f"b-{600 + i}" for i in range(10)]), (None, []))

    def test_eleven(self):
        self.assertEqual(self.check([f"b-{600 + i}" for i in range(11)])[0], "TOO_MANY_CITATIONS")

    def test_empty_or_bad_input(self):
        for bad in ([], None, "b-601", [""], [1], ["b-601", None]):
            with self.subTest(cited=bad):
                self.assertEqual(self.check(bad)[0], "INVALID_INPUT")

    def test_duplicate(self):
        self.assertEqual(self.check(["b-601", "b-602", "b-601"]), ("DUPLICATE_CITATION", ["b-601"]))

    def test_not_read(self):
        reads = {"b-601", "b-602"}
        self.assertEqual(self.check(["b-601", "b-605", "b-606"], reads=reads),
                         ("NOT_READ_IN_SESSION", ["b-605", "b-606"]))

    def test_read_but_now_hidden(self):
        self.blocks["b-602"]["status"] = "quarantined"
        self.assertEqual(self.check(["b-601", "b-602"]), ("NOT_FOUND", ["b-602"]))

    def test_missing_block(self):
        self.reads.add("b-999")
        self.assertEqual(self.check(["b-999"]), ("NOT_FOUND", ["b-999"]))

    def test_cloud_cannot_cite_internal(self):
        self.assertEqual(self.check(["b-601"], who=CLOUD), ("EXPOSURE_INTERNAL", ["b-601"]))


# 降级样本：写入者读过一条内部、一条外部，写出的内容只用到外部那条
PUBLIC_GUIDE = block("b-801", level=0, scope="public", exposure="external",
                     title="新员工入职指南", content="新员工第一周完成账号开通和安全培训。周五参加部门介绍会。")
DEPLOY_MANUAL = block("b-802", level=1, scope="rnd", exposure="internal",
                      title="生产环境部署手册", content="部署前在跳板机上执行 release.sh，并通知值班工程师。")
READS = {"b-801": PUBLIC_GUIDE, "b-802": DEPLOY_MANUAL}
DEFAULT = {"level": 1, "scope": "rnd", "exposure": "internal"}
CONTENT = "新员工第一周要开通账号并完成安全培训。周五有部门介绍会。"
GOOD = {
    "label": {"level": 0, "scope": "rnd", "exposure": "external"},
    "evidence": [
        {"sentence": "新员工第一周要开通账号并完成安全培训。", "block_id": "b-801",
         "snippet": "新员工第一周完成账号开通和安全培训"},
        {"sentence": "周五有部门介绍会", "block_id": "b-801", "snippet": "周五参加部门介绍会"},
    ],
}


def with_(**changes):
    s = {"label": dict(GOOD["label"]), "evidence": [dict(e) for e in GOOD["evidence"]]}
    for k, v in changes.items():
        s[k] = v
    return s


class TestCheckDowngrade(unittest.TestCase):
    def check(self, suggestion, content=CONTENT, default=DEFAULT, reads=READS):
        return check_downgrade(default, suggestion, content, reads)

    def test_no_suggestion(self):
        self.assertEqual(self.check(None), ("none", "", None))

    def test_valid_downgrade_passes(self):
        result, reason, computed = self.check(GOOD)
        self.assertEqual((result, reason), ("passed", ""))
        self.assertEqual(computed, {"level": 0, "scope": "rnd", "exposure": "external"})

    def test_whitespace_in_snippet_is_ignored(self):
        s = with_()
        s["evidence"][1]["snippet"] = "  周五参加\n部门介绍会 "
        self.assertEqual(self.check(s)[0], "passed")

    def test_snippet_not_in_source(self):
        s = with_()
        s["evidence"][1]["snippet"] = "周五全员去团建"
        result, reason, computed = self.check(s)
        self.assertEqual(result, "void")
        self.assertIn("b-801", reason)
        self.assertIsNone(computed)

    def test_snippet_too_short(self):
        s = with_()
        s["evidence"][1]["snippet"] = "周五"
        self.assertEqual(self.check(s)[0], "void")

    def test_sentence_without_evidence(self):
        s = with_(evidence=[GOOD["evidence"][0]])
        result, reason, _ = self.check(s)
        self.assertEqual(result, "void")
        self.assertIn("第 2 句", reason)

    def test_paraphrased_sentence_counts_as_missing(self):
        s = with_()
        s["evidence"][0]["sentence"] = "第一周开通账号、完成培训"
        self.assertEqual(self.check(s)[0], "void")

    def test_cited_block_not_read_by_writer(self):
        reads = {"b-802": DEPLOY_MANUAL}
        result, reason, _ = self.check(GOOD, reads=reads)
        self.assertEqual(result, "void")
        self.assertIn("b-801", reason)

    def test_label_below_cited_sources(self):
        """内容其实用到了内部手册，却建议降为外部：代码算出 internal，建议作废。"""
        content = "新员工第一周要开通账号并完成安全培训。部署前要在跳板机上执行 release.sh。"
        s = with_(evidence=[
            GOOD["evidence"][0],
            {"sentence": "部署前要在跳板机上执行 release.sh", "block_id": "b-802",
             "snippet": "部署前在跳板机上执行 release.sh"},
        ])
        result, reason, _ = self.check(s, content=content)
        self.assertEqual(result, "void")
        self.assertIn("L1/rnd/internal", reason)

    def test_label_above_default(self):
        s = with_(label={"level": 2, "scope": "rnd", "exposure": "external"})
        self.assertEqual(self.check(s)[0], "void")

    def test_scope_change_refused(self):
        s = with_(label={"level": 0, "scope": "public", "exposure": "external"})
        self.assertEqual(self.check(s)[0], "void")

    def test_model_cannot_choose_a_different_label(self):
        """来源都是 L0 外部，模型却建议 L1 外部：不等于算出的标签，作废。"""
        s = with_(label={"level": 1, "scope": "rnd", "exposure": "external"})
        self.assertEqual(self.check(s)[0], "void")

    def test_malformed(self):
        for bad in ({}, {"label": GOOD["label"]}, {"label": GOOD["label"], "evidence": []},
                    {"label": {"level": "0", "scope": "rnd", "exposure": "external"}, "evidence": GOOD["evidence"]},
                    {"label": {"level": 0, "scope": "rnd", "exposure": "public"}, "evidence": GOOD["evidence"]},
                    {"label": GOOD["label"], "evidence": [{"sentence": "x", "block_id": "b-801"}]},
                    "downgrade please"):
            with self.subTest(suggestion=bad):
                self.assertEqual(self.check(bad)[0], "void")

    def test_english_reason(self):
        s = with_(evidence=[GOOD["evidence"][0]])
        result, reason, _ = check_downgrade(DEFAULT, s, CONTENT, READS, lang="en")
        self.assertEqual(result, "void")
        self.assertIn("Sentence 2", reason)


class TestSplitSentences(unittest.TestCase):
    def test_chinese_and_english(self):
        self.assertEqual(split_sentences("客户 B 续约。采购量增加 35%！\nUse v2. Version 2.5 ships"),
                         ["客户 B 续约", "采购量增加 35%", "Use v2", "Version 2.5 ships"])

    def test_empty(self):
        self.assertEqual(split_sentences("  \n。 "), [])


if __name__ == "__main__":
    unittest.main()
