import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from chatmock.upstream_errors import (  # noqa: E402
    build_error_info,
    classify_error,
    normalized_error_payload,
    normalized_http_status,
)


class UpstreamErrorTests(unittest.TestCase):
    def test_insufficient_quota_maps_to_429(self):
        info = build_error_info(
            source="chatgpt-backend",
            phase="http",
            raw_status=429,
            raw_code="insufficient_quota",
            raw_message="You exceeded your current quota, please check your plan and billing details.",
            raw_body={"error": {"message": "You exceeded your current quota, please check your plan and billing details.", "code": "insufficient_quota"}},
        )
        self.assertEqual(classify_error(info), "insufficient_balance")
        self.assertEqual(normalized_http_status(info), 429)

    def test_invalid_account_maps_to_401(self):
        info = build_error_info(
            source="codex-app-server",
            phase="thread_start",
            raw_status=403,
            raw_message="Account revoked and deactivated",
            raw_body={"error": {"message": "Account revoked and deactivated"}},
        )
        self.assertEqual(classify_error(info), "account_invalid")
        self.assertEqual(normalized_http_status(info), 401)

    def test_402_quota_message_maps_to_429(self):
        info = build_error_info(
            source="codex-app-server",
            phase="turn_start",
            raw_status=402,
            raw_message="insufficient quota for this request",
            raw_body={"error": {"message": "insufficient quota for this request"}},
        )
        self.assertEqual(classify_error(info), "insufficient_balance")
        self.assertEqual(normalized_http_status(info), 429)

    def test_402_invalid_message_maps_to_401(self):
        info = build_error_info(
            source="codex-app-server",
            phase="turn_start",
            raw_status=402,
            raw_message="account unauthorized and revoked",
            raw_body={"error": {"message": "account unauthorized and revoked"}},
        )
        self.assertEqual(classify_error(info), "account_invalid")
        self.assertEqual(normalized_http_status(info), 401)

    def test_generic_400_maps_to_502(self):
        info = build_error_info(
            source="codex-app-server",
            phase="turn_start",
            raw_status=400,
            raw_message="Upstream error",
            raw_body={"error": {"message": "Upstream error"}},
        )
        self.assertEqual(classify_error(info), "generic_failure")
        self.assertEqual(normalized_http_status(info), 502)
        payload = normalized_error_payload(info)
        self.assertEqual(payload["raw_status"], 400)
        self.assertEqual(payload["code"], "generic_failure")


if __name__ == "__main__":
    unittest.main()
