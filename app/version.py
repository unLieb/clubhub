import os
import re

_ROOT = os.path.dirname(os.path.dirname(__file__))


def _read_file(path: str, default: str) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        return default


VERSION = _read_file(os.path.join(_ROOT, "VERSION"), "0.0.0-dev")
BUILD_HASH = _read_file(os.path.join(_ROOT, "BUILD_HASH"), "unbekannt")


def _parse_changelog() -> list:
    """Parst CHANGELOG.md in eine Liste von Versions-Einträgen fürs Anzeigen
    unter /changelog - kein Markdown-Renderer nötig, da das Format
    ("## [x.y.z] - Datum" + Bullet-Liste) selbst vorgegeben ist."""
    content = _read_file(os.path.join(_ROOT, "CHANGELOG.md"), "")
    entries = []
    for block in re.split(r"^## \[", content, flags=re.MULTILINE)[1:]:
        header, _, rest = block.partition("\n")
        m = re.match(r"([^\]]+)\]\s*-\s*(.+)", header)
        if not m:
            continue
        changes = re.findall(r"^- (.+)$", rest, flags=re.MULTILINE)
        entries.append({"version": m.group(1).strip(), "date": m.group(2).strip(), "changes": changes})
    return entries


CHANGELOG = _parse_changelog()
