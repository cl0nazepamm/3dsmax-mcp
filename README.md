# 3dsmax-mcp

<p align="left">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="./docs/images/logo-white.png">
    <img src="./docs/images/logo.png" alt="3dsmax-mcp logo" width="600">
  </picture>
</p>

Connect AI agents to Autodesk 3ds Max through the [Model Context Protocol](https://modelcontextprotocol.io).

This is a preview release of 3dsmax-mcp 2.0.0
Mainly the new `Code Mode` profile. 

It has a single tool. `run_python` wraps all the existing tools from 1.7.8 to be used by AI agent. 


**Current release: 2.0.0 Preview** — see [CHANGELOG.md](docs/CHANGELOG.md).

## Features

- **Python API** — the agent writes Python against the `mx` API instead of picking from a long tool list. All the tools are behind this.
- **Native Bridge** — 2023-2027 versions.
- **Introspection** — discover arbitrary Max classes for all kinds of automation and scripting purposes. 

## Quick start

1. Run `3dsmax-mcp-2.0.0-Preview`, select your clients and a tool profile.
2. Open 3ds Max and restart your AI client.

The installer includes Python and it's dependencies.

**Tool profile.** That default is the **Code Mode** profile. The installer also offers the profiles
of 1.x with heavily trimmed tool schemas

**How it works.** The agent writes Python scripts against the `mx` API. `mx.tools.<name>(...)`
calls any tool in the catalog below and `mx.help("<name>")` prints its manual. A script that
runs inside 3ds Max is one undo step, rolled back if it fails.

Call these from a `run_python` script as `mx.tools.<name>

# This is a highly experimental release. 


- **[LICENSE](LICENSE)**
