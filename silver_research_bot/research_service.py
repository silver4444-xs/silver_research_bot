"""用于部署的 FastAPI 服务启动器。"""

from __future__ import annotations

import uvicorn

from silver_research_bot.research_app import app


def main() -> None:
    """以开发模式启动研究助手 API。"""

    uvicorn.run(app, host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    main()
