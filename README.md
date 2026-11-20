# 3dsmax-mcp

<p align="left">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="./docs/images/logo-white.png">
    <img src="./docs/images/logo.png" alt="3dsmax-mcp logo" width="600">
  </picture>
</p>

This is the unofficial 3ds Max MCP server and plugin. [Model Context Protocol](https://modelcontextprotocol.io).

**Current release: 2.0.1** — see [CHANGELOG.md](docs/CHANGELOG.md).

## Features

- **Python API** — the agent writes Python against the `mx` API instead of picking from a long tool list.
- **Native Bridge** — only 2023-2027 versions.
- **Introspection** — discover arbitrary Max classes for all kinds of automation and scripting purposes. 
- **Plugin support** It supports many 3rd party plugins including tyFlow.
- **Atomic undo:** Everything a script mutates (`mx.tools.*`, `sdk`, and `rt`/`pymxs`) is wrapped in a single undo step and rolled back if the script raises.
- **Live feedback:** `print()` output, `result`, viewport/render images (`mx.show`), and the tool-call trace come back in one reply.
- **Multi-instance aware:** Automatically targets your visible 3ds Max window; use `list_max_instances` and `select_max_instance` when running multiple sessions.

## Quick start

1. Run `3dsmax-mcp-2.0.1-Setup.exe`, select your clients and install.
2. Open 3ds Max and restart your AI client.

The installer includes Python and it's dependencies.

**Tool profile.** The default is **Code Mode** (~2k tokens of context instead of ~103k). The installer also offers **Full**, the 1.x profile that lists every tool directly (deprecated).

**Requirements:** Windows · 3ds Max 2023–2027 · Python 3.13+ (included in the Windows Setup installer)

---

## What agents can drive

| Area | Capabilities |
| --- | --- |
| **Scene & Hierarchy** | Microsecond `sdk` collections (`nodes`, `selection`, `layers`, `groups`, `selection_sets`), transforms, instancing, delta queries |
| **Modeling & QA** | Editable Poly `mesh_edit` with cage hash guards, parametric `curve_model` & `loft_mesh`, `boolean_operation`, `geometry_qa`, `contact_check` |
| **Materials & Shading** | Material graph inspection & rewiring, `OpenPBR` / `Physical` / renderer-native materials, procedural **OSL** shader authoring & compilation, texture-to-material builders |
| **Lighting & Rendering** | Unified `render_setup` across **V-Ray**, **Corona**, **Octane**, **FStorm**, **ART**, and **Scanline** (lights, sun/sky, cameras, quality presets, renders) |
| **Animation & Rigging** | `with sdk.animate():` / `with sdk.at(frame):`, track & controller resolver, `keyframe_tracks`, `wire_params`, multi-frame motion strips |
| **Agent Viewport** | Dedicated floating viewport for framing, shading toggles, visual targeting, and multi-angle captures without disturbing your working view |
| **Plugins & Simulation** | **tyFlow** (graphs, patches, C# `tyflow_script` compile/run loop, PhysX, particles), **Data Channel**, **MCG**, **Forest Pack**, **RailClone**, **Chaos Cosmos**, **Pulze** |

---

## Supported AI clients

Auto-configured by the Inno installer.

- **Claude** (classic & Microsoft Store)
- **Cursor**
- **Antigravity**
- **Codex**
- **OpenCode**

Manual MCP client setup, building native bridges from source, and architecture details live in [docs/ADVANCED.md](docs/ADVANCED.md).

---

## Documentation

- [MX SDK Reference](docs/mx-sdk-reference.md) — full `sdk` collection, node, material, modifier, and track API
- [Advanced Guide](docs/ADVANCED.md) — manual client setup, renderer presets, building from source, architecture
- [Changelog](docs/CHANGELOG.md) — version history
- [License (MIT)](LICENSE)
