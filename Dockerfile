# =============================================================================
# silver_research_bot — 多阶段 Docker 构建
# =============================================================================
# 阶段1: 构建 Vue 前端
# 阶段2: Python 运行时 + 服务
# =============================================================================

# ── 阶段1: 前端构建 ─────────────────────────────────────────────────────────
FROM node:20-alpine AS frontend-builder

WORKDIR /build/web

# 安装前端依赖
COPY web/package.json web/package-lock.json* ./
RUN npm ci 2>/dev/null || npm install

# 构建前端
COPY web/ ./
RUN npm run build

# ── 阶段2: Python 运行时 ────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

LABEL org.opencontainers.image.title="silver_research_bot"
LABEL org.opencontainers.image.description="AI-powered paper research assistant"
LABEL org.opencontainers.image.version="0.7.0"

# 安装 PyMuPDF 所需的系统库
RUN apt-get update && apt-get install -y --no-install-recommends \
    libmupdf-dev \
    && rm -rf /var/lib/apt/lists/*

# 创建非 root 用户
RUN useradd --create-home --uid 1000 silver && \
    mkdir -p /home/silver/.silver_research_bot/workspace && \
    chown -R silver:silver /home/silver/.silver_research_bot

WORKDIR /app

# 安装 Python 依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制后端源码
COPY silver_research_bot/ ./silver_research_bot/
COPY pyproject.toml .

# 复制前端构建产物
COPY --from=frontend-builder /build/web/dist/ ./web/dist/

# 安装项目自身 (使 silver_research_bot 包可导入)
RUN pip install --no-cache-dir -e .

USER silver
EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8765/api/health')" || exit 1

CMD ["uvicorn", "silver_research_bot.research_app:app", "--host", "0.0.0.0", "--port", "8765"]
