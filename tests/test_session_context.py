import json
import unittest
from unittest.mock import patch

from maxmcp.tools.session_context import get_session_context


class SessionContextTests(unittest.TestCase):
    def test_get_session_context_combines_live_summaries(self) -> None:
        with (
            patch("maxmcp.tools.bridge.get_bridge_status", return_value='{"connected": true}'),
            patch("maxmcp.tools.capabilities.get_plugin_capabilities", return_value='{"maxVersion": 2025}'),
            patch("maxmcp.tools.session_context.run_overview", return_value='{"objectCount": 4}'),
            patch("maxmcp.tools.session_context.run_selection", return_value='{"selected": 1, "objects": []}'),
            patch(
                "maxmcp.tools.session_context._unit_context",
                return_value={"systemType": "centimeters", "systemScale": 1.0},
            ),
        ):
            result = json.loads(get_session_context(max_roots=10, max_selection=5))

        self.assertEqual(
            result,
            {
                "bridge": {"connected": True},
                "capabilities": {"maxVersion": 2025},
                "scene": {"objectCount": 4},
                "selection": {"selected": 1, "objects": []},
                "units": {"systemType": "centimeters", "systemScale": 1.0},
            },
        )


if __name__ == "__main__":
    unittest.main()
