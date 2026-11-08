"""Check publishable files without reading ignored credentials or changing Git."""
import argparse
import ast
import json
import re
import subprocess
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
LOCAL_DIRS = {"outputs", "ouput", "workspace", "models", "third_party", ".venv", ".venv-firered", "venv", ".git"}
MEDIA_SUFFIXES = {".mp4", ".mkv", ".flv", ".mov", ".ts", ".wav", ".mp3", ".srt", ".ass", ".xml", ".pt", ".pth", ".onnx", ".safetensors", ".whl"}
TEXT_SUFFIXES = {".py", ".md", ".txt", ".json", ".yml", ".yaml", ".toml", ".cmd"}


def private_path(path):
    parts, name = path.parts, path.name.lower()
    if name == ".gitkeep" and parts[:2] == ("config", "local"):
        return False
    return bool(parts and (parts[0] in LOCAL_DIRS or parts[0].startswith(".venv-")) or
                "__pycache__" in parts or parts[:2] == ("config", "local") or
                (name.startswith(".env") and name != ".env.example") or
                name.endswith((".local.json", ".secrets.json", ".cookies.json", ".pyc", ".pyo", ".pth.tar")) or
                name in {"cookies.txt", "data_source.txt"} or path.suffix.lower() in MEDIA_SUFFIXES or
                (parts[:2] == ("resources", "fonts") and name != "readme.md"))


def candidate_files(root=ROOT):
    root = Path(root).resolve()
    try:
        repository = subprocess.run(["git", "-C", str(root), "rev-parse", "--show-toplevel"], capture_output=True)
        owns_index = repository.returncode == 0 and Path(repository.stdout.decode("utf-8").strip()).resolve() == root
        if owns_index:
            result = subprocess.run(["git", "-C", str(root), "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
                                    capture_output=True, check=True)
            names = {n for n in result.stdout.decode("utf-8").split("\0") if n}
            return sorted(Path(n) for n in names if (root / n).is_file())
    except FileNotFoundError:
        pass
    # Source archives have no Git index. Scan source files while leaving local data alone.
    return sorted(p.relative_to(root) for p in root.rglob("*") if p.is_file() and not private_path(p.relative_to(root)))


def inspect_repository(root=ROOT, files=None):
    root = Path(root).resolve()
    files = candidate_files(root) if files is None else [Path(p) for p in files]
    known = {p.as_posix() for p in files}
    issues = []
    total = 0
    for relative in files:
        path = (root / relative).resolve()
        if not path.is_relative_to(root):
            issues.append({"path": relative.as_posix(), "reason": "文件链接指向仓库外"})
            continue
        if private_path(relative):
            issues.append({"path": relative.as_posix(), "reason": "本机配置、凭据或生成数据出现在待发布文件中"})
            continue
        size = path.stat().st_size
        total += size
        if size > 10 * 1024 * 1024:
            issues.append({"path": relative.as_posix(), "reason": "超过项目规定的单文件10 MiB上限"})
        if path.suffix.lower() not in TEXT_SUFFIXES and path.name != ".env.example":
            continue
        text = path.read_text(encoding="utf-8-sig")
        # Report only location; never echo a matched credential.
        for match in re.finditer(r"\bsk-[A-Za-z0-9_-]{24,}\b", text):
            issues.append({"path": relative.as_posix(), "line": text.count("\n", 0, match.start())+1,
                           "reason": "疑似真实API密钥，内容已隐藏"})
        if path.suffix == ".py":
            try:
                tree = ast.parse(text)
                for node in ast.walk(tree):
                    if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                        for target in node.targets:
                            if isinstance(target, ast.Name) and target.id.casefold() in {"api_key", "sessdata", "password", "cookie"}:
                                value = node.value.value.strip()
                                if value and not re.fullmatch(r"[A-Z_][A-Z0-9_]*", value):
                                    issues.append({"path": relative.as_posix(), "line": node.lineno,
                                                   "reason": "疑似硬编码凭据，内容已隐藏；请改用环境变量"})
            except SyntaxError:
                issues.append({"path": relative.as_posix(), "reason": "Python文件有语法错误"})
        if relative.parts[:2] == ("config", "examples") and path.suffix == ".json":
            try:
                config = json.loads(text)
                if any(config.get("input", {}).get(k) for k in ("video", "srt", "output_dir")) or config.get("llm", {}).get("model"):
                    issues.append({"path": relative.as_posix(), "reason": "公开配置模板应将本机输入路径和模型选择留空"})
            except ValueError:
                issues.append({"path": relative.as_posix(), "reason": "配置模板不是有效JSON"})
        if path.suffix == ".md":
            for href in re.findall(r"\]\(([^)]+)\)", text):
                href = href.strip("<>")
                if re.match(r"(?:[A-Za-z][A-Za-z0-9+.-]*:|#|/)", href):
                    continue
                target = (path.parent / unquote(href.split("#", 1)[0])).resolve()
                if target.is_relative_to(root):
                    local = target.relative_to(root)
                    if private_path(local):
                        continue
                    if not target.exists() or (target.is_file() and local.as_posix() not in known):
                        issues.append({"path": relative.as_posix(), "reason": "本地文档链接无发布目标："+href})
    return {"status": "passed" if not issues else "failed", "files": len(files), "bytes": total, "issues": issues}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--json", type=Path, help="可选：保存检查结果，不包含凭据原文")
    args = parser.parse_args(argv)
    result = inspect_repository(args.root)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(f"仓库检查：{result['status']}；{result['files']} 个文件，{result['bytes']/1024/1024:.2f} MiB。")
    for issue in result["issues"]:
        print(f"- {issue['path']}" + (f":{issue['line']}" if issue.get("line") else "") + "：" + issue["reason"])
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
