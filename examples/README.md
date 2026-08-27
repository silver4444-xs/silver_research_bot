# 可运行示例 (examples)

克隆仓库后最先跑的东西——每个脚本都是 `python examples/0X_*.py` 直接可跑的独立示例。

## 前置条件

```bash
# 1. 安装依赖（[all] 包含全部可选依赖；只跑 02 可只装最小依赖）
pip install -e ".[all]"

# 2. 复制配置模板（首次运行必需）
cp config.example.json ~/.silver_research_bot/config.json

# 3. 填写 API Key
#    方式 A：编辑 ~/.silver_research_bot/config.json，把 API Key 写进对应 provider
#            （建议用 ${ENV_VAR} 引用，如 "apiKey": "${OPENAI_API_KEY}"）
#    方式 B：在仓库根目录 cp .env.example .env 并填写，启动时自动加载
```

> 没有 API Key 时，脚本不会静默跑成功：`03_agent_chat.py` 会直接抛出
> `ValueError: No API key configured for provider '...'`，可读可解；
> `01_analyze_paper.py` 会打印可读的「未配置 API Key」提示，并在任何网络调用前直接退出；
> `02_rag_search.py` 无需 key（无 provider 时自动回退 TF-IDF 向量）；
> `04_custom_provider.py` 连本地 Ollama/vLLM，无需云 key。

## 四个脚本

| 脚本 | 一句话说明 | 所需环境变量 |
|------|-----------|-------------|
| `01_analyze_paper.py` | 对一篇 PDF 跑完 8 阶段论文分析（解析→翻译→四维分析→公式→可视化→引用图谱→A/B 审稿→审计） | 默认模型对应 provider 的 API Key（`config.agents.defaults.model` 决定，如 `OPENAI_API_KEY` / `DEEPSEEK_API_KEY` / `OPENROUTER_API_KEY`） |
| `02_rag_search.py` | 入库一条 "Attention Is All You Need" 文献并执行混合检索（BM25+向量+RRF） | 无（无 provider 时用 TF-IDF 回退，完全离线可跑） |
| `03_agent_chat.py` | 最小 Agent 对话（推荐入口，经 `SilverAgent.from_config()` 走完整 AgentLoop） | 默认模型对应 provider 的 API Key（同上） |
| `04_custom_provider.py` | 演示用任意 OpenAI 兼容端点（DeepSeek / Ollama / vLLM / LM Studio）发起一次对话 | 无（硬编码本地端点，按需修改脚本内 `api_base`） |

## 运行方式

```bash
python examples/01_analyze_paper.py ~/papers/transformer.pdf   # 传 PDF 路径
python examples/02_rag_search.py "attention mechanism"         # 传查询语句
python examples/03_agent_chat.py "帮我总结这篇论文"              # 传消息
python examples/04_custom_provider.py                          # 无需参数
```

## 常见问题

- **`No API key configured for provider 'auto'`**：`config.agents.defaults.model` 对应的 provider 未配置 key。
  打开 `~/.silver_research_bot/config.json`，在 `providers.<name>.apiKey` 填入，或用 `${ENV_VAR}` 引用环境变量。
- **02 检索不到结果**：示例只入库了一条文献，换更宽泛的查询词，或调用 `rag.add_paper` 多入库几条。
- **01 需要真实 PDF**：脚本不做下载，请传入本地论文 PDF 的绝对路径。
