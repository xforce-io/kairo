"""转换后端执行 + dispatch:backend 名 → 处理器,统一返回结果。

分层:machine.py 解析「这台机器怎么转写」(配置);本模块「真正执行 backend」。
返回 ("ok", text, origin) | ("blocked", reason),由 TransformRule 据此 emit 或挂起。

- markitdown:进程内把二进制(docx/pptx/xlsx/pdf)转 markdown,纯 Python,无需机器配置。
- whisper / 其它 asr 系:走 machine.resolve_asr 解析的本机命令(_run_asr_cmd)。
"""

from __future__ import annotations

import shlex
import subprocess
import tempfile
from pathlib import Path

from kairo.machine import resolve_asr

_ASR_OUTPUT_PLACEHOLDERS = ("{output}", "{outdir}", "{stem}")
_ASR_TEXT_EXTS = (".txt", ".md", ".srt", ".vtt", ".json")

# 后端结果:("ok", text, origin) | ("blocked", reason)
BackendResult = tuple


def run_backend(backend: str, src: Path, src_hash: str) -> BackendResult:
    """按 backend 名分派执行;markitdown 进程内转换,其余按 asr 命令转写。"""
    if backend == "markitdown":
        return _run_markitdown(src, src_hash)
    return _run_asr(backend, src)


def _run_markitdown(src: Path, src_hash: str) -> BackendResult:
    """进程内把二进制转 markdown;失败/空产物/未安装 → blocked: convert-failed。"""
    try:
        from markitdown import MarkItDown
    except ImportError:
        return ("blocked", "convert-failed")
    try:
        res = MarkItDown().convert(str(src))
    except Exception:
        return ("blocked", "convert-failed")
    text = getattr(res, "text_content", None) or getattr(res, "markdown", None)
    if not text or not text.strip():
        return ("blocked", "convert-failed")
    return ("ok", text, f"markitdown-from:{src_hash}")


def _run_asr(backend: str, src: Path) -> BackendResult:
    """本机配置(env/config.toml)解析转写命令并执行;无配置 no-asr,命令失败 asr-failed。"""
    resolved = resolve_asr(backend)
    if resolved is None:
        return ("blocked", "no-asr")
    cmd_template, origin = resolved
    text = _run_asr_cmd(cmd_template, src)
    if not text:
        return ("blocked", "asr-failed")
    return ("ok", text, origin)


def _run_asr_cmd(template: str, input_path: Path) -> str | None:
    """跑本机转写命令,返回转写文本;失败/空产物返回 None。

    占位符:{input}=音频路径,{outdir}=临时输出目录,{stem}=输出名(transcript),
    {output}={outdir}/{stem}.txt。模板含任一输出占位 → 从产物文件读;否则捕获 stdout。
    """
    with tempfile.TemporaryDirectory() as d:
        outdir = Path(d)
        stem = "transcript"
        subs = {
            "input": str(input_path),
            "outdir": str(outdir),
            "stem": stem,
            "output": str(outdir / f"{stem}.txt"),
        }
        args = [_subst(tok, subs) for tok in shlex.split(template)]
        uses_output = any(p in template for p in _ASR_OUTPUT_PLACEHOLDERS)
        try:
            proc = subprocess.run(args, capture_output=True, text=True)
        except (OSError, ValueError):
            return None
        if proc.returncode != 0:
            return None
        if not uses_output:
            return proc.stdout.strip() or None
        preferred = outdir / f"{stem}.txt"
        candidates = [preferred] if preferred.is_file() else []
        for ext in _ASR_TEXT_EXTS:
            candidates += sorted(p for p in outdir.glob(f"*{ext}") if p != preferred)
        for c in candidates:
            text = c.read_text().strip()
            if text:
                return text
        return None


def _subst(token: str, subs: dict[str, str]) -> str:
    for key, value in subs.items():
        token = token.replace("{" + key + "}", value)
    return token
