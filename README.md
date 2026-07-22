# Coin11 Control Backend 🚀

**coin11-control-backend** 是一个基于 **FastAPI** 构建的 Web 后端服务，用于集中控制和监视多台安卓设备，自动执行 **淘宝**、**支付宝**、**闲鱼** 等平台的签到、任务脚本。

> 本项目基于 [coin11-tb](https://github.com/czl0325/coin11-tb) 二次开发，后者提供了淘宝/支付宝/闲鱼等平台的自动化脚本。  \
> 配套前端项目：[coin11-control-frontend](https://github.com/hurttttr/coin11-control-frontend)（作为 git 子模块包含在本仓库中）

---

## 目录

- [功能特性](#功能特性)
- [快速开始](#快速开始)
  - [环境要求](#环境要求)
  - [安装步骤](#安装步骤)
  - [启动服务](#启动服务)
- [Docker 部署](#docker-部署)
  - [GitHub Actions 自动构建](#github-actions-自动构建)
  - [本地构建](#本地构建)
  - [拉取运行](#拉取运行)
  - [ADB 设备连接](#adb-设备连接)
- [项目结构](#项目结构)
- [API 文档](#api-文档)
  - [设备管理](#设备管理)
  - [任务队列](#任务队列)
  - [脚本列表](#脚本列表)
  - [版本更新](#版本更新)
  - [健康检查](#健康检查)
- [WebSocket 实时通信](#websocket-实时通信)
  - [连接地址](#连接地址)
  - [消息类型](#消息类型)
  - [客户端控制指令](#客户端控制指令)
- [配置说明](#配置说明)
- [多设备工作流](#多设备工作流)
- [注意事项](#注意事项)
- [开发](#开发)

---

## 功能特性

- **多设备管理** — 自动发现 USB / Wi-Fi 连接的 ADB 设备，支持远程连接与断开
- **任务队列编排** — 为每台设备独立维护任务队列，支持入队、出队、重排序、启动/停止
- **实时日志推送** — 任务执行日志通过 WebSocket 实时推送到前端
- **屏幕截图流** — 通过 ADB screencap 持续采集设备截图，经 WebSocket 推送（base64 编码）
- **版本自动更新** — 内嵌 Git 更新检测与拉取，方便同步上游脚本仓库
- **脚本白名单** — 自动扫描 coin11-tb 仓库中的 Python 脚本，仅允许白名单内的脚本入队
- **安全设计** — 路径穿越防护、WebSocket Token 鉴权、运行中任务保护

---

## 快速开始

### 环境要求

| 依赖 | 说明 |
|------|------|
| **Python** ≥ 3.14 | 推荐使用 [uv](https://docs.astral.sh/uv/) 管理虚拟环境 |
| **ADB** (Android Debug Bridge) | 设备通信必需，需在 `PATH` 中或通过 `.env` 指定路径 |
| **Git** | 版本更新功能可选依赖 |
| **coin11-tb 项目** | 已嵌入 `app/coin11_tb/` 目录，或通过 `.env` 配置外部路径 |

### ADB 安装指南

ADB (Android Debug Bridge) 是设备通信的核心依赖，请根据您的操作系统安装：

**Windows：**
1. 下载 [Platform Tools](https://dl.google.com/android/repository/platform-tools-latest-windows.zip) 并解压到 `C:\path\to\platform-tools`其他位置自行替换下方路径
2. 将解压后的 `platform-tools` 目录添加到系统 PATH，或在 `.env` 中配置 `ADB_PATH=C:\path\to\platform-tools\adb.exe`
3. 打开 CMD 运行 `adb devices` 确认安装成功

**macOS：**
```bash
brew install android-platform-tools
```

**Linux：**
```bash
# Ubuntu/Debian
sudo apt install android-tools-adb
# 或手动下载
wget -q https://dl.google.com/android/repository/platform-tools-latest-linux.zip
unzip platform-tools-latest-linux.zip
sudo cp platform-tools/adb /usr/local/bin/
```

**验证安装：**
```bash
adb version
# 应输出类似：Android Debug Bridge version 1.0.41
```

### 安装步骤

```bash
# 1. 克隆项目
git clone https://github.com/your-org/coin11-control-backend.git
cd coin11-control-backend

# 2. 创建虚拟环境（使用 uv，推荐）
uv venv
source .venv/bin/activate  # Linux/macOS
.venv\Scripts\activate     # Windows

# 3. 安装依赖
uv sync                     # 使用 uv（推荐）
# 或
pip install -r requirements.txt
```

### 启动服务

```bash
# 开发模式（热重载）
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

# 或直接运行
python app/main.py
```

服务启动后，访问 **http://127.0.0.1:8000/docs** 即可看到交互式 API 文档（Swagger UI）。

> 项目提供了 `start-coin11.bat` 一键启动脚本（后端 + 前端），双击即可。

---

## Docker 部署

项目使用 **多阶段构建**，自动编译前端后与后端打包成单一镜像，支持 GitHub Actions 自动构建推送。

### 前提条件

- 安装 [Docker Desktop](https://www.docker.com/products/docker-desktop/)
- 设备通过 **Wi-Fi ADB** 连接（`adb connect IP:5555`），Docker 不支持 USB 设备透传

### 克隆项目

```bash
# 含前端子模块
git clone --recursive https://github.com/hurttttr/coin11-control-backend.git
cd coin11-control-backend
```

如果已经 clone 了但没拉子模块：

```bash
git submodule update --init --recursive
```

### 本地构建

```bash
docker compose build
```

或直接：

```bash
docker build -t coin11-control .
```

### 拉取运行（推荐）

每次 `git push` 后 GitHub Actions 自动构建镜像并推送到 GitHub Container Registry：

```bash
# 拉取最新镜像
docker pull ghcr.io/hurttttr/coin11-control-backend:master

# 启动
docker run -d \
  --name coin11-control \
  -p 8000:8000 \
  ghcr.io/hurttttr/coin11-control-backend:master
```

或使用 docker compose：

```bash
docker compose up -d
```

启动后访问 **http://localhost:8000** 即可使用（无需单独启动前端）。

> 如果部署在远程服务器上，将 `localhost` 替换为服务器 IP，例如 `http://192.168.1.100:8000`。

### ADB 设备连接

容器内通过 Wi-Fi ADB 连接设备：

```bash
# 进入容器
docker exec -it coin11-control sh

# 连接设备
adb connect 192.168.1.100:5555
```

也可在宿主机连好设备后转发。

### 查看构建状态

Push 后去 GitHub 仓库 → **Actions** tab 查看构建进度，绿色对号表示构建成功。

---

## 项目结构

```
coin11-control-backend/
├── app/
│   ├── main.py                     # FastAPI 应用入口，WebSocket 端点，CORS 配置
│   ├── api/
│   │   └── v1/
│   │       ├── router.py           # API 路由聚合（/api 前缀）
│   │       ├── devices.py          # 设备管理端点（列表/连接/断开/详情）
│   │       ├── tasks.py            # 任务队列端点（入队/出队/启动/停止）
│   │       └── update.py           # 版本更新端点（检查/拉取）
│   ├── services/
│   │   ├── device_manager.py       # ADB 设备发现与管理（adb devices -l）
│   │   ├── task_engine.py          # 任务队列引擎（subprocess 调度 + 异步回调）
│   │   ├── screen_capture.py       # ADB 截图服务（单帧 + 持续流）
│   │   ├── websocket_manager.py    # WebSocket 连接池与消息广播
│   │   └── version_manager.py      # Git 版本检测与拉取
│   ├── core/
│   │   ├── config.py               # Pydantic 配置（从 .env 加载）
│   │   └── constants.py            # 枚举常量（设备状态、任务状态、连接方式）
│   ├── schemas/
│   │   ├── device.py               # 设备/任务请求与响应 Pydantic 模型
│   │   └── task.py                 # 任务 Schema 再导出（扩展占位）
│   ├── models/
│   │   └── device.py               # 内部数据模型（dataclass）
│   └── coin11_tb/                  # 嵌入的 coin11-tb 脚本仓库
│       ├── 淘金币任务.py
│       ├── 淘宝现金签到.py
│       ├── 支付宝农场.py
│       ├── 闲鱼现金签到.py
│       └── ...                     # 其他自动化脚本
├── tests/                          # 单元 & 集成测试
├── .env                            # 环境变量配置（不提交到 Git）
├── .gitignore
├── pyproject.toml                  # 项目元数据与依赖声明
├── requirements.txt                # Pip 依赖清单
└── README.md                       # 本文件
```

---

## API 文档

所有 API 端点均以 `/api` 为前缀。启动服务后可通过 `http://127.0.0.1:8000/docs` 浏览完整 Swagger 文档。

### 设备管理

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/devices` | 获取所有已连接的 ADB 设备列表 |
| `POST` | `/api/devices/connect` | 远程连接 ADB 设备（`address: "IP:Port"`） |
| `DELETE` | `/api/devices/{serial}` | 断开指定设备连接 |
| `GET` | `/api/devices/{serial}` | 获取单台设备的详细信息（型号、Android 版本、连接方式） |
| `GET` | `/api/devices/{serial}/screenshot` | 获取设备单帧截图（HTTP 降级方案，返回 base64 PNG） |
| `GET` | `/api/devices/{serial}/queue` | 获取指定设备的任务队列 |

**设备信息示例：**

```json
{
  "serial": "192.168.1.100:5555",
  "model": "SM-S928B",
  "status": "online",
  "connection_type": "wifi",
  "android_version": "14"
}
```

### 任务队列

所有任务端点以 `/api/devices/{device_id}/queue` 为前缀。

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/devices/{device_id}/queue` | 获取设备任务队列 |
| `POST` | `/api/devices/{device_id}/queue` | 添加任务到队列（`body: {"script": "淘金币任务.py"}`） |
| `DELETE` | `/api/devices/{device_id}/queue/{task_id}` | 从队列中移除指定任务（运行中任务不可删除） |
| `PUT` | `/api/devices/{device_id}/queue/reorder` | 按 task_id 列表重新排序队列 |
| `POST` | `/api/devices/{device_id}/queue/start` | 启动队列执行（自动开启 WebSocket 日志推送和截图流） |
| `POST` | `/api/devices/{device_id}/queue/stop` | 停止队列执行 |
| `GET` | `/api/devices/{device_id}/queue/scripts` | 获取原项目中的可用脚本列表 |

**任务对象示例：**

```json
{
  "id": "a1b2c3d4",
  "device_id": "192.168.1.100:5555",
  "script_name": "淘金币任务.py",
  "script_path": "D:/coin11-tb/淘金币任务.py",
  "status": "running",
  "position": 0,
  "created_at": "2026-06-23T10:30:00",
  "started_at": "2026-06-23T10:31:00",
  "finished_at": null,
  "log": "[INFO] 开始执行..."
}
```

### 脚本列表

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/scripts` | 获取原项目中所有可用的自动化脚本列表（排除工具类脚本） |

自动排除的脚本：`utils.py`、`chromedriver.py`、`识别图片测试.py`。

### 版本更新

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/update/check` | 检查 coin11-tb 仓库是否有远程更新（`git fetch` + `rev-list`） |
| `POST` | `/api/update/pull` | 拉取远程更新（`git pull origin main`） |

**更新检查返回示例：**

```json
{
  "has_update": true,
  "current_commit": "abc1234",
  "latest_commit": "def5678",
  "commits_behind": 3,
  "commit_messages": ["fix: 签到页面适配新UI", "feat: 新增618活动脚本"],
  "checked_at": "2026-06-23T10:35:00"
}
```

### 健康检查

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/health` | 服务健康检查（返回 ADB 可用性、脚本路径状态） |

---

## WebSocket 实时通信

### 连接地址

```
ws://127.0.0.1:8000/ws/device/{device_id}?token={token}
```

| 参数 | 说明 |
|------|------|
| `device_id` | 设备序列号（与 ADB serial 一致） |
| `token` | WebSocket 鉴权 Token（默认 `coin11-control-token`，可通过 `.env` 修改） |

### 消息类型

所有消息为 JSON 格式：

```json
{
  "type": "<消息类型>",
  "device_id": "<设备序列号>",
  "data": "<数据内容>"
}
```

| 类型 | data 格式 | 说明 |
|------|-----------|------|
| `screenshot` | `string` (base64 编码的 PNG) | 设备实时屏幕截图（默认 2 FPS） |
| `log` | `string` (纯文本) | 任务执行日志行 |
| `status` | `string` (JSON: `{"task_id": "...", "status": "..."}`) | 任务状态变更通知 |
| `screencast` | `string` (如 `"started"` / `"stopped"`) | 截图流生命周期事件 |

**任务状态枚举：** `pending` → `running` → `completed` / `failed`

### 客户端控制指令

客户端可向 WebSocket 发送以下文本指令：

| 指令 | 效果 |
|------|------|
| `ping` | 服务端回复 `{"type":"pong"}` |
| `start_screencast` | 启动截图流推送 |
| `stop_screencast` | 停止截图流推送 |

截图流在 WebSocket 建立连接后**自动启动**，无需客户端主动发送 `start_screencast`。当最后一个客户端断开连接时，截图流自动停止以节省资源。

---

## 配置说明

通过项目根目录的 `.env` 文件配置（参考 `app/core/config.py`）：

```ini
# coin11-tb 远程仓库地址（启动时自动 clone 到 coin11_tb/）
COIN11_TB_REPO_URL=https://github.com/czl0325/coin11-tb.git

# 自定义 coin11-tb 路径（可选，留空则使用内置 coin11_tb/）
# COIN11_TB_PATH=D:\path\to\custom\coin11-tb

# ADB 可执行文件路径（默认使用 PATH 中的 adb）
ADB_PATH=adb

# 服务监听地址
HOST=127.0.0.1

# 服务监听端口
PORT=8000

# CORS 允许的来源（JSON 数组字符串）
CORS_ORIGINS=["http://localhost:5173","http://127.0.0.1:5173"]

# WebSocket 鉴权 Token
WS_AUTH_TOKEN=coin11-control-token

# DeepSeek API Key（可选，用于调试）
DEEPSEEK_API_KEY=
```

### 配置项说明

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `COIN11_TB_REPO_URL` | `https://github.com/czl0325/coin11-tb.git` | coin11-tb 远程仓库地址，启动时自动 clone/更新到 `coin11_tb/` |
| `ADB_PATH` | `adb` | ADB 可执行文件路径。留空则从系统 PATH 查找 |
| `HOST` | `127.0.0.1` | 监听地址。生产环境可改为 `0.0.0.0` |
| `PORT` | `8000` | 监听端口 |
| `CORS_ORIGINS` | 前端开发地址 | 允许跨域的前端来源 |
| `WS_AUTH_TOKEN` | `coin11-control-token` | WebSocket 连接鉴权 Token |
| `DEEPSEEK_API_KEY` | — | DeepSeek API Key（仅调试用途） |

---

## 多设备工作流

### 设备发现

1. **USB 设备** — 安卓设备通过 USB 连接电脑后，ADB 自动识别
2. **Wi-Fi 设备** — 通过 `adb connect IP:Port` 或 API `POST /api/devices/connect` 连接
3. **自动过滤** — 服务自动过滤 ADB TLS 服务发现名称（`adb-xxx._adb-tls-connect._tcp`），只保留真正可操作的设备

### 任务队列

每台设备拥有独立的 FIFO 任务队列：

```
设备 A (192.168.1.100:5555)        设备 B (USB: R58N12345AB)
┌─────────────────────┐           ┌─────────────────────┐
│ [0] 淘金币任务.py    │           │ [0] 支付宝农场.py    │
│ [1] 淘宝现金签到.py  │           │ [1] 闲鱼现金签到.py  │
│ [2] 天猫摇钱树.py    │           └─────────────────────┘
└─────────────────────┘
```

- **入队**：添加任务到队尾，自动校验脚本白名单
- **重排**：通过 task_id 列表自定义执行顺序
- **启动**：按 FIFO 顺序依次执行，每个任务在前一个完成后自动启动
- **停止**：取消当前正在执行的任务，队列保留未完成的任务

### 截图流

- 每个设备的 WebSocket 连接建立后**自动启动**截图流
- 默认帧率 **2 FPS**，可通过代码调整
- 截图数据以 **base64** 编码的 PNG 格式推送
- 支持多客户端同时订阅同一设备的截图流
- 最后一个客户端断开时自动停止截图流，节省 ADB 资源

### 日志与状态推送

任务执行时，`task_engine` 通过异步回调将日志行和状态变更实时推送到 WebSocket：

```
[WebSocket]  log      →  {"type":"log", "data":"[INFO] 打开淘宝..."}
[WebSocket]  status   →  {"type":"status", "data":{"task_id":"a1b2","status":"running"}}
[WebSocket]  status   →  {"type":"status", "data":{"task_id":"a1b2","status":"completed"}}
```

### 版本管理

服务在项目根目录的 `coin11_tb/` 内置了 coin11-tb 仓库：

1. **启动时**：自动 clone（如果不存在）或检查仓库完整性
2. **更新检查**：通过 API `GET /api/update/check` 检查远程是否有新 commit
3. **拉取更新**：通过 API `POST /api/update/pull` 拉取最新脚本
4. **仓库状态**：通过 API `GET /api/update/repo-status` 查看仓库状态

---

## 注意事项

### ADB 依赖

- 服务依赖 **ADB (Android Debug Bridge)** 进行设备通信
- 确保 `adb` 已在系统 PATH 中，或通过 `.env` 的 `ADB_PATH` 指定完整路径
- 首次使用前建议运行 `adb devices` 确认 ADB 工作正常
- 服务启动时会自动检查 ADB 可用性，若不可用则设备管理功能不可用

### 设备连接

- **USB 连接**：确保已开启"开发者选项"和"USB 调试"
- **Wi-Fi 连接**：设备需与电脑在同一局域网，通过 `adb connect <IP>:5555` 连接（端口默认 5555）
- **ADB TLS 连接**：Android 14+ 默认启用 ADB TLS，首次连接需要在设备上确认 RSA 密钥指纹

### Python 版本

- 项目推荐 Python ≥ 3.14
- 使用 `asyncio.to_thread` + `subprocess.run` 而非 `create_subprocess_exec`（Windows Python 3.14+ 兼容方案）

### Windows 环境

- 服务启动时会自动将 stdout 编码设为 UTF-8（`sys.stdout.reconfigure`），解决 Windows CMD GBK 乱码
- 使用 `uv` 或 `python -m venv` 创建虚拟环境均可

### 安全性

- WebSocket 连接需要 Token 鉴权（默认 `coin11-control-token`）
- 脚本入队有**白名单校验**和**路径穿越防护**（`os.path.basename`）
- 运行中的任务不能被删除，必须先停止队列
- `.env` 文件已加入 `.gitignore`，不会提交到版本库

### 端口冲突

如果默认的 8000 端口已被占用，修改 `.env` 中的 `PORT` 配置项即可。

---

## 开发

### 运行测试

```bash
pytest
```

测试使用 `pytest-asyncio`，配置了 `asyncio_mode = "auto"`，测试文件放在 `tests/` 目录下。

### 本地开发前端

CORS 已默认允许 `http://localhost:5173` 和 `http://127.0.0.1:5173`（Vite 默认端口），可直接与前端项目联调。

### 代码风格

- 使用类型注解（Python 3.14+）
- 服务层采用单例模式（全局 `_manager` / `_engine` 实例）
- 所有阻塞调用通过 `asyncio.to_thread` 委派到线程池执行
- 异常路径使用 `try/except` 捕获，不影响事件循环

---

## 许可证

本项目基于 MIT 许可证开源。
