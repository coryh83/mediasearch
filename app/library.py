import re
import shutil
from pathlib import Path

# A name-like segment: 1-5 words, each starting with a capital, letters/dots/apostrophes/hyphens only.
_NAME_WORD = re.compile(r"^[A-Z][A-Za-z.'\-]*$")

# File types that count as books. Covers, .nfo, checksums and the like are left where they are.
BOOK_EXTENSIONS = {".m4b", ".mp3", ".m4a", ".flac", ".ogg", ".opus", ".wav",
                   ".epub", ".pdf", ".mobi", ".azw", ".azw3", ".cbz", ".cbr"}


def _safe_name(name: str) -> str:
    if not name or name in (".", "..") or "/" in name or "\\" in name:
        raise ValueError(f"unsafe name: {name!r}")
    return name


# ---------- author guessing (used only to label search results) ----------

_NOT_NAMES = {"unabridged", "abridged", "audiobook", "audio", "complete", "saga", "collection",
              "series", "edition", "books", "ebook", "epub", "pdf", "variable", "volume", "vol"}


def guess_author(torrent_name: str) -> str:
    """Best guess at the author from a torrent name. Returns '' if nothing looks like a name."""
    text = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", torrent_name)  # drop [M4B], (Unabridged), etc.
    text = re.sub(r"\s+", " ", text).strip()

    # "Dune by Frank Herbert"
    if " by " in text:
        candidate = text.rsplit(" by ", 1)[1]
        candidate = candidate.split(" - ")[0].split(",")[0].strip()
        if _looks_like_name(candidate):
            return candidate

    # "Title - Author" or "Title - Author, Co-author" (last matching segment wins).
    # With no " - " there is nothing to split on, so no guess.
    segments = text.split(" - ")
    if len(segments) < 2:
        return ""
    for segment in reversed(segments[1:]):
        candidate = segment.split(",")[0].strip()
        if _looks_like_name(candidate):
            return candidate
    return ""


def _looks_like_name(text: str) -> bool:
    words = text.split()
    if not (1 <= len(words) <= 5) or not all(_NAME_WORD.match(w) for w in words):
        return False
    return not any(w.lower().strip(".") in _NOT_NAMES for w in words)


# ---------- saving finished downloads ----------

def _book_files(folder: Path) -> list[Path]:
    return sorted(
        (p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in BOOK_EXTENSIONS),
        key=lambda p: p.name.lower(),
    )


def save_to_complete(complete_dir: str, name: str) -> list[Path]:
    """Leave the book file(s) of a finished download directly in the complete folder, with no subfolders.

    - A single-file download is already there, so nothing moves.
    - A folder download has its book files moved up into the complete folder root.
    Every destination is checked first, so nothing is moved if any name would overwrite another file.
    Covers and other non-book files stay in their folder.
    """
    root = Path(complete_dir)
    src = root / _safe_name(name)
    if not src.exists():
        raise FileNotFoundError(f"not found in the complete folder: {src}")

    if src.is_file():
        if src.suffix.lower() not in BOOK_EXTENSIONS:
            raise RuntimeError(f"not a book file: {src.name}")
        return [src]

    files = _book_files(src)
    if not files:
        raise RuntimeError("no book files found in that folder")

    # Books with the same file name in different subfolders (e.g. "Part 1.m4b" in each book's folder)
    # get the folder path in front of the name, so "Book 1 - Part 1.m4b" is kept, not overwritten.
    counts: dict[str, int] = {}
    for f in files:
        counts[f.name.lower()] = counts.get(f.name.lower(), 0) + 1

    plan = []
    seen = set()
    for f in files:
        name = f.name
        if counts[f.name.lower()] > 1:
            parts = list(f.relative_to(src).parts[:-1])
            name = " - ".join(parts + [f.name])
        dst = root / name
        if dst.exists() or dst in seen:
            raise RuntimeError(f"would overwrite: {dst}")
        seen.add(dst)
        plan.append((f, dst))

    moved = []
    for src_file, dst in plan:
        shutil.move(str(src_file), str(dst))
        moved.append(dst)

    # Remove the folders the books came from, but only if they are now empty.
    for d in sorted((p for p in src.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        if not any(d.iterdir()):
            d.rmdir()
    if src.exists() and not any(src.iterdir()):
        src.rmdir()
    return moved
