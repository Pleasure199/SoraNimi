"""Small deterministic PageRank implementation, without a graph dependency."""

from pathlib import PurePosixPath


def rank_files(files, iterations: int = 20):
    if not files:
        return []
    modules = {str(PurePosixPath(f.path).with_suffix("")).replace("/", "."): f.path for f in files}
    edges = {
        f.path: {
            path
            for module, path in modules.items()
            if any(module.endswith(name) for name in f.imports) and path != f.path
        }
        for f in files
    }
    n = len(files)
    scores = dict.fromkeys(edges, 1 / n)
    for _ in range(iterations):
        dangling = sum(scores[path] for path in edges if not edges[path]) / n
        updated = {path: 0.15 / n + 0.85 * dangling for path in edges}
        for source, targets in edges.items():
            for target in targets:
                updated[target] += 0.85 * scores[source] / len(targets)
        scores = updated
    return sorted(files, key=lambda f: (-scores[f.path], f.path))


def render_map(files, token_budget: int = 2000):
    output = []
    remaining = token_budget * 3
    for file in rank_files(files):
        block = file.path + "\n" + "\n".join(f"  {line}" for line in file.definitions[:30])
        if len(block) > remaining:
            continue
        output.append(block)
        remaining -= len(block)
    return "\n\n".join(output)
