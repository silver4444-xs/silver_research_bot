"""自主人工智能研究助手的 FastAPI 应用入口。"""

from __future__ import annotations

from typing import Any

import asyncio
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from loguru import logger
from pydantic import BaseModel, Field

from silver_research_bot.research_core import ResearchCore
from silver_research_bot.research_rag import ResearchRAG
from silver_research_bot.paper_analyzer.manager import PaperManager
from silver_research_bot.paper_analyzer.orchestrator import PaperOrchestrator


class ResearchRequest(BaseModel):
    """单个研究任务请求。"""

    topic: str = Field(..., min_length=1, description="研究主题")
    hypothesis: str | None = Field(default=None, description="研究假设")
    constraints: list[str] = Field(default_factory=list, description="约束条件")
    seeds: list[int] = Field(default_factory=lambda: [7, 13, 29], description="实验随机种子")
    epochs: int = Field(default=8, ge=1, le=200, description="CPU 训练轮数")
    dry_run: bool = Field(default=False, description="是否只创建工作区，不执行实验")


class BatchRequest(BaseModel):
    topics: list[str] = Field(default_factory=list)
    dry_run: bool = False


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    run_id: str | None = None


class PaperIngestRequest(BaseModel):
    title: str = Field(..., min_length=1)
    abstract: str = Field(default="")
    content: str = Field(..., min_length=1)
    authors: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class RagSearchRequest(BaseModel):
    query: str = Field(..., min_length=1)
    top_k: int = Field(default=5, ge=1, le=20)
    tag: str | None = None
    modality: str | None = None  # "text" | "formula" | "figure" | "table" | None (all)
    rerank: bool = True


class RagContextRequest(BaseModel):
    query: str = Field(..., min_length=1)
    top_k: int = Field(default=5, ge=1, le=20)


app = FastAPI(title="Silver Research Bot Research API", version="0.4.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

engine = ResearchCore()
rag = ResearchRAG()
_rag_initialized = False


def _get_rag() -> ResearchRAG:
    global rag, _rag_initialized
    if not _rag_initialized:
        from silver_research_bot.config.loader import load_config, resolve_config_env_vars
        config = resolve_config_env_vars(load_config())
        provider = None
        try:
            from silver_research_bot.providers.openai_compat_provider import OpenAICompatProvider
            defaults = config.agents.defaults
            provider = OpenAICompatProvider(
                api_key=config.get_api_key(defaults.model),
                api_base=config.get_api_base(defaults.model),
                default_model=defaults.model,
            )
        except Exception:
            pass
        rag = ResearchRAG(provider=provider, config=config.rag)
        _rag_initialized = True
    return rag


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/research/runs")
def get_runs() -> list[dict[str, Any]]:
    return engine.list_runs()


@app.post("/api/research/run")
def create_run(request: ResearchRequest) -> dict[str, Any]:
    try:
        return engine.create_run(
            topic=request.topic,
            hypothesis=request.hypothesis,
            constraints=request.constraints,
            seeds=request.seeds,
            epochs=request.epochs,
            dry_run=request.dry_run,
        )
    except Exception as exc:
        logger.exception("创建研究运行失败")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/research/run/{run_id}/execute")
def execute_run(run_id: str, request: ResearchRequest | None = None) -> dict[str, Any]:
    try:
        topic = request.topic if request else None
        seeds = request.seeds if request else None
        epochs = request.epochs if request else None
        return engine.execute_run(run_id, topic=topic, seeds=seeds, epochs=epochs)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="run not found") from None
    except Exception as exc:
        logger.exception("执行研究运行失败")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/research/batch")
def batch_run(request: BatchRequest) -> list[dict[str, Any]]:
    if not request.topics:
        raise HTTPException(status_code=400, detail="topics 不能为空")
    try:
        return engine.batch_runs(request.topics, dry_run=request.dry_run)
    except Exception as exc:
        logger.exception("批量研究运行失败")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/research/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    try:
        return engine.summarize_run(run_id)
    except Exception:
        raise HTTPException(status_code=404, detail="run not found") from None


@app.get("/api/research/runs/{run_id}/audit")
def get_audit(run_id: str) -> list[dict[str, Any]]:
    try:
        return engine.audit_events(run_id)
    except Exception:
        raise HTTPException(status_code=404, detail="run not found") from None


@app.get("/api/research/runs/{run_id}/paper-outline")
def get_paper_outline(run_id: str) -> dict[str, Any]:
    try:
        return engine.research_outline(run_id)
    except Exception:
        raise HTTPException(status_code=404, detail="run not found") from None


@app.get("/api/research/compare")
def compare_runs(run_ids: list[str]) -> dict[str, Any]:
    if not run_ids:
        raise HTTPException(status_code=400, detail="run_ids 不能为空")
    return engine.compare_runs(run_ids)


@app.post("/api/research/runs/{run_id}/notes")
def add_note(run_id: str, payload: dict[str, str]) -> dict[str, Any]:
    title = payload.get("title", "研究笔记")
    content = payload.get("content", "")
    if not content.strip():
        raise HTTPException(status_code=400, detail="content 不能为空")
    return engine.ingest_note(run_id, title, content)


@app.get("/api/rag/papers")
def list_papers() -> list[dict[str, Any]]:
    return _get_rag().list_papers()


@app.post("/api/rag/papers")
async def add_paper(request: PaperIngestRequest) -> dict[str, Any]:
    try:
        return await _get_rag().add_paper(
            title=request.title,
            abstract=request.abstract,
            content=request.content,
            authors=request.authors,
            tags=request.tags,
        )
    except Exception as exc:
        logger.exception("文献入库失败")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/rag/search")
async def rag_search(request: RagSearchRequest) -> dict[str, Any]:
    try:
        return await _get_rag().search(
            request.query, top_k=request.top_k, tag=request.tag,
            modality=request.modality, rerank=request.rerank,
        )
    except Exception as exc:
        logger.exception("RAG 检索失败")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/rag/context")
async def rag_context(request: RagContextRequest) -> dict[str, Any]:
    try:
        return await _get_rag().build_context(request.query, top_k=request.top_k)
    except Exception as exc:
        logger.exception("RAG 上下文构建失败")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/rag/suggest")
async def rag_suggest(request: RagContextRequest) -> dict[str, Any]:
    try:
        return await _get_rag().suggest_research(request.query, top_k=request.top_k)
    except Exception as exc:
        logger.exception("RAG 建议生成失败")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/rag/snapshot")
def rag_snapshot() -> dict[str, Any]:
    return _get_rag().export_snapshot()


@app.put("/api/rag/papers/{paper_id}")
async def rag_update(paper_id: str, request: PaperIngestRequest) -> dict[str, Any]:
    try:
        return await _get_rag().update_paper(
            paper_id=paper_id, title=request.title, abstract=request.abstract,
            content=request.content, authors=request.authors, tags=request.tags,
        )
    except Exception as exc:
        logger.exception("文献更新失败")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.delete("/api/rag/papers/{paper_id}")
def rag_delete(paper_id: str) -> dict[str, Any]:
    ok = _get_rag().delete_paper(paper_id)
    if not ok:
        raise HTTPException(status_code=404, detail="文献未找到")
    return {"status": "deleted", "paper_id": paper_id}


@app.post("/api/rag/reindex")
async def rag_reindex() -> dict[str, Any]:
    try:
        return await _get_rag().reindex()
    except Exception as exc:
        logger.exception("RAG 重建索引失败")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/agent/chat")
def chat(request: ChatRequest) -> dict[str, Any]:
    reply = _build_chat_reply(request)
    if request.run_id:
        try:
            reply["run"] = engine.summarize_run(request.run_id)
        except Exception:
            reply["run"] = None
    return reply


# ── Paper Analysis API ────────────────────────────────────────────

_workspace = Path("~/.silver_research_bot/workspace").expanduser()
_paper_manager = PaperManager(workspace=_workspace)
_paper_orchestrator: PaperOrchestrator | None = None


def _get_orchestrator() -> PaperOrchestrator:
    global _paper_orchestrator
    if _paper_orchestrator is None:
        from silver_research_bot.config.loader import load_config, resolve_config_env_vars
        from silver_research_bot.providers.openai_compat_provider import OpenAICompatProvider
        config = resolve_config_env_vars(load_config())
        defaults = config.agents.defaults
        provider = OpenAICompatProvider(
            api_key=config.get_api_key(defaults.model),
            api_base=config.get_api_base(defaults.model),
            default_model=defaults.model,
        )
        _paper_orchestrator = PaperOrchestrator(
            provider=provider, model=defaults.model, workspace=_workspace,
        )
        _paper_orchestrator.set_manager(_paper_manager)
    return _paper_orchestrator


class PaperCompareRequest(BaseModel):
    paper_ids: list[str] = Field(..., min_length=2)


@app.post("/api/paper/upload")
async def paper_upload(file: UploadFile, language: str = "auto"):
    suffix = Path(file.filename or "paper.pdf").suffix.lower()
    if suffix not in (".pdf", ".txt", ".md"):
        raise HTTPException(status_code=400, detail="仅支持 PDF / TXT / MD 文件")
    tmp = Path(f"/tmp/{file.filename}")
    try:
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(await file.read())
        orch = _get_orchestrator()
        # Stage 0: extract metadata immediately, start full analysis in background
        from silver_research_bot.paper_analyzer.extractor import extract_paper_meta
        meta = extract_paper_meta(str(tmp), orch.workspace)
        paper_dir = Path(meta["workspace_dir"])
        orch._write_progress(paper_dir, "parse", "completed",
            f"文档解析完成，{meta['page_count']}页，{meta['formula_count']}个公式")
        _paper_manager._index[meta["paper_id"]] = {
            "paper_id": meta["paper_id"], "title": meta["title"],
            "language": meta["language"], "page_count": meta["page_count"],
            "formula_count": meta["formula_count"], "status": "processing",
            "uploaded_at": meta.get("uploaded_at", ""),
            "workspace_dir": str(paper_dir),
        }
        _paper_manager._save_index()
        # Launch full analysis in background
        async def _run():
            try:
                await orch.analyze_paper(str(tmp), language, paper_meta=meta)
            except Exception as e:
                logger.exception(f"Paper analysis failed for {meta['paper_id']}: {e}")
                paper_dir = Path(meta.get("workspace_dir", ""))
                if paper_dir.is_dir():
                    paper_dir.joinpath("progress.json").write_text(
                        __import__("json").dumps({"stage":"error","status":"failed","message":str(e)[:200]}, ensure_ascii=False),
                        encoding="utf-8")
        asyncio.create_task(_run())
        return {"paper_id": meta["paper_id"], "title": meta["title"],
                "language": meta["language"], "status": "processing"}
    except Exception as exc:
        logger.exception("论文上传失败")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        tmp.unlink(missing_ok=True)


@app.get("/api/paper/list")
def paper_list():
    papers = _paper_manager.list_papers()
    # Fallback: scan filesystem for papers not in index
    indexed = {p["paper_id"] for p in papers}
    if _paper_manager.papers_dir.exists():
        for d in _paper_manager.papers_dir.iterdir():
            if d.is_dir() and d.name.startswith("p_") and d.name not in indexed:
                sf = d / "analysis_summary.json"
                ef = d / "extracted.json"
                if sf.exists() or ef.exists():
                    info = {"paper_id": d.name, "title": d.name, "language": "en",
                            "page_count": 0, "formula_count": 0, "status": "unknown",
                            "uploaded_at": "", "workspace_dir": str(d)}
                    papers.append(info)
    return papers


def _read_paper_from_fs(paper_dir: Path) -> dict:
    """直接从文件系统读取论文数据，不依赖索引。"""
    result = {"paper_id": paper_dir.name, "title": paper_dir.name, "language": "en",
              "page_count": 0, "formula_count": 0, "status": "unknown"}
    ef = paper_dir / "extracted.json"
    if ef.exists():
        import json as _json
        try:
            ex = _json.loads(ef.read_text(encoding="utf-8"))
            result["formula_count"] = ex.get("formula_count", 0)
            result["page_count"] = ex.get("page_count", 0)
        except Exception: pass
    sf = paper_dir / "analysis_summary.json"
    if sf.exists():
        import json as _json
        try:
            s = _json.loads(sf.read_text(encoding="utf-8"))
            result["status"] = "completed"
            result["has_translation"] = s.get("has_translation", False)
        except Exception: pass
    filename_map = {"translation": "translation.md", "system_model": "analysis_system_model.md",
                    "problem_formulation": "analysis_problem.md", "optimization_algorithm": "analysis_algorithm.md",
                    "experiment_design": "analysis_experiment.md", "formula_explanations": "formula_explanations.md",
                    "visualization_html": "analysis_visualization.html", "citation_graph_html": "citation_graph.html",
                    "review_theory": "review_theory.md", "review_engineering": "review_engineering.md",
                    "review_domain": "review_domain.md", "audit": "audit_report.json"}
    for key, fn in filename_map.items():
        fp = paper_dir / fn
        if fp.exists():
            result[key] = fp.read_text(encoding="utf-8")
    result["workspace_dir"] = str(paper_dir)
    return result


@app.get("/api/paper/{paper_id}")
def paper_get(paper_id: str):
    paper = _paper_manager.get_paper(paper_id)
    if not paper:
        pd = _paper_manager.papers_dir / paper_id
        if pd.is_dir():
            paper = _read_paper_from_fs(pd)
    if not paper:
        raise HTTPException(status_code=404, detail="论文未找到")
    return paper


@app.get("/api/paper/{paper_id}/export")
def paper_export(paper_id: str):
    """一键导出论文所有分析产物为 ZIP 文件。"""
    paper_dir = _paper_manager.papers_dir / paper_id
    if not paper_dir.is_dir():
        entry = _paper_manager._index.get(paper_id)
        if entry:
            paper_dir = Path(entry.get("workspace_dir", ""))
    if not paper_dir.is_dir():
        raise HTTPException(status_code=404, detail="论文未找到")

    import io, zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        artifact_files = [
            "translation.md", "analysis_system_model.md", "analysis_problem.md",
            "analysis_algorithm.md", "analysis_experiment.md",
            "formula_explanations.md", "analysis_visualization.html",
            "audit_report.json", "extracted.json", "progress.json",
        ]
        for name in artifact_files:
            fp = paper_dir / name
            if fp.exists():
                zf.write(fp, name)
        # Include original file
        for orig in paper_dir.glob("original.*"):
            zf.write(orig, orig.name)

    # Derive filename from paper title
    title = paper_id
    entry = _paper_manager._index.get(paper_id)
    if entry:
        title = entry.get("title", paper_id)
    else:
        ef = paper_dir / "extracted.json"
        if ef.exists():
            import json as _json2
            try:
                ex = _json2.loads(ef.read_text(encoding="utf-8"))
                title = ex.get("title", paper_id)
            except Exception:
                pass
    safe_title = "".join(c for c in title[:60] if c.isalnum() or c in "._- ()（）").strip()
    if not safe_title:
        safe_title = paper_id

    buf.seek(0)
    from fastapi.responses import StreamingResponse
    from urllib.parse import quote
    return StreamingResponse(
        buf, media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(safe_title)}.zip"},
    )


@app.get("/api/paper/{paper_id}/progress")
def paper_progress(paper_id: str):
    # Direct path construction — always consistent with upload
    ws_dir = _paper_manager.papers_dir / paper_id
    if not ws_dir.is_dir():
        # Fallback: try index entry in case workspace_dir differs
        entry = _paper_manager._index.get(paper_id)
        if entry:
            alt = Path(entry.get("workspace_dir", ""))
            if alt.is_dir():
                ws_dir = alt
            else:
                raise HTTPException(status_code=404, detail="论文未找到")
        else:
            raise HTTPException(status_code=404, detail="论文未找到")
    pf = ws_dir / "progress.json"
    if not pf.exists():
        return {"stage": "unknown", "status": "pending", "message": "等待开始..."}
    import json as _json
    return _json.loads(pf.read_text(encoding="utf-8"))


@app.get("/api/paper/{paper_id}/audit")
def paper_audit(paper_id: str):
    content = _paper_manager.get_artifact(paper_id, "audit")
    if content is None:
        raise HTTPException(status_code=404, detail="审计报告未找到")
    import json
    return json.loads(content)


@app.get("/api/paper/{paper_id}/figures/{filename}")
def paper_figure(paper_id: str, filename: str):
    """Serve extracted figure images from the paper workspace."""
    from fastapi.responses import FileResponse
    paper_dir = _paper_manager.papers_dir / paper_id
    img_path = paper_dir / "figures" / filename
    if not img_path.exists():
        raise HTTPException(status_code=404, detail="图片未找到")
    return FileResponse(str(img_path), media_type="image/png")


@app.get("/api/paper/{paper_id}/{artifact_type}")
def paper_artifact(paper_id: str, artifact_type: str):
    content = _paper_manager.get_artifact(paper_id, artifact_type)
    if content is None:
        raise HTTPException(status_code=404, detail="产物未找到")
    return {"paper_id": paper_id, "type": artifact_type, "content": content}


@app.post("/api/paper/compare")
async def paper_compare(request: PaperCompareRequest):
    orch = _get_orchestrator()
    comparison = await _paper_manager.compare_papers(
        request.paper_ids, provider=orch.provider, model=orch.model,
    )
    return {
        "paper_ids": comparison.paper_ids,
        "dimensions": comparison.dimensions,
        "synthesis": comparison.synthesis,
    }


class PaperAskRequest(BaseModel):
    question: str = Field(..., min_length=1)


@app.post("/api/paper/{paper_id}/ask")
async def paper_ask(paper_id: str, request: PaperAskRequest):
    """交互式分析 — 基于论文产物回答用户问题。"""
    ws_dir = _paper_manager.papers_dir / paper_id
    if not ws_dir.is_dir():
        raise HTTPException(status_code=404, detail="论文未找到")
    artifacts = {}
    for key, fname in [("translation","translation.md"),("system_model","analysis_system_model.md"),
        ("problem_formulation","analysis_problem.md"),("optimization_algorithm","analysis_algorithm.md"),
        ("experiment_design","analysis_experiment.md"),("formula_explanations","formula_explanations.md")]:
        fp = ws_dir / fname
        if fp.exists():
            artifacts[key] = fp.read_text(encoding="utf-8")[:3000]
    context = "\n\n".join(f"## {k}\n{v}" for k, v in artifacts.items())
    if not context:
        raise HTTPException(status_code=404, detail="论文分析产物不可用")
    orch = _get_orchestrator()
    try:
        response = await orch.provider.chat_with_retry(
            model=orch.model,
            messages=[
                {"role": "system", "content": f"基于以下论文分析产物回答用户问题。\n{context}"},
                {"role": "user", "content": request.question},
            ],
            tools=None, max_tokens=1000, temperature=0.3,
        )
        return {"paper_id": paper_id, "question": request.question, "answer": response.content or ""}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.delete("/api/paper/{paper_id}")
def paper_delete(paper_id: str):
    if not _paper_manager.delete_paper(paper_id):
        raise HTTPException(status_code=404, detail="论文未找到")
    return {"status": "deleted", "paper_id": paper_id}


@app.get("/")
def root() -> dict[str, str]:
    return {"message": "silver_research_bot research api"}


frontend_dist = __import__("pathlib").Path(__file__).resolve().parent.parent / "web" / "dist"
if frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")


def _build_chat_reply(request: ChatRequest) -> dict[str, Any]:
    text = request.message.strip()
    if any(keyword in text for keyword in ["批量", "batch", "多个", "基准"]):
        reply = "我建议先整理多个主题，再用批量模式生成统一实验蓝图，并保留每个 run 的独立审计记录。"
        action = "batch"
    elif any(keyword in text for keyword in ["论文", "latex", "草稿", "写作"]):
        reply = "我会根据真实实验数据生成 LaTeX 草稿，并优先保留方法、实验设置、结果和讨论四个部分。"
        action = "paper"
    elif any(keyword in text for keyword in ["实验", "训练", "cpu", "执行"]):
        reply = "我建议先创建工作区，再执行 CPU 实验并导出 results、metrics、summary 和审计日志。"
        action = "execute"
    elif any(keyword in text for keyword in ["文献", "rag", "检索"]):
        reply = "下一步可以接入文献检索与笔记整理模块，让 Agent 在研究之前先做信息汇总和假设归纳。"
        action = "rag"
    else:
        reply = "请告诉我研究主题、假设与约束。我可以帮助你生成 brief、实验计划、代码骨架、分析和论文初稿。"
        action = "ideation"
    return {"reply": reply, "suggested_action": action, "guidelines": ["先定义研究问题与指标", "再生成可执行实验代码", "最后用真实结果写论文草稿"]}
