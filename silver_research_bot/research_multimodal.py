"""多模态索引工厂 — 从 extractor 结构化数据生成可检索的 chunk"""

from __future__ import annotations

from typing import Any

from silver_research_bot.research_rag import PaperChunk


class MultimodalIndexer:
    """将 extractor 输出的 formulas/figures/tables 转为 PaperChunk 列表。"""

    @staticmethod
    def index_from_extracted(
        extracted: dict[str, Any], paper_id: str, title: str
    ) -> list[PaperChunk]:
        chunks: list[PaperChunk] = []

        formulas: list[dict[str, Any]] = extracted.get("formulas", []) or []
        for f in formulas:
            latex = f.get("latex", "")
            context = f.get("context", "")
            text = f"Formula: {latex}"
            if context:
                text += f"\nContext: {context[:300]}"
            chunks.append(PaperChunk(
                chunk_id=f"fm_{paper_id}_{f.get('index', 0)}",
                paper_id=paper_id, title=title, section="formulas",
                text=text, chunk_type="formula",
                metadata={"latex": latex, "context": context[:300], "page": f.get("page", 0)},
            ))

        figures: list[dict[str, Any]] = extracted.get("figures", []) or []
        for fig in figures:
            caption = fig.get("caption", "")
            text = f"Figure {fig.get('index', '?')}: {caption}"
            chunks.append(PaperChunk(
                chunk_id=f"fg_{paper_id}_{fig.get('index', 0)}",
                paper_id=paper_id, title=title, section="figures",
                text=text, chunk_type="figure",
                metadata={"index": fig.get("index"), "page": fig.get("page", 0), "caption": caption},
            ))

        tables: list[dict[str, Any]] = extracted.get("tables", []) or []
        for tbl in tables:
            md_table = tbl.get("markdown", "")
            text = f"Table {tbl.get('index', '?')}:\n{md_table[:500]}"
            chunks.append(PaperChunk(
                chunk_id=f"tb_{paper_id}_{tbl.get('index', 0)}",
                paper_id=paper_id, title=title, section="tables",
                text=text, chunk_type="table",
                metadata={"index": tbl.get("index"), "page": tbl.get("page", 0), "rows": tbl.get("rows", 0), "cols": tbl.get("cols", 0)},
            ))

        return chunks
