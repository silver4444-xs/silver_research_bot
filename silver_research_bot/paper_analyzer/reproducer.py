"""复现能力 — 提取算法伪代码 → LLM 转 Python → subprocess 执行 → 对比指标"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from silver_research_bot.providers.base import LLMProvider

_EXTRACT_ALGO = """Extract the main algorithm from the paper as pseudo-code. Output JSON with: name, steps (array), inputs (array), outputs (array). Only JSON.

Paper text:
{text}"""

_CODEGEN = """Convert this algorithm into a runnable Python function using standard library + numpy. Include a main() that runs on synthetic data and prints results. Only code, no explanation.

Steps: {steps}
Inputs: {inputs}
Outputs: {outputs}"""

_VALIDATE = """Compare reproduced results with claimed results. Score 1-10.

Claimed: {claimed}
Reproduced: {actual}

Brief comparison and score."""


async def extract_algorithm(full_text: str, provider: "LLMProvider", model: str) -> dict | None:
    try:
        response = await provider.chat_with_retry(
            model=model,
            messages=[{"role": "user", "content": _EXTRACT_ALGO.format(text=full_text[:6000])}],
            tools=None, max_tokens=1000, temperature=0.0,
        )
        content = (response.content or "{}").strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[1].rsplit("```", 1)[0]
        return json.loads(content)
    except Exception:
        return None


async def generate_code(algorithm: dict, provider: "LLMProvider", model: str) -> str:
    steps = json.dumps(algorithm.get("steps", []), ensure_ascii=False)
    inputs = ", ".join(algorithm.get("inputs", []))
    outputs = ", ".join(algorithm.get("outputs", []))
    try:
        response = await provider.chat_with_retry(
            model=model,
            messages=[{"role": "user", "content": _CODEGEN.format(steps=steps, inputs=inputs, outputs=outputs)}],
            tools=None, max_tokens=3000, temperature=0.0,
        )
        code = response.content or ""
        if code.startswith("```"):
            code = code.split("\n", 1)[1].rsplit("```", 1)[0]
        return code
    except Exception:
        return "# Code generation failed"


def execute_code(code: str, timeout: int = 60) -> dict:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as f:
        f.write(code)
        tmp_path = f.name
    try:
        result = subprocess.run(["python", tmp_path], capture_output=True, text=True, timeout=timeout)
        return {"stdout": result.stdout[:2000], "stderr": result.stderr[:1000], "returncode": result.returncode, "success": result.returncode == 0}
    except subprocess.TimeoutExpired:
        return {"stdout": "", "stderr": "Timeout", "returncode": -1, "success": False}
    except FileNotFoundError:
        return {"stdout": "", "stderr": "Python not found", "returncode": -1, "success": False}
    finally:
        try: Path(tmp_path).unlink()
        except OSError: pass


async def validate_results(claimed: str, actual: str, provider: "LLMProvider", model: str) -> str:
    try:
        response = await provider.chat_with_retry(
            model=model,
            messages=[{"role": "user", "content": _VALIDATE.format(claimed=claimed[:1500], actual=actual[:1500])}],
            tools=None, max_tokens=500, temperature=0.0,
        )
        return response.content or "Validation failed"
    except Exception:
        return "Validation failed"


async def run_reproduction(
    full_text: str, claimed_results: str, provider: "LLMProvider", model: str, workspace: str = ""
) -> dict:
    algorithm = await extract_algorithm(full_text, provider, model)
    if not algorithm:
        return {"status": "no_algorithm_found"}
    code = await generate_code(algorithm, provider, model)
    if not code or "failed" in code:
        return {"status": "codegen_failed", "code": code}
    exec_result = execute_code(code)
    validation = ""
    if exec_result["success"]:
        validation = await validate_results(claimed_results, exec_result["stdout"], provider, model)
    return {"status": "completed" if exec_result["success"] else "execution_failed", "algorithm_name": algorithm.get("name", ""), "code": code, "execution": exec_result, "validation": validation}
