# Deployment

## Docker (推荐)

多阶段构建：`node:20-alpine` 构建 Vue 前端 → `python:3.11-slim` 运行时，单端口 (8765) 同时服务 API + 前端。

### 前置条件

- Docker 20.10+
- Docker Compose v2

### 快速开始

```bash
# 1. 配置 API Key
cp .env.example .env
vim .env   # 填入 LLM Provider Key (OPENAI_API_KEY / ANTHROPIC_API_KEY / DEEPSEEK_API_KEY ...)

# 2. 构建镜像
docker compose build

# 3. 启动服务 (后台运行, 自动重启)
docker compose up -d

# 4. 验证
curl http://localhost:8765/api/health
# → {"status":"ok"}
```

浏览器访问 `http://localhost:8765` 即可使用完整应用。

### 常用操作

```bash
docker compose logs -f                  # 查看日志 (Ctrl+C 退出)
docker compose restart                  # 重启服务
docker compose down                     # 停止并删除容器 (volume 保留)
docker compose down -v                  # 停止并删除容器 + volume (数据丢失!)
docker compose up -d --build            # 更新代码后重新构建并启动
docker compose exec silver-research-bot python -m silver_research_bot status  # 查看状态
```

### 目录挂载

| 容器路径 | 持久化方式 | 说明 |
|----------|-----------|------|
| `/home/silver/.silver_research_bot/workspace` | named volume `silver_workspace` | 论文产物、RAG 索引、阅读历史、Agent 记忆 |
| `.env` | env_file 注入 | API Key，不进入镜像 |

工作区数据存储在 Docker volume 中，容器删除后数据依然保留。如需备份：

```bash
# 备份工作区到宿主机
docker run --rm -v silver_workspace:/data -v $(pwd)/backup:/backup alpine cp -r /data /backup/silver_workspace

# 恢复
docker run --rm -v silver_workspace:/data -v $(pwd)/backup:/backup alpine cp -r /backup/silver_workspace/. /data/
```

### 自定义端口

编辑 `docker-compose.yml`:

```yaml
ports:
  - "8080:8765"   # 宿主机:容器
```

### 自定义配置挂载

如果已有本机配置文件 `~/.silver_research_bot/config.json`，可直接挂载：

```yaml
volumes:
  - ~/.silver_research_bot:/home/silver/.silver_research_bot
  # 不再需要 named volume
```

---

## 直接部署 (无 Docker)

### 1. 构建前端

```bash
cd web
npm install
npm run build        # → web/dist/
cd ..
```

### 2. 配置环境

```bash
cp .env.example .env
vim .env             # 填入 API Key
pip install -r requirements.txt
pip install -e .
```

### 3. 启动

```bash
uvicorn silver_research_bot.research_app:app --host 0.0.0.0 --port 8765
```

FastAPI 检测到 `web/dist/` 存在后会自动挂载前端，访问 `http://localhost:8765` 即可。

### 4. 生产化 (systemd + Nginx)

**systemd 服务** — 创建 `/etc/systemd/system/silver-research-bot.service`:

```ini
[Unit]
Description=silver_research_bot API Server
After=network.target

[Service]
Type=simple
User=www-data
WorkingDirectory=/opt/silver_research_bot
ExecStart=/opt/silver_research_bot/.venv/bin/uvicorn silver_research_bot.research_app:app --host 127.0.0.1 --port 8765
Restart=always
RestartSec=10
EnvironmentFile=/opt/silver_research_bot/.env

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now silver-research-bot
```

**Nginx 反代** (可选，前端已内置在 FastAPI 中，Nginx 可提供 HTTPS + 限流):

```nginx
server {
    listen 443 ssl;
    server_name your-domain.com;

    ssl_certificate     /etc/ssl/certs/your-cert.pem;
    ssl_certificate_key /etc/ssl/private/your-key.pem;

    location / {
        proxy_pass http://127.0.0.1:8765;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # WebSocket 支持 (实时进度推送)
    location /api/paper/ {
        proxy_pass http://127.0.0.1:8765;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 86400s;
    }
}
```

---

## 镜像结构

```
python:3.11-slim (runtime)
├── /app/
│   ├── silver_research_bot/    ← 后端源码
│   ├── web/dist/               ← 前端构建产物 (来自 node:20-alpine 阶段)
│   ├── requirements.txt
│   └── pyproject.toml
├── /home/silver/               ← 非 root 用户 silver (UID 1000)
│   └── .silver_research_bot/
│       └── workspace/          ← 持久化 volume 挂载点
└── EXPOSE 8765
```
