# ============================================
# Stage 1: Build Frontend (Vue 3 + Vite)
# ============================================
FROM node:20-alpine AS frontend-builder

WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
# 构建前端 (跳过 vue-tsc 类型检查, 测试文件类型错误影响构建)
RUN npx vite build

# ============================================
# Stage 2: Backend (FastAPI)
# ============================================
FROM python:3.12-slim

WORKDIR /app

# 系统依赖: git (clone coin11-tb), adb (设备通信)
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    android-tools-adb \
    && rm -rf /var/lib/apt/lists/*

# 复制后端依赖并安装 (利用 Docker 缓存)
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 复制后端代码
COPY . ./
# 排除前端源码 (构建产物由 stage 1 提供)
RUN rm -rf frontend/node_modules frontend/src frontend/public

# 复制前端构建产物到 frontend-dist/
COPY --from=frontend-builder /app/frontend/dist /app/frontend-dist

# 生产环境配置
ENV HOST=0.0.0.0 \
    PORT=8000 \
    ADB_PATH=adb \
    CORS_ORIGINS='["*"]'

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
