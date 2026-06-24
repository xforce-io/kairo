"""本机级配置(machine-level):环境相关、不进版本库的设置,如 ASR 转写命令。

constitution 只声明意图(transform 的 backend),"这台机器具体怎么转写"由本模块解析。
解析顺序:KAIRO_ASR_CMD 环境变量 > $XDG_CONFIG_HOME/kairo/config.toml 的 [asr] > 无。

config.toml 按 transform 的 backend 名分节,形如:
    [asr.whisper]
    cmd = "mlx_whisper {input} --model ... -f txt -o {outdir} --output-name {stem}"
    origin = "whisper:large-v3-turbo"
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path


def _config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "kairo" / "config.toml"


def resolve_asr(backend: str) -> tuple[str, str] | None:
    """按 transform 的 backend 解析转写命令,返回 (cmd_template, origin),无配置则 None。

    顺序:KAIRO_ASR_CMD 环境变量(全局覆盖)> config.toml 的 [asr.<backend>] 节 > 无。
    cmd_template 支持占位符 {input}/{outdir}/{stem}/{output}(见 rules._run_asr_cmd)。
    """
    cmd = os.environ.get("KAIRO_ASR_CMD")
    if cmd:
        return cmd, os.environ.get("KAIRO_ASR_ORIGIN", backend)
    path = _config_path()
    if path.is_file():
        section = (tomllib.loads(path.read_text()).get("asr") or {}).get(backend) or {}
        cmd = section.get("cmd")
        if cmd:
            return cmd, section.get("origin", backend)
    return None
