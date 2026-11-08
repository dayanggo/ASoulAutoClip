"""Read only the configured credential from a local .env, without expansion."""

import os
import re
from pathlib import Path

from core.highlight_selection import SelectionError


def load_local_key(name, path):
    if not name:
        return
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise SelectionError(
            "llm.api_key_env 应填写环境变量名称（例如 SILICONFLOW_API_KEY），不能填写 API Key。"
            "请将该字段设为 SILICONFLOW_API_KEY，并在项目根目录 .env 中填写 SILICONFLOW_API_KEY=你的密钥。"
        )
    if os.environ.get(name) or not Path(path).exists():
        return
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        key, separator, value = line.partition("=")
        if separator and key.strip() == name:
            value = value.strip()
            if value[:1] in {"'", '"'} and value[-1:] == value[:1]:
                value = value[1:-1]
            else:
                value = re.split(r"\s+#", value, maxsplit=1)[0].rstrip()
            if value:
                os.environ[name] = value
            return
