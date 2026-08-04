import os
import sys
import unittest

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class ServerModuleLaunchTests(unittest.IsolatedAsyncioTestCase):
    async def _list_tool_names(self, args: list[str], profile: str = "core") -> list[str]:
        params = StdioServerParameters(
            command=sys.executable,
            args=args,
            env={**os.environ, "MCP_TOOL_PROFILE": profile},
        )
        async with stdio_client(params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                tools = (await session.list_tools()).tools
                return [tool.name for tool in tools]

    async def test_module_launch_registers_tools(self) -> None:
        tool_names = await self._list_tool_names(["-m", "maxmcp.server"])

        self.assertIn("execute_maxscript", tool_names)
        self.assertIn("query_scene", tool_names)
        self.assertIn("get_material_library", tool_names)
        self.assertIn("backup_material_library", tool_names)
        self.assertNotIn("mcg_create_graph", tool_names)
        self.assertNotIn("builder_session", tool_names)
        self.assertNotIn("builder_gate", tool_names)
        self.assertGreater(len(tool_names), 0)

    async def test_full_profile_registers_agentic_mcg_tools(self) -> None:
        tool_names = await self._list_tool_names(["-m", "maxmcp.server"], profile="full")

        self.assertIn("mcg_get_context", tool_names)
        self.assertIn("mcg_search_operators", tool_names)
        self.assertIn("mcg_create_graph", tool_names)
        self.assertIn("mcg_apply_patch", tool_names)
        self.assertIn("mcg_compile_graph", tool_names)
        self.assertNotIn("builder_session", tool_names)
        self.assertNotIn("builder_gate", tool_names)


if __name__ == "__main__":
    unittest.main()
