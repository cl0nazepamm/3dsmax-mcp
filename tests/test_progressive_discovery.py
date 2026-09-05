import json
import os
import sys
import unittest

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def _payload(result) -> dict:
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict) and "ok" in structured:
        return structured
    for block in getattr(result, "content", []):
        text = getattr(block, "text", None)
        if not isinstance(text, str):
            continue
        try:
            parsed = json.loads(text)
        except (TypeError, ValueError):
            continue
        if isinstance(parsed, dict) and "ok" in parsed:
            return parsed
    raise AssertionError(f"No ToolEnvelope in result: {result!r}")


class ProgressiveDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_progressive_profile_stays_compact_while_loading_toolsets(self) -> None:
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "maxmcp.server"],
            env={**os.environ, "MCP_TOOL_PROFILE": "progressive"},
        )
        async with stdio_client(params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()

                names = [tool.name for tool in (await session.list_tools()).tools]
                self.assertEqual(names, ["list_toolsets", "describe_toolset", "call_tool"])

                listed = _payload(await session.call_tool("list_toolsets", {}))
                self.assertTrue(listed["ok"])
                catalog = listed["result"]
                self.assertEqual(catalog["profile"], "progressive")
                self.assertGreater(catalog["tool_count"], 150)
                self.assertTrue(all(not item["loaded"] for item in catalog["toolsets"]))

                described = _payload(
                    await session.call_tool("describe_toolset", {"toolset": "scene"})
                )
                self.assertTrue(described["ok"])
                tools = {item["name"]: item for item in described["result"]["tools"]}
                self.assertIn("query_scene", tools)
                self.assertIn("scene_patch", tools)
                self.assertIn("action", tools["query_scene"]["input_schema"]["properties"])

                modeling = _payload(await session.call_tool("describe_toolset", {"toolset":"modeling"}))
                modeling_tools = {item["name"]:item for item in modeling["result"]["tools"]}
                self.assertTrue({"create_mesh","inspect_mesh","mesh_edit"}.issubset(modeling_tools))
                self.assertTrue({"curve_model","inspect_curve","edit_curve"}.issubset(modeling_tools))
                preview_curve = _payload(await session.call_tool("call_tool", {
                    "name":"curve_model", "arguments":{"action":"preview", "definition":{
                        "curves":{"outline":{"kind":"circle","radius":5}},
                        "output":{"kind":"curve","curve":"outline"}}}
                }))
                self.assertTrue(preview_curve["ok"], preview_curve)
                self.assertEqual(preview_curve["result"]["counts"]["knots"],4)
                self.assertIn("operations",modeling_tools["mesh_edit"]["input_schema"]["properties"])
                invalid_mesh = _payload(await session.call_tool("call_tool", {
                    "name":"create_mesh", "arguments":{"name":"Invalid", "vertices":[], "faces":[]}
                }))
                self.assertFalse(invalid_mesh["ok"])

                # Lazy imports populate only the private registry. The public MCP
                # surface remains the same three schemas after discovery.
                names_after = [tool.name for tool in (await session.list_tools()).tools]
                self.assertEqual(names_after, names)

                listed_after = _payload(await session.call_tool("list_toolsets", {}))
                scene = next(
                    item for item in listed_after["result"]["toolsets"] if item["name"] == "scene"
                )
                materials = next(
                    item
                    for item in listed_after["result"]["toolsets"]
                    if item["name"] == "materials"
                )
                self.assertTrue(scene["loaded"])
                self.assertEqual(scene["loaded_tool_count"], scene["tool_count"])
                self.assertEqual(materials["loaded_tool_count"], 0)

    async def test_call_tool_forwards_original_tool_envelope_and_guards_dispatch(self) -> None:
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "maxmcp.server"],
            env={**os.environ, "MCP_TOOL_PROFILE": "progressive"},
        )
        async with stdio_client(params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()

                # Empty MAXScript is rejected locally, so this proves real lazy
                # invocation and single ToolEnvelope forwarding without needing Max.
                forwarded = _payload(
                    await session.call_tool(
                        "call_tool",
                        {"name": "execute_maxscript", "arguments": {}},
                    )
                )
                self.assertFalse(forwarded["ok"])
                self.assertEqual(forwarded["error"]["code"], "BAD_PARAM")
                # FastMCP's wire-level structuredContent includes optional
                # ToolEnvelope fields as null in eager and progressive modes.
                self.assertIsNone(forwarded.get("result"))

                unknown = _payload(
                    await session.call_tool(
                        "call_tool",
                        {"name": "definitely_not_a_tool", "arguments": {}},
                    )
                )
                self.assertFalse(unknown["ok"])
                self.assertEqual(unknown["error"]["code"], "NOT_FOUND")

                recursive = _payload(
                    await session.call_tool(
                        "call_tool",
                        {"name": "call_tool", "arguments": {}},
                    )
                )
                self.assertFalse(recursive["ok"])
                self.assertEqual(recursive["error"]["code"], "BAD_PARAM")

                names = [tool.name for tool in (await session.list_tools()).tools]
                self.assertEqual(names, ["list_toolsets", "describe_toolset", "call_tool"])


if __name__ == "__main__":
    unittest.main()
