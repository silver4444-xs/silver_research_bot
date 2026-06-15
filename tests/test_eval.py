"""论文分析质量评估 + 模块单元测试"""

import pytest


class TestFormulaExtraction:
    def test_formula_count(self, sample_extracted):
        assert sample_extracted["formula_count"] == 2

    def test_formula_structure(self, sample_extracted):
        for f in sample_extracted["formulas"]:
            assert "latex" in f and "index" in f and len(f["latex"]) >= 3


class TestTranslationEval:
    def test_word_count(self, sample_paper_en):
        assert len(sample_paper_en.split()) >= 50

    def test_formula_preservation(self, sample_formulas):
        for f in sample_formulas:
            assert f.count("$$") % 2 == 0


class TestPipeline:
    def test_progress_json(self, tmp_path):
        import json
        progress = {"stage": "parse", "status": "completed", "message": "OK"}
        pf = tmp_path / "progress.json"
        pf.write_text(json.dumps(progress), encoding="utf-8")
        loaded = json.loads(pf.read_text(encoding="utf-8"))
        assert loaded["stage"] == "parse"


class TestBM25:
    def test_bm25_search(self):
        from silver_research_bot.research_bm25 import BM25Scorer
        bm25 = BM25Scorer()
        bm25.fit([("d1", "neural network deep learning"), ("d2", "database SQL query")])
        results = bm25.search("neural network", top_k=3)
        assert results[0][0] == "d1"

    def test_bm25_add_remove(self):
        from silver_research_bot.research_bm25 import BM25Scorer
        bm25 = BM25Scorer()
        bm25.add("x1", "test doc"); bm25.add("x2", "another doc")
        assert bm25.doc_count == 2
        bm25.remove("x1")
        assert bm25.doc_count == 1


class TestVectorStore:
    def test_add_search(self, tmp_path):
        import numpy as np
        from silver_research_bot.research_vector_store import VectorStore
        store = VectorStore(tmp_path, dim=4)
        vecs = [np.random.randn(4).astype(np.float32).tolist() for _ in range(5)]
        ids = [f"c{i}" for i in range(5)]
        store.add(vecs, ids)
        assert len(store) == 5
        results = store.search(vecs[0], top_k=3)
        assert results[0][0] == ids[0]

    def test_tombstones(self, tmp_path):
        from silver_research_bot.research_vector_store import VectorStore
        store = VectorStore(tmp_path, dim=4)
        store.add([[1.0, 0, 0, 0]], ["c1"])
        store.remove(["c1"])
        assert len(store) == 0
