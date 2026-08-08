"""Generate MODULE_FUNCTIONS.md from current source code."""
import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "public" / "MODULE_FUNCTIONS.md"


def first_doc(node):
    body = getattr(node, "body", [])
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        return str(body[0].value.value).strip().splitlines()[0]
    return ""


def collect(tree):
    out = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            doc = first_doc(node)
            out.append((node.name, type(node).__name__, doc))
    return out


def main():
    lines = [
        "# QQBot 模块函数说明",
        "",
        "> 自动从当前代码提取，不继续拆分。",
        "",
    ]
    modules = [ROOT / "bridge_qq.py", *sorted((ROOT / "qqbot_core").glob("*.py"))]
    for path in modules:
        source = path.read_text(encoding="utf-8-sig")
        tree = ast.parse(source)
        funcs = collect(tree)
        lines.append(f"## {path.relative_to(ROOT)}")
        lines.append("")
        for name, kind, doc in funcs:
            lines.append(f"### {kind}: {name}")
            lines.append("")
            lines.append(doc or "（无 docstring）")
            lines.append("")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(OUT)


if __name__ == "__main__":
    main()
