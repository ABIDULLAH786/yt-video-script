#!/usr/bin/env python3
"""
video_registry.py - the global record of every YouTube video this pipeline has
already turned into frames.

WHY THIS EXISTS. The collector used to work out "have I seen this video?" by
scanning the frame filenames on disk. That answer disappears the moment the
frames are moved, triaged or deleted — and they always are, because triage is
the next step after collection. Measured on our own corpus: the raw folders for
five of seven families were cleared after triage, so a re-run would have
re-downloaded and re-cut every one of those videos.

It also could not answer the question that matters most. YouTube returns the
same video for searches of quite different aircraft; on one measured run 133
videos had landed in more than one model directory and 38 of those spanned
FAMILIES, one video sitting in nine directories at once. Each copy is a second
full set of frames of the same footage, and usually the wrong aircraft for at
least one of the categories that took it.

So the registry is a single JSON file, independent of the frames, that answers
one question: has this video id been processed before, by ANY category? If yes
it is skipped, whatever category is asking now.

  WRITTEN AFTER EVERY VIDEO, not at the end of a run. A run killed by a
  rate-limit, a full disk or Ctrl-C keeps everything it had already done.

Shape of the file:

    {
      "version": 1,
      "updated_utc": "2026-10-21T09:14:03Z",
      "counts": {"videos": 1323, "processed": 1290, "failed": 33},
      "categories": {                      <- derived on save, for humans
        "f1_folding_quad": {
          "videos": 177,
          "urls": ["https://www.youtube.com/watch?v=...", ...]
        }
      },
      "videos": {                          <- the source of truth
        "p6aNfWdPqJQ": {
          "url": "https://www.youtube.com/watch?v=p6aNfWdPqJQ",
          "category": "f1_folding_quad",   <- the category that FIRST took it
          "model": "dji_neo",
          "term": 1,
          "title": "DJI Neo flight test",
          "duration_s": 412,
          "frames": 124,
          "status": "processed",
          "attempts": 1,
          "processed_utc": "2026-10-21T09:14:03Z"
        }
      }
    }

`categories` is rebuilt from `videos` on every save, so the two can never drift.
Edit `videos`, never `categories`.

CLI:

    ./video_registry.py stats                     counts per category
    ./video_registry.py check <url-or-id> [...]   has this been processed?
    ./video_registry.py list --category f1_folding_quad
    ./video_registry.py add <url-or-id> --category X [--model Y]
    ./video_registry.py remove <url-or-id> [...]  let it be collected again
    ./video_registry.py export --csv videos.csv
    ./video_registry.py seed ...                  build from an existing corpus
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parent / "video_registry.json"
VERSION = 1
URL = "https://www.youtube.com/watch?v={}"

# A YouTube id is exactly 11 characters of [A-Za-z0-9_-].
_ID_RE = re.compile(r"[A-Za-z0-9_-]{11}")
# "<model>_t<term>_<videoid>_00001.jpg" — the name the collector writes.
_FRAME_RE = re.compile(r"^.*_t\d+_(?P<vid>[A-Za-z0-9_-]{11})_\d{5}\.(?:jpg|jpeg|png)$", re.I)

STATUS_PROCESSED = "processed"
STATUS_FAILED = "failed"
MAX_ATTEMPTS = 2          # a video that failed this often is not retried


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def video_id(text: str) -> str | None:
    """Accept a bare id, a watch URL, a youtu.be link or a frame filename."""
    text = text.strip()
    if not text:
        return None
    m = _FRAME_RE.match(Path(text).name)
    if m:
        return m.group("vid")
    for pat in (r"[?&]v=([A-Za-z0-9_-]{11})",
                r"youtu\.be/([A-Za-z0-9_-]{11})",
                r"/(?:shorts|embed|live)/([A-Za-z0-9_-]{11})"):
        m = re.search(pat, text)
        if m:
            return m.group(1)
    if _ID_RE.fullmatch(text):
        return text
    return None


class Registry:
    """The global video record. Every mutation is written to disk at once.

    Each write re-reads the file first and merges into it, so a second process
    (a parallel fetch of another family, say) cannot silently erase the first
    one's entries. The write itself is a temp-file rename, which is atomic on
    POSIX — an interrupted run can leave the old file or the new one, never a
    half-written one.
    """

    def __init__(self, path: Path | str = DEFAULT_PATH, autosave: bool = True):
        self.path = Path(path)
        self.autosave = autosave
        self.data = self._read()
        # Ids removed in this session. save() merges into whatever is on disk,
        # which is additive — without this a delete is undone by the very save
        # that is meant to persist it, because the id is still in the file the
        # merge reads back.
        self._removed: set[str] = set()
        self._cleared_sweeps: set[str] = set()

    # ------------------------------------------------------------ disk
    def _read(self) -> dict:
        if not self.path.exists():
            return {"version": VERSION, "updated_utc": None,
                    "counts": {}, "categories": {}, "videos": {}}
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            sys.exit(f"{self.path} is not readable JSON ({e}).\n"
                     f"Fix or delete it — do NOT let a run continue without it, "
                     f"or every video will be downloaded again.")
        d.setdefault("videos", {})
        d.setdefault("categories", {})
        return d

    def save(self) -> None:
        """Merge into whatever is on disk now, then replace it atomically."""
        on_disk = self._read()
        merged = on_disk["videos"]
        for vid in self._removed:
            merged.pop(vid, None)
        for vid, entry in self.data["videos"].items():
            prev = merged.get(vid)
            # First processed record wins: it names the category that actually
            # collected the frames. A later failure must not overwrite it.
            if prev and prev.get("status") == STATUS_PROCESSED \
                    and entry.get("status") != STATUS_PROCESSED:
                continue
            merged[vid] = entry
        self.data["videos"] = merged
        sweeps = on_disk.get("sweeps", {})
        for name, models in self.data.get("sweeps", {}).items():
            sweeps.setdefault(name, {}).update(models)
        for name in list(sweeps):
            if name not in self.data.get("sweeps", {}) and name in self._cleared_sweeps:
                sweeps.pop(name, None)
        self.data["sweeps"] = sweeps
        self.data["version"] = VERSION
        self.data["updated_utc"] = now_utc()
        self.data["categories"] = self._derive_categories()
        self.data["counts"] = {
            "videos": len(merged),
            "processed": sum(1 for v in merged.values()
                             if v.get("status") == STATUS_PROCESSED),
            "failed": sum(1 for v in merged.values()
                          if v.get("status") == STATUS_FAILED),
            "frames": sum(int(v.get("frames") or 0) for v in merged.values()),
            "categories": len(self.data["categories"]),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._sweep_stale_temps()
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent),
                                   prefix=".registry-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self.data, fh, indent=2, ensure_ascii=False)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    _TEMP_TTL_S = 60

    def _sweep_stale_temps(self) -> None:
        """Delete temp files abandoned by a killed run.

        The write is temp-file-then-rename, and the cleanup on the failure path
        covers an exception — but not SIGKILL, a power cut or an OOM kill, where
        no Python runs at all. Measured: 200 hard kills during writes left 200
        orphans sitting next to the registry. They are swept on the next save
        rather than at exit, because the next save is the first moment any of
        our code runs again.

        Only files older than the TTL go, so a temp another process is writing
        right now is never touched — a real write takes milliseconds.
        """
        import time as _t
        cutoff = _t.time() - self._TEMP_TTL_S
        for p in self.path.parent.glob(".registry-*"):
            try:
                if p.is_file() and p.stat().st_mtime < cutoff:
                    p.unlink()
            except OSError:
                pass

    def _derive_categories(self) -> dict:
        cats: dict[str, dict] = {}
        for vid, e in sorted(self.data["videos"].items()):
            if e.get("status") != STATUS_PROCESSED:
                continue
            c = e.get("category") or "(uncategorised)"
            cats.setdefault(c, {"videos": 0, "frames": 0, "urls": []})
            cats[c]["videos"] += 1
            cats[c]["frames"] += int(e.get("frames") or 0)
            cats[c]["urls"].append(e.get("url") or URL.format(vid))
        return {k: cats[k] for k in sorted(cats)}

    # ------------------------------------------------------------ query
    def get(self, vid: str) -> dict | None:
        return self.data["videos"].get(vid)

    def is_processed(self, vid: str) -> bool:
        """True if this video has frames from ANY category.

        Category-blind on purpose. The same clip collected twice under two
        aircraft names is the same footage both times, and the second copy is
        usually mislabelled besides.
        """
        e = self.get(vid)
        return bool(e and e.get("status") == STATUS_PROCESSED)

    def should_skip(self, vid: str, retry_failed: bool = False) -> tuple[bool, str]:
        """(skip?, human reason)."""
        e = self.get(vid)
        if not e:
            return False, ""
        if e.get("status") == STATUS_PROCESSED:
            where = "/".join(x for x in (e.get("category"), e.get("model")) if x)
            return True, f"already in {where or '?'} ({e.get('frames', '?')} frames)"
        if e.get("status") == STATUS_FAILED and not retry_failed:
            n = int(e.get("attempts") or 1)
            if n >= MAX_ATTEMPTS:
                return True, (f"failed {n}x ({e.get('error', 'no reason recorded')}) "
                              f"— use --retry-failed to try again")
        return False, ""

    # ------------------------------------------------------------ mutate
    def record(self, vid: str, category: str, model: str = "", *,
               frames: int = 0, title: str = "", duration_s=None,
               term=None, source: str = "youtube") -> dict:
        """Mark a video processed and write the file immediately."""
        entry = {
            "url": URL.format(vid),
            "category": category,
            "model": model,
            "term": term,
            "title": title,
            "duration_s": duration_s,
            "frames": int(frames),
            "status": STATUS_PROCESSED,
            "attempts": int((self.get(vid) or {}).get("attempts") or 0) + 1,
            "source": source,
            "processed_utc": now_utc(),
        }
        self.data["videos"][vid] = entry
        if self.autosave:
            self.save()
        return entry

    def record_failure(self, vid: str, category: str, error: str,
                       model: str = "") -> dict:
        prev = self.get(vid) or {}
        if prev.get("status") == STATUS_PROCESSED:
            return prev
        entry = {
            "url": URL.format(vid),
            "category": category,
            "model": model,
            "status": STATUS_FAILED,
            "error": (error or "")[:200],
            "attempts": int(prev.get("attempts") or 0) + 1,
            "frames": 0,
            "processed_utc": now_utc(),
        }
        self.data["videos"][vid] = entry
        if self.autosave:
            self.save()
        return entry

    # ------------------------------------------------------- sweeps
    # A sweep is one pass over a category's models — "one new video for every
    # aircraft in f1", say. It is recorded HERE rather than as a .done file in
    # the model folder, because those folders are deleted once their frames
    # move into the filtered set, taking the resume state with them. The
    # registry outlives them.
    def sweep_done(self, sweep: str, fam: str, model_slug: str) -> bool:
        return f"{fam}/{model_slug}" in self.data.get("sweeps", {}).get(sweep, {})

    def mark_sweep(self, sweep: str, fam: str, model_slug: str,
                   videos: int = 0) -> None:
        s = self.data.setdefault("sweeps", {}).setdefault(sweep, {})
        s[f"{fam}/{model_slug}"] = {"videos": videos, "utc": now_utc()}
        if self.autosave:
            self.save()

    def sweep_progress(self, sweep: str) -> tuple[int, int]:
        """(models done, videos taken) for a sweep."""
        s = self.data.get("sweeps", {}).get(sweep, {})
        return len(s), sum(int(v.get("videos") or 0) for v in s.values())

    def clear_sweep(self, sweep: str) -> int:
        n = len(self.data.get("sweeps", {}).pop(sweep, {}))
        self._cleared_sweeps.add(sweep)
        if n and self.autosave:
            self.save()
        return n

    def remove(self, vid: str) -> bool:
        gone = self.data["videos"].pop(vid, None) is not None
        if gone:
            self._removed.add(vid)
            if self.autosave:
                self.save()
        return gone


# --------------------------------------------------------------- seeding
def seed_from_frames(reg: Registry, root: Path, category: str = "") -> int:
    """Read video ids out of frame filenames under root.

    Layout assumed: <root>/<category>/<model>/<frames>. Pass --category to
    override when root IS one category's folder.
    """
    n = 0
    if not root.exists():
        print(f"  ! {root} does not exist", file=sys.stderr)
        return 0
    for f in root.rglob("*"):
        if f.suffix.lower() not in (".jpg", ".jpeg", ".png"):
            continue
        m = _FRAME_RE.match(f.name)
        if not m:
            continue
        vid = m.group("vid")
        if reg.is_processed(vid):
            continue
        rel = f.relative_to(root).parts
        cat = category or (rel[0] if len(rel) > 2 else "(unknown)")
        mdl = rel[1] if len(rel) > 2 else (rel[0] if len(rel) > 1 else "")
        reg.record(vid, cat, mdl, source="seed:frames")
        n += 1
    return n


def seed_from_manifest_csv(reg: Registry, path: Path, category: str = "") -> int:
    """A triage MANIFEST.csv, which holds a source_path per frame."""
    n = 0
    cat = category or path.parent.name
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            src = row.get("source_path") or row.get("filename") or ""
            vid = video_id(src)
            if not vid or reg.is_processed(vid):
                continue
            reg.record(vid, cat, row.get("model_folder", ""), source="seed:manifest")
            n += 1
    return n


def seed_from_index_csv(reg: Registry, path: Path) -> int:
    """video_index.py output: video_id, url, frames, categories, models, ..."""
    n = 0
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            vid = video_id(row.get("video_id") or row.get("url") or "")
            if not vid or reg.is_processed(vid):
                continue
            cat = (row.get("categories") or row.get("category") or "").split("|")[0].strip()
            mdl = (row.get("models") or "").split("|")[0].strip()
            reg.record(vid, cat or "(unknown)", mdl,
                       frames=int(row.get("frames") or 0),
                       title=row.get("title", ""),
                       duration_s=row.get("duration_s") or None,
                       source="seed:index")
            n += 1
    return n


def seed_from_videos_json(reg: Registry, path: Path) -> int:
    """A per-model videos.json written by model_frames.py fetch."""
    n = 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return 0
    model = path.parent.name
    cat = path.parent.parent.name
    for vid, meta in data.items():
        if not video_id(vid) or reg.is_processed(vid):
            continue
        reg.record(vid, cat, model, frames=int(meta.get("frames") or 0),
                   title=meta.get("title", ""), duration_s=meta.get("duration"),
                   term=meta.get("term"), source="seed:videos.json")
        n += 1
    return n


# ------------------------------------------------------------------- cli
def cmd_stats(args):
    reg = Registry(args.registry)
    c = reg.data.get("counts", {})
    print(f"{reg.path}")
    print(f"updated {reg.data.get('updated_utc') or 'never'}\n")
    print(f"{'category':<34}{'videos':>9}{'frames':>10}")
    print("-" * 53)
    for name, e in reg.data.get("categories", {}).items():
        print(f"{name:<34}{e['videos']:>9,}{e['frames']:>10,}")
    print("-" * 53)
    print(f"{'TOTAL processed':<34}{c.get('processed', 0):>9,}{c.get('frames', 0):>10,}")
    if c.get("failed"):
        print(f"{'failed (skipped on re-run)':<34}{c['failed']:>9,}")


def cmd_check(args):
    reg = Registry(args.registry)
    for raw in args.videos:
        vid = video_id(raw)
        if not vid:
            print(f"{raw}: not a YouTube id or URL")
            continue
        e = reg.get(vid)
        if not e:
            print(f"{vid}: NEW — would be collected")
        elif e.get("status") == STATUS_PROCESSED:
            print(f"{vid}: processed in {e.get('category')}/{e.get('model')} "
                  f"({e.get('frames')} frames, {e.get('processed_utc')}) — would be SKIPPED")
        else:
            print(f"{vid}: failed {e.get('attempts')}x — {e.get('error', '')}")


def cmd_list(args):
    reg = Registry(args.registry)
    rows = [(v, e) for v, e in reg.data["videos"].items()
            if (not args.category or e.get("category") == args.category)
            and (args.failed or e.get("status") == STATUS_PROCESSED)]
    for vid, e in sorted(rows, key=lambda kv: (kv[1].get("category") or "", kv[0])):
        print(f"{e.get('url')}\t{e.get('category')}\t{e.get('model')}\t"
              f"{e.get('frames')}\t{e.get('title', '')[:60]}")
    print(f"\n{len(rows):,} videos", file=sys.stderr)


def cmd_add(args):
    reg = Registry(args.registry)
    n = 0
    for raw in args.videos:
        vid = video_id(raw)
        if not vid:
            print(f"! not a YouTube id or URL: {raw}", file=sys.stderr)
            continue
        reg.record(vid, args.category, args.model, frames=args.frames,
                   source="manual")
        n += 1
    print(f"recorded {n} video(s) under {args.category}")


def cmd_remove(args):
    reg = Registry(args.registry)
    n = sum(1 for raw in args.videos
            if (v := video_id(raw)) and reg.remove(v))
    print(f"removed {n} video(s) — they will be collected again on the next run")


def cmd_export(args):
    reg = Registry(args.registry)
    out = Path(args.csv)
    cols = ["video_id", "url", "category", "model", "frames", "status",
            "duration_s", "processed_utc", "title"]
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for vid, e in sorted(reg.data["videos"].items()):
            w.writerow([vid] + [e.get(c, "") for c in cols[1:]])
    print(f"{len(reg.data['videos']):,} rows -> {out}")


def cmd_seed(args):
    reg = Registry(args.registry, autosave=False)
    before = len(reg.data["videos"])
    total = 0
    for p in args.from_frames or []:
        n = seed_from_frames(reg, Path(p), args.category)
        print(f"  frames   {p}: +{n}")
        total += n
    for p in args.from_manifest or []:
        for f in sorted(Path(".").glob(p)) or [Path(p)]:
            if f.exists():
                n = seed_from_manifest_csv(reg, f, args.category)
                print(f"  manifest {f}: +{n}")
                total += n
    for p in args.from_index or []:
        if Path(p).exists():
            n = seed_from_index_csv(reg, Path(p))
            print(f"  index    {p}: +{n}")
            total += n
    for p in args.from_videos_json or []:
        for f in sorted(Path(".").glob(p)) or [Path(p)]:
            if f.exists():
                n = seed_from_videos_json(reg, f)
                print(f"  videos.json {f}: +{n}")
                total += n
    reg.save()
    print(f"\n{before:,} -> {len(reg.data['videos']):,} videos (+{total:,}) in {reg.path}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--registry", default=str(DEFAULT_PATH),
                    help="path to video_registry.json")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("stats", help="counts per category").set_defaults(fn=cmd_stats)

    p = sub.add_parser("check", help="would these videos be skipped?")
    p.add_argument("videos", nargs="+")
    p.set_defaults(fn=cmd_check)

    p = sub.add_parser("list", help="one line per recorded video")
    p.add_argument("--category")
    p.add_argument("--failed", action="store_true", help="include failed ones")
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser("add", help="record a video by hand")
    p.add_argument("videos", nargs="+")
    p.add_argument("--category", required=True)
    p.add_argument("--model", default="")
    p.add_argument("--frames", type=int, default=0)
    p.set_defaults(fn=cmd_add)

    p = sub.add_parser("remove", help="forget a video so it is collected again")
    p.add_argument("videos", nargs="+")
    p.set_defaults(fn=cmd_remove)

    p = sub.add_parser("export", help="dump the registry to CSV")
    p.add_argument("--csv", default="video_registry.csv")
    p.set_defaults(fn=cmd_export)

    p = sub.add_parser("seed", help="build the registry from an existing corpus")
    p.add_argument("--from-frames", action="append",
                   help="a folder of collected frames (repeatable)")
    p.add_argument("--from-manifest", action="append",
                   help="a triage MANIFEST.csv, glob allowed (repeatable)")
    p.add_argument("--from-index", action="append",
                   help="a video_index.py CSV (repeatable)")
    p.add_argument("--from-videos-json", action="append",
                   help="a videos.json, glob allowed (repeatable)")
    p.add_argument("--category", default="",
                   help="force a category instead of inferring it")
    p.set_defaults(fn=cmd_seed)

    # A YouTube id may begin with "-", e.g. "-Crjhc_L600", and argparse reads
    # any leading dash as a flag: `check -Crjhc_L600` died with "unrecognized
    # arguments". Inserting the end-of-options marker lets ids be passed the
    # obvious way instead of making the user remember to type "--" first.
    argv = sys.argv[1:]
    if "--" not in argv:
        for i, a in enumerate(argv):
            if a in ("check", "add", "remove"):
                head, tail = argv[:i + 1], argv[i + 1:]
                opts = [t for t in tail if t.startswith("--")]
                if not opts:
                    argv = head + ["--"] + tail
                break
    args = ap.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
