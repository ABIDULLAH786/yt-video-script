"""frame_meta.py - resolve a frame's provenance after the short rename.

Collected names carried the video id; short ones (f1_b1_00001) do not, so the
id now lives in <category>/rename_map.csv. Everything that needs provenance
asks here rather than parsing the filename, so both naming schemes work and a
half-renamed tree does not silently lose videos.
"""
import csv
import functools
import re
from pathlib import Path

OLD_RE = re.compile(r"^.*_t\d+_(?P<vid>[A-Za-z0-9_-]{11})_\d{5}$")


@functools.lru_cache(maxsize=64)
def _map_for(cat_dir: str) -> dict:
    p = Path(cat_dir) / "rename_map.csv"
    if not p.exists():
        return {}
    return {r["new_stem"]: r for r in csv.DictReader(open(p))}


def category_of(path: Path) -> Path | None:
    """Walk up to the folder holding MANIFEST.csv / rename_map.csv."""
    for up in path.parents:
        if (up / "MANIFEST.csv").exists() or (up / "rename_map.csv").exists():
            return up
    return None


def video_id(path: Path) -> str | None:
    """YouTube id for a frame, from its name or from the rename map."""
    m = OLD_RE.match(path.stem)
    if m:
        return m.group("vid")
    cat = category_of(path)
    if cat:
        row = _map_for(str(cat)).get(path.stem)
        if row:
            return row.get("video_id") or None
    return None


def model_of(path: Path) -> str | None:
    if "__" in path.name:
        return path.name.split("__")[1]
    cat = category_of(path)
    if cat:
        row = _map_for(str(cat)).get(path.stem)
        if row:
            return row.get("model") or None
    return None
