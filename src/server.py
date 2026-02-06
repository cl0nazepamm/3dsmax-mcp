import logging
import os
from mcp.server.fastmcp import FastMCP
from .max_client import MaxClient

logging.basicConfig(level=logging.INFO, format="%(message)s")

mcp = FastMCP("3dsmax-mcp")
client = MaxClient()

# Import tool modules to trigger @mcp.tool() registration
from .tools import execute, scene, objects, materials, render, viewport, identify  # noqa: E402, F401

# Expose skill/knowledge files as MCP resources for Claude Desktop users
_SKILL_PATH = os.path.join(
    os.path.dirname(__file__), os.pardir, ".claude", "skills", "3dsmax-mcp-dev", "SKILL.md"
)
_SKILL_PATH = os.path.normpath(_SKILL_PATH)


@mcp.resource("resource://3dsmax-mcp/skill")
def get_skill() -> str:
    """3ds Max MCP development guide — MAXScript gotchas, conventions, and best practices."""
    if os.path.exists(_SKILL_PATH):
        with open(_SKILL_PATH, "r", encoding="utf-8") as f:
            return f.read()
    return "Skill file not found."


@mcp.prompt()
def max_assistant() -> str:
    """Load 3ds Max assistant context with all conventions and gotchas."""
    skill_content = get_skill()
    return (
        "You are a 3ds Max assistant connected via MCP. Follow these rules:\n\n"
        "- Always inspect objects before manipulating (showProperties, classOf, etc.)\n"
        "- Organize objects under Dummy hierarchies (sized to bbox, pivot at min Z)\n"
        "- Use holdMaxFile()/fetchMaxFile quiet:true for critical operations\n"
        "- Never enable spline viewport rendering unless asked\n"
        "- Use capture_viewport tool to see the viewport, capture_screen for UI panels\n\n"
        "Full reference:\n\n" + skill_content
    )


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
