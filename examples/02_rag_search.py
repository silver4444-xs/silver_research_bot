"""入库一条文献并混合检索。用法: python 02_rag_search.py "attention mechanism" """
import asyncio, sys
from silver_research_bot.rag.research_rag import ResearchRAG

async def main(query: str):
    rag = ResearchRAG()
    await rag.add_paper(
        title="Attention Is All You Need",
        abstract="Transformer 论文摘要…",
        content="…正文…",
        authors=["Vaswani et al."], tags=["transformer"], year=2017, venue="NeurIPS",
    )
    hits = await rag.search(query, top_k=5)
    for r in hits["results"]:
        print(f"{r['score']:.3f}  {r['title']}  [{r['chunk_type']}]")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit('用法: python 02_rag_search.py "查询语句"')
    asyncio.run(main(sys.argv[1]))
