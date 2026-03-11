import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from chatmock.codex_manager import CodexAppServerPoolManager  # noqa: E402


class CodexManagerExternalTests(unittest.TestCase):
    def test_external_listening_instance_is_available(self):
        manager = CodexAppServerPoolManager("ws://127.0.0.1:8787")
        manager.status_all = lambda: [  # type: ignore[method-assign]
            {
                "label": "acc01",
                "status": "external",
                "listening": True,
            }
        ]
        manager._request_state = {"acc01": {"cooldown_until": 0.0}}
        manager._ordered_labels = lambda labels: labels  # type: ignore[method-assign]
        self.assertEqual(manager._available_labels(), ["acc01"])


if __name__ == "__main__":
    unittest.main()
