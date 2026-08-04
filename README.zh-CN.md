# 3dsmax-mcp

通过 [Model Context Protocol](https://modelcontextprotocol.io) 把 AI 智能体接入 **Autodesk 3ds Max**。

用中文描述你要做的事，智能体调用 **151 个专用 MCP 工具**（核心配置 87 个）直接操作场景——创建物体、
构建材质、驱动修改器与控制器、截取视口、检查插件。不是让 AI 盲写 MAXScript 再反复试错。

**当前版本：1.5.1** — 见 [CHANGELOG.md](docs/CHANGELOG.md)。

> English: [README.md](README.md)

## 特点

- **原生桥接（Native Bridge）** — C++ 插件，支持 3ds Max 2023–2027，无需 MAXScript 轮询
- **运行时自省** — 可发现任意 Max 类、插件接口与参数，方便自动化与二次开发
- **深度插件支持** — tyFlow、Data Channel、MCG、OSL、Forest Pack、RailClone、Octane
- **内置智能体技能包** — 附带 MAXScript 参考文档，便于你编写自己的工具

## 环境要求

- Windows
- [Python 3.12+](https://www.python.org/)
- Autodesk **3ds Max 2023–2027**
- [uv](https://docs.astral.sh/uv/)（仅源代码安装或开发时需要）

---

## 安装

### 1. 从 PyPI 安装（推荐，国内加速）

无需克隆 GitHub 仓库。使用清华 TUNA 镜像安装完整软件包：

```powershell
python -m pip install 3dsmax-mcp -i https://pypi.tuna.tsinghua.edu.cn/simple
3dsmax-mcp-install
```

如果 PowerShell 找不到 `3dsmax-mcp-install`，可直接运行：

```powershell
python -m maxmcp.installer
```

安装程序会部署原生桥接插件、写入配置、构建技能包，并尽可能自动注册到已安装的 AI 客户端。
**必须重启 3ds Max** 插件才会加载。

> 其他可用镜像：阿里云 `https://mirrors.aliyun.com/pypi/simple/`、腾讯云 `https://mirrors.cloud.tencent.com/pypi/simple/`。

### 2. 从源代码安装（开发者）

先通过国内镜像安装 uv：

```powershell
python -m pip install uv -i https://pypi.tuna.tsinghua.edu.cn/simple
```

然后克隆仓库并安装：

```powershell
git clone https://github.com/cl0nazepamm/3dsmax-mcp.git
cd 3dsmax-mcp
uv sync
uv run python install.py
```

依赖下载慢或超时，先设置国内镜像再执行 `uv sync`：

```powershell
$env:UV_DEFAULT_INDEX = "https://pypi.tuna.tsinghua.edu.cn/simple"
uv sync
```

> 较早版本的 uv 使用 `UV_INDEX_URL` 环境变量，若上面这个不生效请改用它。

### 更新

PyPI 安装：

```powershell
python -m pip install --upgrade 3dsmax-mcp -i https://pypi.tuna.tsinghua.edu.cn/simple
3dsmax-mcp-install
```

源代码安装：

```powershell
git pull
uv sync
uv run python install.py
```

---

## 配置 AI 客户端

国内用户最常见的组合是 **Cline + DeepSeek**（VS Code 插件），下面以它为主。

### Cline + DeepSeek（推荐）

1. 在 VS Code 中安装 **Cline** 扩展
2. 在 Cline 设置里选择 API Provider 为 **DeepSeek**，填入 [DeepSeek 开放平台](https://platform.deepseek.com/)
   的 API Key，模型选择支持函数调用的对话模型（如 `deepseek-chat`）
3. 打开 Cline 的 **MCP Servers → Configure MCP Servers**，编辑配置文件：

```
%APPDATA%\Code\User\globalStorage\saoudrizwan.claude-dev\settings\cline_mcp_settings.json
```

如果使用上面的 PyPI 安装，先查询 Python 的绝对路径：

```powershell
python -c "import sys; print(sys.executable)"
```

填入（把 `command` 换成上一步返回的实际路径）：

```json
{
  "mcpServers": {
    "3dsmax-mcp": {
      "command": "C:/Users/你的用户名/AppData/Local/Programs/Python/Python312/python.exe",
      "args": ["-m", "maxmcp.server"],
      "disabled": false,
      "autoApprove": []
    }
  }
}
```

如果使用源代码安装，也可以继续使用：

```json
{
  "mcpServers": {
    "3dsmax-mcp": {
      "command": "uv",
      "args": ["run", "--directory", "C:/path/to/3dsmax-mcp", "3dsmax-mcp"],
      "disabled": false,
      "autoApprove": []
    }
  }
}
```

保存后 Cline 会自动重连。3ds Max 处于打开状态时，让智能体调用 `get_bridge_status`，
返回正常即表示打通。

> **推理模型注意**：`deepseek-reasoner` 一类纯推理模型对工具调用的支持与对话模型不同，
> 接 MCP 建议优先使用对话模型。

### 通义千问 Qwen / 智谱 GLM

这两家都提供 OpenAI 兼容接口，在 Cline 里选择 **OpenAI Compatible** provider，填入对应
Base URL、API Key 和模型名：

| 平台 | Base URL |
|------|----------|
| 通义千问（阿里云百炼） | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| 智谱 GLM | `https://open.bigmodel.cn/api/paas/v4/` |

模型名请以各平台文档为准，务必选择**支持函数调用（Function Calling）**的型号，否则无法调用 MCP 工具。

### Claude Desktop / Cursor（可选）

配置文件位置：

| 客户端 | 路径 |
|--------|------|
| Claude Desktop | `%APPDATA%\Claude\claude_desktop_config.json` |
| Cursor | `%USERPROFILE%\.cursor\mcp.json` |

服务器条目与上面的 `mcpServers` 写法相同。更多手动配置见
[docs/ADVANCED.md](docs/ADVANCED.md)。

---

## 快速上手一：建筑可视化

打开 3ds Max，在 AI 客户端里直接用中文提要求。典型流程：

**1. 批量导入与场景速览**

> 把 D:\assets\furniture 里的模型批量导入，然后给我一个场景概览。

`smart_import` 批量导入并自动匹配 PBR 材质，`query_scene` 给出场景结构概览。

**2. 构建材质**

> 用 D:\textures\wood_oak 里的贴图做一套木地板材质，赋给地面。

`create_material_from_textures` 从贴图文件夹自动搭好一整套连线完整的 PBR 材质节点，
再由 `assign_material` 赋给物体——不用一个个槽位手动接。

**3. 检查材质网络**

> 检查一下这个材质的节点连接有没有问题。

`inspect_material_network` 返回语义化的材质节点图：已连接的槽位、贴图清单、以及常见错误的
健康检查。材质"看起来不对"但不知道问题在哪时，先用它。

**4. 复用与批量替换**

> 把这个材质的结构复制到墙面物体上，贴图换成 concrete 那一套。
> 然后把场景里所有旧材质统一替换成新做的这个。

`replicate_material` 做保结构的材质克隆并重映射贴图路径，`batch_replace_materials`
批量替换所有引用——比在材质编辑器里逐个改快得多。

**5. 预览与渲染**

> 截个视口图看看，没问题的话渲染一张 1920×1080 的图。

`capture_viewport` 先出快速预览（智能体能"看到"结果并据此调整），确认后 `render_scene` 正式渲染。

> **渲染器说明**：国内建筑可视化多用 V-Ray / Corona。当前对 Octane 的材质连线支持最完整，
> V-Ray / Corona 的深度支持正在推进中——如果你在用，欢迎提 issue 告诉我们你的具体需求。

---

## 快速上手二：MMD / 动画

**1. 导入模型并理清结构**

> 把 D:\models\ 里的模型批量导入，然后告诉我场景里的骨骼层级是什么样的。

`smart_import` 批量导入并自动匹配 PBR 材质，`get_hierarchy` 输出父子层级树。

**2. 检查朝向与轴心**

> 检查一下头部骨骼的轴心和坐标轴朝向对不对。

`analyze_node_orientation` 返回轴心、包围盒、局部坐标轴和世界矩阵——绑定和摆放出问题时先看这个。

**3. 动画与控制器**

> 给这个骨骼加一个注视约束，目标是摄像机。
> 把第 0 帧到第 60 帧的循环接顺，首尾姿势对齐。

`assign_controller` + `add_controller_target` 建立约束，`keyframe_tracks` 处理关键帧、
姿势匹配、循环闭合与切线设置。

**4. 预览**

> 截个视口图看看动作效果。

`capture_viewport` 出图，智能体可以据此判断并继续调整。

---

## 工具配置（Tool Profile）

服务器默认加载 **full** 配置（全部工具）。若客户端上下文吃紧、或模型能力较弱导致工具选择混乱，
可切换到 **core** 精简配置：

```powershell
$env:MCP_TOOL_PROFILE = "core"
```

| 配置 | 包含范围 |
|------|----------|
| **core** | 场景、物体、材质、修改器、控制器、视口、文件、插件、组织管理、学习 |
| **full** | core 全部，外加 tyFlow、MCG、Forest Pack、RailClone、Data Channel、特效、状态集、参数关联、**渲染**、户型平面、Max 内置聊天 |

> **注意**：`render_scene` 属于 **full** 专属模块，切到 core 之后无法直接出图。
> 需要渲染、或要用 tyFlow / MCG / Data Channel 的话请保持 full 配置。

---

## 常见问题

**智能体说连不上 / 工具报错**
让它调用 `get_bridge_status`。先确认 3ds Max 正在运行、且安装后已经**重启过**。

**支持哪些 Max 版本**
2023–2027。原生桥接插件为每个版本单独编译，安装脚本会自动匹配已安装的版本。

**安全模式**
`execute_maxscript` 默认受安全模式限制。配置文件位于
`%LOCALAPPDATA%\3dsmax-mcp\mcp_config.ini`，详见 [docs/ADVANCED.md](docs/ADVANCED.md)。

**能自己加工具吗**
可以。安装脚本会生成一个智能体技能包，内含 MAXScript 参考资料，专门用来指导 AI 写新工具。

---

## 反馈

**提 issue 用中文完全可以**，不必勉强写英文——中文 issue 一样会被认真处理。

- 问题反馈：https://github.com/cl0nazepamm/3dsmax-mcp/issues
- 更新日志：[docs/CHANGELOG.md](docs/CHANGELOG.md)
- 进阶配置：[docs/ADVANCED.md](docs/ADVANCED.md)

如果这个工具对你有用，欢迎在 GitHub 点个 Star，也欢迎录制视频、写文章分享——
让更多中文用户看到。

## 许可证

[MIT](LICENSE)
