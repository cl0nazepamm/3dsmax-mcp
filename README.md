# 3dsmax-mcp

<p align="left">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="./docs/images/logo-white.png">
    <img src="./docs/images/logo.png" alt="3dsmax-mcp logo" width="600">
  </picture>
</p>

Connect AI agents to Autodesk 3ds Max through the [Model Context Protocol](https://modelcontextprotocol.io).

This is a preview release of 3dsmax-mcp 2.0.1
Mainly the new `Code Mode` profile. 

It has a single tool. `run_python` wraps all the existing tools from 1.7.8 to be used by AI agent. 


**Current release: 2.0.1 Preview** — see [CHANGELOG.md](docs/CHANGELOG.md).

## Features

- **Python API** — the agent writes Python against the `mx` API instead of picking from a long tool list. All the tools are behind this.
- **Native Bridge** — 2023-2027 versions.
- **Introspection** — discover arbitrary Max classes for all kinds of automation and scripting purposes. 

## Quick start

1. Run `3dsmax-mcp-2.0.1-Setup.exe`, select your clients and a tool profile.
2. Open 3ds Max and restart your AI client.

The installer includes Python and it's dependencies.

**Tool profile.** That default is the **Code Mode** profile. The installer also offers **Full**,
the 1.x profile that lists every tool, with heavily trimmed tool schemas.

**How it works.** The agent writes Python scripts against the `mx` API and runs them with
`run_python`. `mx.tools.<name>(...)` calls any tool, `mx.help()` lists them all and
`mx.help("<name>")` prints one tool's manual. A script that runs inside 3ds Max is one undo
step, rolled back if it fails.

# This is a highly experimental release. 


- **[LICENSE](LICENSE)**
