# 3dsmax-mcp

Connect AI agents to **Autodesk 3ds Max** through the [Model Context Protocol](https://modelcontextprotocol.io).

Ask in natural language; the agent creates objects, builds materials, drives modifiers and
controllers, captures viewports, and inspects plugins. By default it works through one tool,
`run_python`, writing Python scripts against an API with about 180 tools behind it, so the tool
list takes about 2k tokens of context. The `full`, `core` and `progressive` profiles list the
tools to the client directly instead.

- **Native bridge** — a C++ plugin for 3ds Max 2023–2027, no MAXScript polling
- **Agent viewport** — an independent floating view for navigation, visual targeting and captures
- **Editable modeling** — guarded component edits, named curves, sweeps and lofts with parameters saved in the scene
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
directory, asks which MCP tool profile to expose (`code` is the default), writes user config,
and registers the server with the AI clients it can find. `3dsmax-mcp-install --tool-profile full`
chooses one without the prompt. **Restart 3ds Max afterwards** so the plugin loads.

**Requirements:** Windows · Python 3.13+ · 3ds Max 2023–2027

Manual MCP client setup, tool profiles, safe mode and architecture notes are documented in
[docs/ADVANCED.md](https://github.com/cl0nazepamm/3dsmax-mcp/blob/master/docs/ADVANCED.md).

---

## 中文说明

**3dsmax-mcp** 通过 [Model Context Protocol](https://modelcontextprotocol.io) 把 AI 智能体接入
**Autodesk 3ds Max**。

用中文描述你要做的事，智能体直接操作场景——创建物体、构建材质、驱动修改器与控制器、
截取视口、检查插件。服务器默认只公开 `run_python` 一个操作工具，智能体编写 Python 脚本调用背后约 180 个工具，工具定义约占 2k token；
也可以选择 `full`、`core` 或 `progressive` 配置，把工具直接公开给客户端。

- **原生桥接**：3ds Max 2023–2027 的 C++ 插件，无需 MAXScript 轮询
- **智能体视口**：独立浮动视口，用于导航、视觉定位和截图
- **可编辑建模**：带校验的组件编辑、命名曲线、扫描和放样，参数保存在场景中
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

`3dsmax-mcp-install` 会让你选择 MCP 工具配置（默认 `code`），把原生桥接插件部署到 Autodesk 的
`ApplicationPlugins` 目录、写入用户配置，并尽可能自动注册到已安装的 AI 客户端。也可以用
`3dsmax-mcp-install --tool-profile full` 直接指定。**装完请重启 3ds Max**，插件才会加载。

**环境要求：** Windows · Python 3.13+ · 3ds Max 2023–2027

国内常用的 **Cline + DeepSeek** 配置方式、建筑可视化与 MMD 动画上手示例、以及其他国内镜像，
见中文文档
[README.zh-CN.md](https://github.com/cl0nazepamm/3dsmax-mcp/blob/master/README.zh-CN.md)。

提 issue 用中文完全可以。
