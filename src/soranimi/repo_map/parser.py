import ast
from dataclasses import dataclass, field


@dataclass
class FileSymbols:
    path: str
    definitions: list[str] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)


def parse_python(path: str, source: str) -> FileSymbols:
    symbols = FileSymbols(path)
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return symbols
    for node in ast.walk(tree):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.definitions.append(f"{node.lineno}: {type(node).__name__} {node.name}")
        elif isinstance(node, ast.Import):
            symbols.imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            symbols.imports.append(node.module)
    return symbols
