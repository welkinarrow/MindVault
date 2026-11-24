import json
import unittest

from review.reviewer import _build_user_message


class TestReviewContext(unittest.TestCase):
    def test_reviewer_payload_preserves_service_evidence_roles(self):
        base = {"block_id": "b-101", "title": "Reconciliation", "content": "Final figure: 8%",
                "level": 2, "scope": "hr"}
        evidence = {**base, "evidence_role": "cited_current", "status": "unverified", "revision": 17}
        message = _build_user_message("Staffing note", "Corrected figure: 8%", [evidence, base])
        payload = json.loads(message.split("\n", 1)[1].rsplit("\n\n", 1)[0])

        self.assertEqual(payload["context_blocks"], [
            {**base, "evidence_role": "cited_current", "status": "unverified"}, base,
        ])
