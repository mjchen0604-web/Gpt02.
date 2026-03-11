import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from chatmock.model_profiles import prompt_family_for_model, is_public_chatmock_model  # noqa: E402
from chatmock.upstream import _resolve_upstream_mode  # noqa: E402


class AlignmentTests(unittest.TestCase):
    def test_prompt_family_selection(self):
        self.assertEqual(prompt_family_for_model("gpt-5.4"), "hybrid")
        self.assertEqual(prompt_family_for_model("gpt-5.4-fast-xhigh"), "hybrid")
        self.assertEqual(prompt_family_for_model("gpt-5.3-codex-low"), "codex")
        self.assertEqual(prompt_family_for_model("codex-mini"), "codex")
        self.assertEqual(prompt_family_for_model("gpt-5.2"), "base")

    def test_public_models_prefer_codex_app_server(self):
        self.assertTrue(is_public_chatmock_model("gpt-5.4-fast-low"))
        self.assertTrue(is_public_chatmock_model("gpt-5.2"))
        self.assertTrue(is_public_chatmock_model("gpt-5.1-codex-max-high"))
        self.assertEqual(_resolve_upstream_mode("auto", "gpt-5.4"), "codex-app-server")
        self.assertEqual(_resolve_upstream_mode("auto", "gpt-5.2"), "codex-app-server")
        self.assertEqual(_resolve_upstream_mode("auto", "codex-mini"), "codex-app-server")
        self.assertEqual(_resolve_upstream_mode("auto", "unknown-model"), "codex-app-server")


if __name__ == "__main__":
    unittest.main()
