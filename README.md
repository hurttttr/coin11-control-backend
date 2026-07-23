<div align="center">

# ⛓️ Coin11 Control Backend

**多设备安卓自动化任务控制平台**

[![Version](https://img.shields.io/badge/版本-v0.2.0-00f0ff?style=flat-square)](https://github.com/hurttttr/coin11-control-backend/releases)
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

**🐳 DevOps**
- Docker 多阶段构建
- GitHub Actions 自动打包
- 一键启动脚本（`.bat` / `.ps1`）
- Git 版本自动更新

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

---

## 📡 API 文档

所有端点均以 `/api` 为前缀。完整文档见 Swagger UI：`http://127.0.0.1:8000/docs`

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
│   ├── main.py                 # FastAPI 入口 + WebSocket
│   ├── api/v1/
│   │   ├── router.py           # 路由聚合 + 批量/设置端点
│   │   ├── devices.py          # 设备管理 + 自动任务触发
│   │   └── tasks.py            # 任务队列端点
│   ├── services/
│   │   ├── device_manager.py   # ADB 设备发现
│   │   ├── task_engine.py      # 任务队列引擎
│   │   ├── screen_capture.py   # 截图流服务
│   │   ├── websocket_manager.py
│   │   ├── auto_task_settings.py  # 自动任务持久化
│   │   └── version_manager.py
│   ├── schemas/device.py       # Pydantic 模型
│   └── coin11_tb/              # coin11-tb 脚本仓库
├── frontend/                   # 前端子模块
├── tests/
├── Dockerfile
├── docker-compose.yml
└── start-coin11.bat
```

---

## ⚙️ 配置说明

通过 `.env` 文件配置：

```ini
HOST=127.0.0.1
PORT=8000
ADB_PATH=adb
CORS_ORIGINS=["http://localhost:5173","http://127.0.0.1:5173"]
WS_AUTH_TOKEN=coin11-control-token
COIN11_TB_REPO_URL=https://github.com/czl0325/coin11-tb.git
```

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
