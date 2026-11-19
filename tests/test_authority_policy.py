from __future__ import annotations

import unittest

from savva.agent.orchestrator import SYSTEM_PROMPT


class AuthorityPolicyTests(unittest.TestCase):
    def test_current_authority_outranks_historical_handoffs(self) -> None:
        prompt = " ".join(SYSTEM_PROMPT.split())
        current_rules = prompt.index("current target-project operating rules")
        access_matrix = prompt.index("currently approved")
        profiles = prompt.index("approved connection profiles")
        wip_scope = prompt.index("the active WIP's explicitly approved scope")
        history = prompt.index("historical handoffs only as non-authoritative")
        self.assertLess(current_rules, access_matrix)
        self.assertLess(access_matrix, profiles)
        self.assertLess(profiles, wip_scope)
        self.assertLess(wip_scope, history)

    def test_missing_or_conflicting_access_authority_blocks_connection(self) -> None:
        self.assertIn("IDENTITY_AUTHORITY_MISSING", SYSTEM_PROMPT)
        self.assertIn("do not attempt access", SYSTEM_PROMPT)
        self.assertIn("Never infer, reconstruct, or copy a host", SYSTEM_PROMPT)


if __name__ == "__main__":
    unittest.main()
