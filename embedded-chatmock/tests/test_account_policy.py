import os
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from chatmock.upstream_errors import build_error_info  # noqa: E402
from chatmock.utils import (  # noqa: E402
    _AUTH_POOL_STATE,
    _apply_account_cooldown,
    handle_chatgpt_candidate_failure,
)


class AccountPolicyTests(unittest.TestCase):
    def setUp(self):
        _AUTH_POOL_STATE.clear()
        os.environ.pop("CHATGPT_LOCAL_AUTH_FILES", None)
        os.environ.pop("CHATMOCK_DASHBOARD_SETTINGS_PATH", None)

    def test_balance_failure_sets_five_hour_cooldown_without_deleting(self):
        candidate = {
            "label": "acc01/auth.json",
            "source_kind": "auth_file",
            "source_path": "C:/tmp/acc01/auth.json",
            "source_index": None,
        }
        classification = handle_chatgpt_candidate_failure(
            candidate,
            build_error_info(
                source="codex-app-server",
                phase="turn_start",
                raw_status=429,
                raw_code="insufficient_quota",
                raw_message="insufficient quota",
                raw_body={"error": {"message": "insufficient quota"}},
            ),
        )
        self.assertEqual(classification, "insufficient_balance")
        state = _AUTH_POOL_STATE["acc01/auth.json"]
        self.assertEqual(state["status"], "cooldown_insufficient_balance")
        self.assertGreaterEqual(state["cooldown_until"] - state["updated_at"], 5 * 60 * 60 - 5)
        filtered = _apply_account_cooldown([candidate])
        self.assertEqual(filtered, [])

    def test_invalid_failure_removes_auth_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            auth_path = Path(temp_dir) / "acc01" / "auth.json"
            auth_path.parent.mkdir(parents=True, exist_ok=True)
            auth_path.write_text("{}", encoding="utf-8")
            os.environ["CHATGPT_LOCAL_AUTH_FILES"] = str(auth_path)
            candidate = {
                "label": "acc01/auth.json",
                "source_kind": "auth_file",
                "source_path": str(auth_path),
                "source_index": None,
            }
            classification = handle_chatgpt_candidate_failure(
                candidate,
                build_error_info(
                    source="codex-app-server",
                    phase="thread_start",
                    raw_status=401,
                    raw_message="account revoked and deactivated",
                    raw_body={"error": {"message": "account revoked and deactivated"}},
                ),
            )
            self.assertEqual(classification, "account_invalid")
            self.assertFalse(auth_path.exists())


if __name__ == "__main__":
    unittest.main()
