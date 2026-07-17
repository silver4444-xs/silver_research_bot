"""自主人工智能研究助手的 FastAPI 应用入口。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import asyncio
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from loguru import logger
from pydantic import BaseModel, Field

from silver_research_bot import __version__
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


app = FastAPI(title="Silver Research Bot Research API", version=__version__)
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

# ── Reading History ───────────────────────────────────────────────
_HISTORY_PATH = _workspace / "reading_history.json"

def _load_history():
    if _HISTORY_PATH.exists():
        import json
        try:
            raw = _HISTORY_PATH.read_text(encoding="utf-8").strip()
            if raw:
                return json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            pass
    return {"events": [], "bookmarks": {}, "notes": {}}

def _save_history(h):
    import json
    _HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    _HISTORY_PATH.write_text(json.dumps(h, ensure_ascii=False, indent=2), encoding="utf-8")


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
    structured: bool = True
    fast: bool = False


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
            result["formulas"] = ex.get("formulas", [])
            result["full_text"] = ex.get("full_text", "")
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
                    "review_domain": "review_domain.md"}
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
            "extracted.json", "progress.json",
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


_ARTIFACT_EXPORT_MAP = {
    "translation":           ("translation.md",              "text/markdown",       "全文翻译"),
    "system_model":          ("analysis_system_model.md",    "text/markdown",       "系统模型分析"),
    "problem_formulation":   ("analysis_problem.md",         "text/markdown",       "问题表述分析"),
    "optimization_algorithm":("analysis_algorithm.md",       "text/markdown",       "优化算法分析"),
    "experiment_design":     ("analysis_experiment.md",      "text/markdown",       "实验设计分析"),
    "formulas":              ("formula_explanations.md",     "text/markdown",       "公式解读"),
    "visualization":         ("analysis_visualization.html", "text/html",           "可视化分析"),
    "citation_graph":        ("citation_graph.html",         "text/html",           "引用图谱"),
    "review_theory":         ("review_theory.md",            "text/markdown",       "理论审稿意见"),
    "review_engineering":    ("review_engineering.md",       "text/markdown",       "工程审稿意见"),
    "review_domain":         ("review_domain.md",            "text/markdown",       "领域审稿意见"),
}


@app.get("/api/paper/{paper_id}/export/{artifact_type}")
def paper_artifact_export(paper_id: str, artifact_type: str):
    """导出单个分析产物为可下载文件。"""
    from urllib.parse import quote
    from fastapi.responses import FileResponse

    if artifact_type not in _ARTIFACT_EXPORT_MAP:
        raise HTTPException(status_code=400, detail=f"不支持的导出类型: {artifact_type}。"
                            f"可选: {', '.join(_ARTIFACT_EXPORT_MAP.keys())}")

    filename, mime_type, label = _ARTIFACT_EXPORT_MAP[artifact_type]

    paper_dir = _paper_manager.papers_dir / paper_id
    if not paper_dir.is_dir():
        entry = _paper_manager._index.get(paper_id)
        if entry:
            paper_dir = Path(entry.get("workspace_dir", ""))
    if not paper_dir.is_dir():
        raise HTTPException(status_code=404, detail="论文未找到")

    fp = paper_dir / filename
    if not fp.exists():
        raise HTTPException(status_code=404, detail=f"产物文件不存在: {filename}")

    # Derive download filename from paper title
    title = paper_id
    entry = _paper_manager._index.get(paper_id)
    if entry:
        title = entry.get("title", paper_id)
    safe_title = "".join(c for c in title[:40] if c.isalnum() or c in "._- ()（）").strip()
    ext = Path(filename).suffix
    dl_name = f"{safe_title}_{label}{ext}" if safe_title else f"{paper_id}_{label}{ext}"

    return FileResponse(
        fp, media_type=mime_type,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(dl_name)}"},
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




@app.get("/api/paper/{paper_id}/figures/{filename}")
def paper_figure(paper_id: str, filename: str):
    """Serve extracted figure images from the paper workspace."""
    from fastapi.responses import FileResponse
    paper_dir = _paper_manager.papers_dir / paper_id
    img_path = paper_dir / "figures" / filename
    if not img_path.exists():
        raise HTTPException(status_code=404, detail="图片未找到")
    ext = img_path.suffix.lower()
    mime_map = {
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
        ".svg": "image/svg+xml", ".tiff": "image/tiff", ".tif": "image/tiff",
    }
    media_type = mime_map.get(ext, "image/png")
    return FileResponse(str(img_path), media_type=media_type)


_compare_progress: dict[str, dict] = {}

async def _cleanup_progress():
    """Periodically remove stale progress entries (older than 10 minutes)."""
    import time as _time
    while True:
        await asyncio.sleep(300)
        now = _time.monotonic()
        stale = [tid for tid, v in _compare_progress.items()
                  if v.get("_ts", 0) and now - v["_ts"] > 600]
        for tid in stale:
            _compare_progress.pop(tid, None)

@app.on_event("startup")
async def _start_progress_cleanup():
    asyncio.create_task(_cleanup_progress())

@app.post("/api/paper/compare")
async def paper_compare(request: PaperCompareRequest):
    import uuid
    import time as _time
    task_id = uuid.uuid4().hex[:12]
    _compare_progress[task_id] = {"phase": "init", "message": "准备中…", "done": False, "_ts": _time.monotonic()}

    def on_progress(phase: str, message: str):
        _compare_progress[task_id] = {"phase": phase, "message": message, "done": phase == "complete"}

    orch = _get_orchestrator()
    comparison = await _paper_manager.compare_papers(
        request.paper_ids, provider=orch.provider, model=orch.model,
        structured=request.structured, fast=request.fast,
        on_progress=on_progress,
    )
    _compare_progress[task_id] = {"phase": "complete", "message": "对比完成", "done": True}
    result = {
        "task_id": task_id,
        "paper_ids": comparison.paper_ids,
        "dimensions": comparison.dimensions,
        "synthesis": comparison.synthesis,
        "comparison_html": comparison.comparison_html,
        "skipped_ids": comparison.skipped_ids,
    }
    if comparison.structured:
        sc = comparison.structured
        result["structured"] = {
            "paper_ids": sc.paper_ids,
            "dimensions": {
                dn: {
                    "name": d.name,
                    "extracted_scores": d.extracted_scores,
                    "extracted_items": d.extracted_items,
                    "score_reasons": d.score_reasons,
                }
                for dn, d in sc.dimensions.items()
            },
            "scores": sc.scores,
            "formula_overlap": sc.formula_overlap,
            "citation_overlap": sc.citation_overlap,
            "similarity_matrix": sc.similarity_matrix,
            "synthesis_md": sc.synthesis_md,
            "created_at": sc.created_at,
            "error": sc.error,
        }
        result["chart_data"] = comparison.chart_data
    return result


@app.get("/api/paper/compare/progress/{task_id}")
async def paper_compare_progress(task_id: str):
    progress = _compare_progress.get(task_id)
    if not progress:
        raise HTTPException(status_code=404, detail="任务未找到")
    return progress


@app.get("/api/paper/compare/history")
async def paper_compare_history():
    return {"comparisons": _paper_manager.list_comparisons()}


@app.get("/api/paper/compare/{comparison_id}")
async def paper_compare_get(comparison_id: str):
    record = _paper_manager.get_comparison(comparison_id)
    if not record:
        raise HTTPException(status_code=404, detail="对比记录未找到")
    return record


@app.delete("/api/paper/compare/{comparison_id}")
async def paper_compare_delete(comparison_id: str):
    if not _paper_manager.delete_comparison(comparison_id):
        raise HTTPException(status_code=404, detail="对比记录未找到")
    return {"status": "deleted", "comparison_id": comparison_id}


@app.get("/api/paper/compare/{comparison_id}/export")
async def paper_compare_export(comparison_id: str):
    from fastapi.responses import FileResponse
    zip_path = _paper_manager.export_comparison(comparison_id)
    if not zip_path:
        raise HTTPException(status_code=404, detail="对比记录未找到")
    return FileResponse(zip_path, media_type="application/zip", filename=f"{comparison_id}.zip")


class PaperBatchDeleteRequest(BaseModel):
    paper_ids: list[str] | None = None  # None = 删除全部
    delete_all: bool = False


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


@app.post("/api/paper/batch-delete")
def paper_batch_delete(req: PaperBatchDeleteRequest):
    """批量或全部删除论文。"""
    if req.delete_all:
        count = _paper_manager.delete_all_papers()
        return {"status": "deleted", "count": count, "all": True}
    if req.paper_ids:
        deleted = []
        for pid in req.paper_ids:
            if _paper_manager.delete_paper(pid):
                deleted.append(pid)
        return {"status": "deleted", "deleted": deleted, "count": len(deleted)}
    raise HTTPException(status_code=400, detail="请指定 paper_ids 或设置 delete_all=true")


# ── Reading History API ────────────────────────────────────────────

@app.post("/api/paper/{paper_id}/view")
def paper_record_view(paper_id: str):
    """记录论文查看事件。"""
    h = _load_history()
    h["events"].append({
        "paper_id": paper_id,
        "action": "view",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    if len(h["events"]) > 200:
        h["events"] = h["events"][-200:]
    _save_history(h)
    return {"status": "ok"}


@app.get("/api/history/events")
def history_events(limit: int = 50):
    """获取阅读历史时间线。"""
    h = _load_history()
    events = h["events"][-limit:]
    events.reverse()
    for ev in events:
        pid = ev["paper_id"]
        p = _paper_manager.get_paper(pid)
        if not p:
            pd = _paper_manager.papers_dir / pid
            if pd.is_dir():
                p = _read_paper_from_fs(pd)
        ev["title"] = p.get("title", pid) if p else pid
        ev["language"] = p.get("language", "?") if p else "?"
    return {"events": events}


@app.get("/api/paper/{paper_id}/bookmark")
def paper_get_bookmark(paper_id: str):
    """获取论文书签状态。"""
    h = _load_history()
    return {"paper_id": paper_id, "bookmarked": paper_id in h.get("bookmarks", {})}


@app.post("/api/paper/{paper_id}/bookmark")
def paper_toggle_bookmark(paper_id: str):
    """切换论文书签。"""
    h = _load_history()
    bm = h.setdefault("bookmarks", {})
    if paper_id in bm:
        del bm[paper_id]
        status = "unbookmarked"
    else:
        bm[paper_id] = True
        status = "bookmarked"
    _save_history(h)
    return {"paper_id": paper_id, "status": status}


@app.get("/api/paper/{paper_id}/notes")
def paper_get_notes(paper_id: str):
    """获取论文笔记。"""
    h = _load_history()
    return {"paper_id": paper_id, "notes": h.get("notes", {}).get(paper_id, "")}


class NotesUpdateRequest(BaseModel):
    notes: str = ""


@app.post("/api/paper/{paper_id}/notes")
def paper_save_notes(paper_id: str, request: NotesUpdateRequest):
    """保存论文笔记。"""
    h = _load_history()
    h.setdefault("notes", {})[paper_id] = request.notes
    _save_history(h)
    return {"paper_id": paper_id, "status": "saved"}


# ── Research Trends API ────────────────────────────────────────────

_TREND_KEYWORDS = [
    ("强化学习", ["reinforcement learning", "RL", "MARL", "MADRL", "DQN", "PPO", "A3C", "SAC", "TD3", "Q-learning", "Actor-Critic", "multi-agent"]),
    ("深度学习", ["deep learning", "DNN", "CNN", "RNN", "LSTM", "Transformer", "attention", "neural network"]),
    ("图神经网络", ["graph neural", "GNN", "GCN", "GAT", "GATAC", "graph attention", "graph convolution"]),
    ("无人机/机器人", ["UAV", "drone", "robotics", "robot", "aerial", "multi-rotor", "quadcopter", "fixed-wing"]),
    ("优化算法", ["optimization", "gradient descent", "SGD", "Adam", "convex", "non-convex", "Lagrangian", "dual", "trajectory"]),
    ("通信网络", ["communication", "wireless", "5G", "6G", "IoT", "RSSI", "channel", "beamforming", "MIMO", "NOMA"]),
    ("边缘/云计算", ["edge computing", "cloud", "federated learning", "distributed", "MEC", "computation offloading"]),
    ("计算机视觉", ["computer vision", "object detection", "segmentation", "image", "video", "YOLO", "ResNet"]),
    ("自然语言处理", ["NLP", "language model", "LLM", "GPT", "BERT", "transformer", "text", "embedding"]),
    ("资源分配", ["resource allocation", "scheduling", "power control", "bandwidth", "spectrum", "energy efficiency"]),
]

def _extract_keywords(text: str) -> list[str]:
    matched = set()
    lower = text.lower()
    for category, patterns in _TREND_KEYWORDS:
        for pat in patterns:
            if pat.lower() in lower:
                matched.add(category)
                break
    return list(matched)


@app.get("/api/trends")
def get_trends():
    """聚合所有论文的研究关键词趋势。"""
    papers = _paper_manager.list_papers()
    keyword_timeline = []
    category_counts = {}
    for p in papers:
        if p.get("status") != "completed":
            continue
        pid = p["paper_id"]
        paper_data = _paper_manager.get_paper(pid)
        if not paper_data:
            pd = _paper_manager.papers_dir / pid
            if pd.is_dir():
                paper_data = _read_paper_from_fs(pd)
            else:
                continue
        search_text = (paper_data.get("title", "") + " " +
                       paper_data.get("translation", "")[:5000] + " " +
                       paper_data.get("system_model", "")[:2000] + " " +
                       paper_data.get("problem_formulation", "")[:2000] + " " +
                       paper_data.get("optimization_algorithm", "")[:2000])
        keywords = _extract_keywords(search_text)
        for kw in keywords:
            category_counts[kw] = category_counts.get(kw, 0) + 1
        keyword_timeline.append({
            "paper_id": pid,
            "title": paper_data.get("title", pid)[:120],
            "uploaded_at": p.get("uploaded_at", "")[:10],
            "language": p.get("language", "en"),
            "keywords": keywords,
        })
    keyword_timeline.sort(key=lambda x: x["uploaded_at"] or "")
    categories_over_time = []
    running_counts = {}
    for entry in keyword_timeline:
        for kw in entry["keywords"]:
            running_counts[kw] = running_counts.get(kw, 0) + 1
        categories_over_time.append({
            "date": entry["uploaded_at"], "paper_id": entry["paper_id"],
            "title": entry["title"], **running_counts.copy(),
        })
    return {
        "category_counts": category_counts,
        "keyword_timeline": keyword_timeline,
        "categories_over_time": categories_over_time,
    }


@app.get("/api/paper/{paper_id}/pdf")
def paper_pdf(paper_id: str):
    """Serve the original PDF file for embedded PDF.js reader."""
    from fastapi.responses import FileResponse
    paper_dir = _paper_manager.papers_dir / paper_id
    if not paper_dir.is_dir():
        raise HTTPException(status_code=404, detail="论文未找到")
    orig = paper_dir / "original.pdf"
    if not orig.exists():
        candidates = list(paper_dir.glob("*.pdf"))
        if not candidates:
            raise HTTPException(status_code=404, detail="原始PDF未找到")
        orig = candidates[0]
    return FileResponse(str(orig), media_type="application/pdf",
                        headers={"Content-Disposition": "inline"})


@app.get("/api/paper/{paper_id}/{artifact_type}")
def paper_artifact(paper_id: str, artifact_type: str):
    content = _paper_manager.get_artifact(paper_id, artifact_type)
    if content is None:
        raise HTTPException(status_code=404, detail="产物未找到")
    return {"paper_id": paper_id, "type": artifact_type, "content": content}


@app.websocket("/api/paper/{paper_id}/stream")
async def paper_stream(websocket: WebSocket, paper_id: str):
    """WebSocket 端点 — 实时推送论文分析各阶段进度和中间结果。"""
    await websocket.accept()
    ws_dir = _paper_manager.papers_dir / paper_id
    try:
        last_stage = ""
        while True:
            pf = ws_dir / "progress.json"
            current = ""
            if pf.exists():
                import json as _j
                data = _j.loads(pf.read_text(encoding="utf-8"))
                current = data.get("stage", "")
                msg = data.get("message", "")
                status = data.get("status", "")
                if current != last_stage:
                    await websocket.send_json({"stage": current, "message": msg, "status": status})
                    last_stage = current
                if status in ("completed", "failed") and current == "review":
                    await websocket.send_json({"stage": "done", "message": "Pipeline complete", "status": "done"})
                    break
            await asyncio.sleep(1.5)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass


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
