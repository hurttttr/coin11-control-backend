<div align="center">

# ⛓️ Coin11 Control Backend

**多设备安卓自动化任务控制平台**

[![Version](https://img.shields.io/badge/版本-v0.3.0-00f0ff?style=flat-square)](https://github.com/hurttttr/coin11-control-backend/releases)
[![Python](https://img.shields.io/badge/Python-3.12+-3776AB?style=flat-square&logo=python&logoColor=white)]()
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?style=flat-square&logo=fastapi&logoColor=white)]()
[![Vue 3](https://img.shields.io/badge/Frontend-Vue_3-4FC08D?style=flat-square&logo=vue.js&logoColor=white)]()
[![License](https://img.shields.io/badge/许可证-MIT-green?style=flat-square)]()

> 基于 [coin11-tb](https://github.com/czl0325/coin11-tb) 二次开发 · 前端仓库： [coin11-control-frontend](https://github.com/hurttttr/coin11-control-frontend)

</div>

---

## ✨ 功能特性

<table>
<tr>
<td width="50%">

**📱 设备管理**
- 自动发现 USB / Wi-Fi 设备
- ADB 无线调试配对（`adb pair`）
- 远程连接 / 断开
- 实时状态监视

**📋 任务队列**
- 每台设备独立 FIFO 队列
- 拖拽重排、清空已完成
- 批量分配 / 启动 / 暂停
- 运行中任务保护

</td>
<td width="50%">

**🤖 自动执行**
- 设备连接自动运行任务 ⭐
- 多脚本多选配置
- WebSocket 实时日志推送
- WebSocket 截图流（2 FPS）
- 任务超时保护（默认 30 分钟）

**🐳 DevOps**
- Docker 多阶段构建
- GitHub Actions 测试 + 自动打包
- 一键启动脚本（`.bat` / `.ps1`）
- Git 版本自动更新

**🔒 安全**
- 可选 API Token 鉴权
- WebSocket 连接鉴权
- CORS 白名单校验

</td>
</tr>
</table>

---

## 🚀 快速开始

### 环境要求

| 依赖 | 说明 |
|------|------|
| **Python** ≥ 3.12 | 推荐使用 [uv](https://docs.astral.sh/uv/) |
| **ADB** | Android Debug Bridge，需在 PATH 或 `.env` 配置 |
| **Git** | coin11-tb 仓库自动拉取 |
| **Node.js** ≥ 20（可选） | 仅本地开发前端时需要 |

### ADB 安装

<details>
<summary>点击展开安装指南</summary>

**Windows**
```bash
# 1. 下载 Platform Tools 并解压
# 2. 将路径加入系统 PATH 或 .env 配置
#    ADB_PATH=D:\platform-tools\adb.exe
# 3. 验证
adb version
```

**macOS**
```bash
brew install android-platform-tools
```

**Linux**
```bash
sudo apt install android-tools-adb
```
</details>

### 安装 & 启动

```bash
# 克隆（含前端子模块）
git clone --recursive https://github.com/hurttttr/coin11-control-backend.git
cd coin11-control-backend

# 安装依赖
uv venv && uv sync

# 启动（开发模式）
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

访问 **http://127.0.0.1:8000/docs** 查看 Swagger 文档。

> 💡 Windows 用户可直接双击 `start-coin11.bat` 一键启动后端 + 前端。

---

## 🐳 Docker 部署

项目使用**多阶段构建**，前端编译后与后端打包成单一镜像。

```bash
# 本地构建
docker compose build

# 或从 GitHub Container Registry 拉取
docker pull ghcr.io/hurttttr/coin11-control-backend:master
docker run -d --name coin11-control -p 8000:8000 ghcr.io/hurttttr/coin11-control-backend:master
```

访问 **http://localhost:8000**（远程服务器替换为对应 IP）。

> ⚠️ Docker 不支持 USB 设备透传，请使用 Wi-Fi ADB（`adb connect IP:5555`）

镜像内已安装 coin11-tb 脚本的运行时依赖（`uiautomator2` / `opencv` / `easyocr` / `ddddocr` /
CPU 版 `torch`），因此体积较大（约 2GB）。若只需要 API 与设备管理、不在容器内执行脚本，
可构建精简镜像：

```bash
docker build --build-arg WITH_SCRIPT_DEPS=0 -t coin11-control:slim .
```

`docker compose` 已配置命名卷 `coin11-data`，持久化 coin11-tb 克隆与自动任务配置，
重建容器不会丢失。

---

## 📡 API 文档

所有端点均以 `/api` 为前缀。完整文档见 Swagger UI：`http://127.0.0.1:8000/docs`

> 🔑 若配置了 `API_AUTH_TOKEN`，除 `/api/health` 外所有端点均需携带
> `Authorization: Bearer <token>` 或 `X-API-Token: <token>` 请求头。

### 设备管理

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/devices` | 获取设备列表 |
| `POST` | `/api/devices/connect` | 远程连接（`{"address":"IP:Port"}`） |
| `POST` | `/api/devices/pair` | ADB 无线配对（`{"address":"IP:Port","code":"123456"}`） |
| `DELETE` | `/api/devices/{serial}` | 断开设备 |

### 任务队列

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | …​/queue | 获取队列 |
| `POST` | …​/queue | 添加任务 |
| `DELETE` | …​/queue/{task_id} | 移除任务 |
| `POST` | …​/queue/start | 启动执行 |
| `POST` | …​/queue/stop | 停止执行 |

### 批量操作

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/tasks/batch-enqueue` | 批量分配任务 |
| `POST` | `/api/tasks/batch-start` | 批量启动队列 |
| `POST` | `/api/tasks/batch-stop` | 批量暂停队列 |

### 自动任务设置

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/settings/auto-tasks` | 获取自动任务脚本列表 |
| `PUT` | `/api/settings/auto-tasks` | 设置自动任务脚本列表 |

### 其他

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/health` | 健康检查 |
| `GET` | `/api/scripts` | 可用脚本列表 |
| `GET` | `/api/update/check` | 检查更新 |
| `POST` | `/api/update/pull` | 拉取更新 |

---

## 📦 版本发布说明

### v0.3.0 (2026-08-25)

> 🛠️ 稳定性与安全加固：修复任务停止、孤儿进程、批量截图错发等核心缺陷；测试从 1 个增至 46 个

**🐛 关键修复：**
- ⏹️ **停止队列真正生效** — 此前取消信号被吞掉，点「停止」后队列仍会继续启动下一个脚本；现在取消会正确终止整个队列
- 💀 **不再遗留孤儿进程** — 此前仅取消了 asyncio 包装任务，`subprocess` 启动的脚本仍在真机上继续操作 App；现在按进程树终止（Windows `taskkill /F /T`、POSIX `killpg`）
- 📸 **批量启动的截图不再错发** — 闭包晚绑定使所有设备的画面都推给最后一台设备，改用 `functools.partial` 绑定
- 🔁 **设备重连后自动任务可再次触发** — 去重集合此前只增不减，掉线重连必须重启后端；现在按连续缺席轮次清理（含 ADB 抖动宽限）
- 🔗 **SPA 深链接刷新不再 404** — 直接访问或刷新 `/tasks`、`/settings`、`/device/xxx` 现在正常返回页面；未命中的 `/api`、`/ws` 路径仍保持 404 JSON
- 🐳 **Docker 镜像可真正执行任务** — 此前未安装 coin11-tb 脚本依赖，容器内所有脚本在 import 阶段即失败
- ⏱️ **任务超时保护** — 挂死的脚本不再永久阻塞设备队列（默认 30 分钟，超时后继续执行下一个任务）
- 🧠 **日志内存上限** — 任务日志改为 `deque(maxlen=2000)`，长跑任务不再无限增长
- 📡 **广播不再被慢客户端阻塞** — WebSocket 推送改为并发发送
- 🔍 **修正设备解析** — 无详细信息的在线设备此前会被正则静默丢弃

**🔒 安全增强：**
- 🔑 **可选 API 鉴权** — 设置 `API_AUTH_TOKEN` 后所有 `/api/*` 需携带 `Authorization: Bearer` 或 `X-API-Token`；`/api/health` 始终豁免。**未设置时行为完全不变**（向后兼容本地单用户场景）
- 🌐 **CORS 合法性** — `CORS_ORIGINS` 含 `"*"` 时自动关闭 `allow_credentials`（该组合被浏览器禁止），并收敛 methods/headers
- 🛡️ **WebSocket 鉴权收紧** — 移除端点上的弱默认 token，缺失 token 即拒绝（close code 4001）
- ⚠️ **暴露风险告警** — 非回环监听且未设置 `API_AUTH_TOKEN` 时启动告警

**✨ 工程质量：**
- 🧪 测试从 **1 → 46 个**，新增 7 个测试文件，覆盖任务引擎、设备解析、自动任务、鉴权、SPA 托管
- 🤖 新增 CI 工作流（`ci.yml`）：后端 pytest + 前端 vitest + ruff/mypy 基线；发布镜像前先过测试闸门
- 📋 统一日志体系（`app/core/logging_config.py`），替换散落各处的 `print`
- 🗑️ 清理死代码：删除无任何引用的 `app/models/`、`app/schemas/task.py`、`task_state.json`
- 🔀 抽出 `git_ops.py` 消除 `repo_manager` / `version_manager` 的重复 git 逻辑，并支持 `master` / `main` 分支自动探测
- 📦 依赖收敛：移除 5 个未使用依赖，拆分 `requirements-dev.txt`（测试依赖不再进入生产镜像）
- 🐋 Docker：非 root 运行、HEALTHCHECK、数据卷持久化、移除危险的 `CORS_ORIGINS=["*"]` 默认值
- 📄 新增 `.env.example` 配置模板

**⚠️ 已知问题（需前端仓库配合修复）：**
- 前端硬编码了 WS token，若 `.env` 中 `WS_AUTH_TOKEN` 被改为其它值，实时画面与日志会**静默失效**（详见「配置说明」）
- 前端将截图与日志混存于同一 2000 条缓冲区，2 FPS 截图约 16 分钟后会把日志挤出
- 前端 WebSocket 重连 5 次耗尽后不会自愈，需刷新页面

### v0.2.1 (2026-08-14)

> 🐛 修复：设备自动任务不再依赖打开网页，后端启动即自动工作

**修复：**
- 🤖 **后台设备监视** — 新增 `AutoTaskWatcher` 后台循环，后端启动后每 5 秒自动扫描 ADB 设备，新设备上线自动入队并启动已配置的自动任务，**无需打开网页 / 前端轮询**
- 🧪 新增无头启动回归测试（`tests/integration/test_headless_auto_task.py`），防止自动任务重新退回"必须开网页才执行"

**增强：**
- 🗂️ 自动任务触发逻辑抽离为独立服务 `app/services/auto_task_runner.py`，HTTP 触发与后台触发共用同一去重逻辑

### v0.2.0 (2026-07-23)

> 🎉 添加设备连接自动运行任务 + ADB 无线配对

**新功能：**
- 🤖 **设备连接自动运行任务** — 设置页面配置脚本列表，设备上线时自动入队并启动
- 📟 **ADB 无线配对** — 支持 Android 11+ `adb pair`，仪表盘新增配对弹窗
- ⊞ **批量任务操作** — 批量分配、批量启动、批量暂停
- ⚙️ **设置页面** — 侧边栏新增设置页，支持多选自动运行脚本

**增强：**
- 🐳 Docker 多阶段构建，支持 GitHub Actions 自动推送
- 🏃 一键启动脚本（`start-coin11.bat` / `start-coin11.ps1`）
- 📝 前端子模块化，`git clone --recursive` 一次拉取全部代码

### v0.1.0 (2026-07-21)

> 🎬 初始版本

- 基础设备管理（列表/连接/断开/详情）
- 任务队列编排（入队/出队/拖拽重排/启动/停止）
- WebSocket 实时日志 & 截图流
- coin11-tb 仓库自动拉取与版本更新
- 前端 Vue 3 + Pinia + Vite

---

## 🏗️ 项目结构

```
coin11-control-backend/
├── app/
│   ├── main.py                 # FastAPI 入口 + WebSocket + SPA 托管 + 鉴权
│   ├── core/
│   │   ├── config.py           # 配置（pydantic-settings）
│   │   ├── constants.py
│   │   └── logging_config.py   # 统一日志配置
│   ├── api/v1/
│   │   ├── router.py           # 路由聚合 + 批量/设置端点
│   │   ├── devices.py          # 设备管理（GET 保持幂等）
│   │   ├── tasks.py            # 任务队列端点
│   │   └── update.py           # 更新检查/拉取
│   ├── services/
│   │   ├── device_manager.py   # ADB 设备发现（含短 TTL 缓存）
│   │   ├── task_engine.py      # 任务队列引擎（进程树终止 + 超时）
│   │   ├── queue_control.py    # 队列启动共享助手
│   │   ├── auto_task_runner.py # 自动任务触发 + 后台设备监视
│   │   ├── screen_capture.py   # 截图流服务
│   │   ├── websocket_manager.py# WS 连接池（并发广播）
│   │   ├── auto_task_settings.py
│   │   ├── git_ops.py          # git 操作共享层
│   │   ├── repo_manager.py
│   │   └── version_manager.py
│   └── schemas/device.py       # Pydantic 模型
├── coin11_tb/                  # coin11-tb 脚本仓库（运行时自动 clone）
├── frontend/                   # 前端子模块
├── tests/
│   ├── unit/                   # 单元测试（7 个文件）
│   └── integration/
├── .github/workflows/
│   ├── ci.yml                  # 测试 + lint
│   └── docker-build.yml        # 镜像构建与推送
├── .env.example
├── requirements.txt            # 运行时依赖
├── requirements-dev.txt        # 测试/lint 依赖
├── Dockerfile
├── docker-compose.yml
└── start-coin11.bat
```

---

## ⚙️ 配置说明

复制 `.env.example` 为 `.env` 后按需修改：

```ini
HOST=127.0.0.1
PORT=8000
ADB_PATH=adb
CORS_ORIGINS=["http://localhost:5173","http://127.0.0.1:5173"]
COIN11_TB_REPO_URL=https://github.com/czl0325/coin11-tb.git

# WebSocket 鉴权令牌 —— ⚠️ 见下方警告，勿随意修改
WS_AUTH_TOKEN=coin11-control-token

# API 鉴权令牌（可选）：留空则 /api 完全开放（本地单用户场景）
# 设置后所有 /api/* 需携带 Authorization: Bearer <token> 或 X-API-Token
# /api/health 始终豁免，供健康检查使用
API_AUTH_TOKEN=

# 日志级别：DEBUG / INFO / WARNING / ERROR
LOG_LEVEL=INFO
```

### ⚠️ 关于 `WS_AUTH_TOKEN`

**当前前端（submodule）硬编码了默认令牌 `coin11-control-token`。**
如果把 `.env` 里的 `WS_AUTH_TOKEN` 改成其它值，后端会拒绝前端的 WebSocket 连接
（close code 4001），表现为 **实时设备画面一直「等待画面传输」、日志不刷新** —— 而且
页面上没有任何错误提示，只在浏览器 console 里有一行日志。

因此：
- **本地使用（`HOST=127.0.0.1`，仅回环监听）**：保持默认值即可
- **对外暴露**：必须同时修改前端的令牌，否则实时功能不可用。根治方式是让前端改为
  构建期注入（`VITE_WS_TOKEN`）——该项待前端仓库配合，见上方「已知问题」

### 🔒 对外暴露时的建议

本平台可完整控制已连接的安卓设备（执行脚本、读取屏幕），请勿在无鉴权状态下暴露到公网：

| 项目 | 建议 |
|------|------|
| `HOST` | 尽量保持 `127.0.0.1`；需远程访问时置于反向代理之后 |
| `API_AUTH_TOKEN` | 非回环监听时**必须**设置为强随机值 |
| `WS_AUTH_TOKEN` | 改为强随机值（需同步前端） |
| `CORS_ORIGINS` | 明确列出来源，不要用 `"*"` |

---

## 🤝 贡献

1. Fork 本仓库
2. 创建特性分支（`git checkout -b feat/xxx`）
3. 提交改动（`git commit -m "feat: xxx"`）
4. 推送到分支（`git push origin feat/xxx`）
5. 提交 Pull Request

---

## 📄 许可证

本项目基于 **MIT** 许可证开源。
