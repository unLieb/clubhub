import os

_ROOT = os.path.dirname(os.path.dirname(__file__))


def _read_file(path: str, default: str) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        return default


VERSION = _read_file(os.path.join(_ROOT, "VERSION"), "0.0.0-dev")
BUILD_HASH = _read_file(os.path.join(_ROOT, "BUILD_HASH"), "unbekannt")
