"""Stage 2: 公式逐条解读 — HTML 卡片格式输出"""

from __future__ import annotations

from typing import TYPE_CHECKING

from silver_research_bot.utils.prompt_templates import render_template

if TYPE_CHECKING:
    from silver_research_bot.providers.base import LLMProvider

FORMULA_CSS = """<style>
.sec-title{font-size:13px;font-weight:500;color:var(--color-text-secondary);letter-spacing:.04em;padding:1.5rem 0 .5rem;border-top:.5px solid var(--color-border-tertiary);margin-top:.5rem}
.sec-title:first-of-type{border-top:none;padding-top:.5rem}
.frow{display:grid;grid-template-columns:56px 1fr;gap:0;border:.5px solid var(--color-border-tertiary);border-radius:var(--border-radius-md);margin-bottom:8px;overflow:hidden;background:var(--color-background-primary)}
.frow:hover{border-color:var(--color-border-secondary)}
.fnum{display:flex;align-items:center;justify-content:center;background:var(--color-background-secondary);font-size:12px;font-weight:500;color:var(--color-text-secondary);border-right:.5px solid var(--color-border-tertiary);padding:10px 6px;min-height:52px}
.fbody{padding:10px 14px}
.ftag{display:inline-block;font-size:11px;padding:2px 8px;border-radius:20px;font-weight:500;margin-bottom:5px}
.tag-sys{background:#E6F1FB;color:#0C447C}
.tag-mdp{background:#EEEDFE;color:#3C3489}
.tag-alg{background:#FAEEDA;color:#633806}
.tag-rwd{background:#FAECE7;color:#712B13}
.tag-gat{background:#E1F5EE;color:#085041}
.tag-obs{background:#FBEAF0;color:#72243E}
.fexpr{font-family:var(--font-mono);font-size:12px;color:var(--color-text-secondary);margin-bottom:4px;opacity:.85}
.fmean{font-size:13px;color:var(--color-text-primary);line-height:1.55}
.fmean b{font-weight:500}
</style>"""


def _wrap_html(body: str) -> str:
    """Wrap formula explanation HTML fragment with CSS styles."""
    import re
    body = re.sub(r'^```html?\s*\n?', '', body.strip())
    body = re.sub(r'\n?```\s*$', '', body)
    if '<style>' not in body:
        body = FORMULA_CSS + '\n' + body
    if '<h2 class="sr-only"' not in body:
        body = '<h2 class="sr-only">论文公式逐条解析</h2>\n' + body
    return body


async def explain_formulas(
    formulas: list[dict],
    full_text: str,
    provider: "LLMProvider",
    model: str,
    batch_size: int = 8,
) -> str:
    """对每个公式逐条解释，返回 HTML 卡片格式文档。

    formulas: [{index, latex, context}, ...]
    """
    if not formulas:
        return _wrap_html(await _explain_from_text(full_text, provider, model))

    system_prompt = render_template("paper/formula_explainer.md", strip=True)
    batches = [formulas[i:i + batch_size] for i in range(0, len(formulas), batch_size)]
    all_parts: list[str] = []

    for i, batch in enumerate(batches):
        formulas_text = "\n\n".join(
            f"公式{f['index']} (LaTeX): {f.get('latex', '')}\n上下文: {f.get('context', '')[:300]}"
            for f in batch
        )
        user_msg = (
            f"## 第 {i + 1}/{len(batches)} 批公式\n\n"
            f"请对以下公式按 HTML 卡片格式逐条解释含义，按类别分组，"
            f"公式表达式不要加 $ 包裹，直接输出 HTML：\n\n{formulas_text}"
        )
        response = await provider.chat_with_retry(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg},
            ],
            tools=None,
        )
        if response.content:
            all_parts.append(response.content)

    body = "\n\n".join(all_parts)
    return _wrap_html(body)


async def _explain_from_text(
    full_text: str, provider: "LLMProvider", model: str
) -> str:
    system_prompt = render_template("paper/formula_explainer.md", strip=True)
    chunk_size = 12000
    overlap = 500
    all_parts: list[str] = []

    start = 0
    chunk_idx = 0
    while start < len(full_text):
        chunk = full_text[start:start + chunk_size]
        chunk_idx += 1
        user_msg = (
            f"以下是论文第{chunk_idx}部分。请识别文中所有数学公式，"
            f"按 HTML 卡片格式逐条解释含义，按类别分组，公式表达式不要加 $ 包裹，直接输出 HTML：\n\n---\n\n" + chunk
        )
        try:
            response = await provider.chat_with_retry(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_msg},
                ],
                tools=None,
            )
            if response.content:
                all_parts.append(response.content)
        except Exception:
            pass

        start += chunk_size - overlap
        if start >= len(full_text):
            break

    body = "\n\n".join(all_parts)
    return _wrap_html(body)
