import os
import sys
import tempfile
import unittest
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class ServerModuleLaunchTests(unittest.IsolatedAsyncioTestCase):
    async def _list_tool_names(
        self,
        args: list[str],
        profile: str | None = "core",
        extra_env: dict[str, str] | None = None,
    ) -> list[str]:
        env = {
            key: value
            for key, value in os.environ.items()
            if key not in {"MCP_TOOL_PROFILE", "THREEDSMAX_MCP_TOOL_PROFILE"}
        }
        if profile is not None:
            env["MCP_TOOL_PROFILE"] = profile
        if extra_env:
            env.update(extra_env)
        params = StdioServerParameters(
            command=sys.executable,
            args=args,
            env=env,
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
        self.assertIn("resolve_node_refs", tool_names)
        self.assertIn("scene_patch", tool_names)
        self.assertIn("scene_qa", tool_names)
        self.assertTrue({"create_mesh", "inspect_mesh", "mesh_edit", "pick_component",
                         "loft_mesh", "geometry_qa", "agent_viewport"}.issubset(tool_names))
        self.assertIn("get_material_library", tool_names)
        self.assertIn("backup_material_library", tool_names)
        self.assertNotIn("mcg_create_graph", tool_names)
        self.assertNotIn("builder_session", tool_names)
        self.assertNotIn("builder_gate", tool_names)
        self.assertTrue({"send_to_chat", "chat_status", "chat_reload", "chat_clear"}.isdisjoint(tool_names))
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
        self.assertTrue({"send_to_chat", "chat_status", "chat_reload", "chat_clear"}.isdisjoint(tool_names))

    async def test_installed_config_selects_progressive_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir) / "3dsmax-mcp"
            config_dir.mkdir()
            (config_dir / "mcp_config.ini").write_text(
                "[mcp]\ntool_profile = progressive\n"
                "[llm]\ntool_profile = full\n",
                encoding="utf-8",
            )

            tool_names = await self._list_tool_names(
                ["-m", "maxmcp.server"],
                profile=None,
                extra_env={"LOCALAPPDATA": temp_dir},
            )

        self.assertEqual(tool_names, ["list_toolsets", "describe_toolset", "call_tool"])

    async def test_environment_profile_overrides_installed_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir) / "3dsmax-mcp"
            config_dir.mkdir()
            (config_dir / "mcp_config.ini").write_text(
                "[mcp]\ntool_profile = progressive\n",
                encoding="utf-8",
            )

            tool_names = await self._list_tool_names(
                ["-m", "maxmcp.server"],
                profile="core",
                extra_env={"LOCALAPPDATA": temp_dir},
            )

        self.assertIn("query_scene", tool_names)
        self.assertNotIn("list_toolsets", tool_names)


if __name__ == "__main__":
    unittest.main()
