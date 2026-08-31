# 3dsmax-mcp

Connect AI agents to **Autodesk 3ds Max** through the [Model Context Protocol](https://modelcontextprotocol.io).

Ask in natural language; the agent creates objects, builds materials, drives modifiers and
controllers, captures viewports, and inspects plugins through a broad dedicated MCP catalog.
The optional progressive profile advertises three discovery/dispatch tools and lazy-loads
exact operational schemas on demand, reducing context use for local or smaller models.

- **Native bridge** — a C++ plugin for 3ds Max 2023–2027, no MAXScript polling
- **Introspection** — discover arbitrary Max classes, plugin surfaces, and parameters at runtime
- **Deep plugin support** — tyFlow, Data Channel, MCG, OSL, Forest Pack, RailClone, Octane
- **Bundled agent skill** — MAXScript reference shipped for writing your own tools

MIT licensed. Source and full documentation:
**https://github.com/cl0nazepamm/3dsmax-mcp**

## Install

```powershell
pip install 3dsmax-mcp
3dsmax-mcp-install
```

`3dsmax-mcp-install` deploys the native bridge plugin into Autodesk's `ApplicationPlugins`
directory, asks which MCP tool profile to expose (`full` is the compatibility default), writes
user config, and registers the server with the AI clients it can find. Local or smaller models
can save context with `3dsmax-mcp-install --tool-profile progressive` for an unattended install.
**Restart 3ds Max afterwards** so the plugin loads.

**Requirements:** Windows · Python 3.12+ · 3ds Max 2023–2027

Manual MCP client setup, tool profiles, safe mode and architecture notes are documented in
[docs/ADVANCED.md](https://github.com/cl0nazepamm/3dsmax-mcp/blob/master/docs/ADVANCED.md).

---

## 中文说明

**3dsmax-mcp** 通过 [Model Context Protocol](https://modelcontextprotocol.io) 把 AI 智能体接入
**Autodesk 3ds Max**。

用中文描述你要做的事，智能体通过专用 MCP 工具直接操作场景——创建物体、构建材质、驱动修改器与控制器、
截取视口、检查插件。可选的 progressive 配置只公开三个发现/调用工具，按需加载精确工具参数，可减少本地或较小模型的上下文占用。

- **原生桥接**：3ds Max 2023–2027 的 C++ 插件，无需 MAXScript 轮询
- **运行时自省**：可发现任意 Max 类、插件接口与参数
- **深度插件支持**：tyFlow、Data Channel、MCG、OSL、Forest Pack、RailClone、Octane
- **内置智能体技能包**：附带 MAXScript 参考文档，方便你编写自己的工具

MIT 开源协议。源码与完整文档：
**https://github.com/cl0nazepamm/3dsmax-mcp**

### 安装

国内建议使用清华 TUNA 镜像，速度更快：

```powershell
pip install 3dsmax-mcp -i https://pypi.tuna.tsinghua.edu.cn/simple
3dsmax-mcp-install
```

`3dsmax-mcp-install` 会让你选择 MCP 工具配置（默认使用兼容性最好的 `full`），把原生桥接插件部署到 Autodesk 的
`ApplicationPlugins` 目录、写入用户配置，并尽可能自动注册到已安装的 AI 客户端。本地或较小模型可用
`3dsmax-mcp-install --tool-profile progressive`。**装完请重启 3ds Max**，插件才会加载。

**环境要求：** Windows · Python 3.12+ · 3ds Max 2023–2027

国内常用的 **Cline + DeepSeek** 配置方式、建筑可视化与 MMD 动画上手示例、以及其他国内镜像，
见中文文档
[README.zh-CN.md](https://github.com/cl0nazepamm/3dsmax-mcp/blob/master/README.zh-CN.md)。

提 issue 用中文完全可以。
