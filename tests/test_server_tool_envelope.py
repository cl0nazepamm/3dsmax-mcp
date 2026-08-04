import json
import unittest
from typing import Any

from maxmcp.server import mcp


def _unwrap_call_tool_result(meta: dict[str, Any], content: Any = None) -> dict[str, Any]:
    """Normalize FastMCP call_tool payload whether string- or dict-enveloped."""
    result = meta.get("result") if isinstance(meta, dict) else None
    if isinstance(result, dict) and "ok" in result:
        return result
    if isinstance(result, str):
        return json.loads(result)
    # Some FastMCP versions put structured content on the content list.
    if content is not None:
        for block in content if isinstance(content, list) else [content]:
            data = getattr(block, "data", None) or getattr(block, "text", None)
            if isinstance(data, dict) and "ok" in data:
                return data
            if isinstance(data, str):
                try:
                    parsed = json.loads(data)
                except (TypeError, ValueError):
                    continue
                if isinstance(parsed, dict) and "ok" in parsed:
                    return parsed
    raise AssertionError(f"Could not unwrap tool envelope from meta={meta!r} content={content!r}")


class ServerToolEnvelopeTests(unittest.IsolatedAsyncioTestCase):
    async def test_registered_tools_return_structured_envelopes(self) -> None:
        content, meta = await mcp.call_tool("execute_maxscript", {"code": ""})
        payload = _unwrap_call_tool_result(meta, content)

        self.assertEqual(payload["ok"], False)
        self.assertIn("error", payload)
        self.assertNotIn("elapsed_ms", payload)
        self.assertNotIn("result", payload)

    async def test_registered_tool_advertises_envelope_return(self) -> None:
        manager = getattr(mcp, "_tool_manager", None) or getattr(mcp, "tool_manager", None)
        self.assertIsNotNone(manager, "FastMCP tool manager not found — API changed?")

        tool = manager.get_tool("execute_maxscript")
        fn = getattr(tool, "fn", None) or getattr(tool, "handler", None)
        self.assertIsNotNone(fn, "Registered tool exposes no callable — API changed?")

        annotations = getattr(fn, "__annotations__", {})
        self.assertIn("return", annotations)
        self.assertEqual(annotations["return"].__name__, "ToolEnvelope")


if __name__ == "__main__":
    unittest.main()
