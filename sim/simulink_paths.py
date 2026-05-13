from __future__ import annotations


def normalize_simulink_block_path(path: object) -> str:
    """Normalize user-entered Simulink block paths.

    Simulink block paths use forward slashes even on Windows. Users often paste
    paths with backslashes from config files; MATLAB accepts those poorly for
    get_param/set_param, which can make PID writes silently target nothing.
    """
    return str(path or "").strip().replace("\\", "/")


def normalize_simulink_block_paths(paths: object) -> list[str]:
    if not isinstance(paths, list):
        return []
    normalized: list[str] = []
    for path in paths:
        block_path = normalize_simulink_block_path(path)
        if block_path:
            normalized.append(block_path)
    return normalized
