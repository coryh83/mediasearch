import re
import shutil
from pathlib import Path

# A name-like segment: 1-5 words, each starting with a capital, letters/dots/apostrophes/hyphens only.
_NAME_WORD = re.compile(r"^[A-Z][A-Za-z.'\-]*$")

# File types that count as books. Covers, .nfo, checksums and the like are left where they are.
BOOK_EXTENSIONS = {".m4b", ".mp3", ".m4a", ".flac", ".ogg", ".opus", ".wav",
                   ".epub", ".pdf", ".mobi", ".azw", ".azw3", ".cbz", ".cbr"}


def _key(text: str) -> str:
    """Comparison key so 'J. R. R. Tolkien', 'J.R.R Tolkien' and 'j r r tolkien' all match."""
    return re.sub(r"[^a-z]", "", text.lower())


def _clean_author(author: str) -> str:
    author = author.strip()
    if not author or "/" in author or "\\" in author or author in (".", ".."):
        raise ValueError(f"not a usable author name: {author!r}")
    return author


def _safe_name(name: str) -> str:
    if not name or name in (".", "..") or "/" in name or "\\" in name:
        raise ValueError(f"unsafe name: {name!r}")
    return name


# ---------- author guessing ----------

_NOT_NAMES = {"unabridged", "abridged", "audiobook", "audio", "complete", "saga", "collection",
              "series", "edition", "books", "ebook", "epub", "pdf", "variable", "volume", "vol"}


def guess_author(torrent_name: str) -> str:
    """Best guess at the author from a torrent name. Returns '' if nothing looks like a name.

    The guess is only pre-filled in the form. You can change it before moving anything.
    """
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


# ---------- book titles from file names ----------

def book_title(filename: str) -> str:
    """Book title from a file name, e.g. 'LotR0 - The Hobbit (read by X).m4b' -> 'The Hobbit'."""
    stem = Path(filename).stem
    stem = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", stem)   # (read by ...), [M4B]
    stem = re.sub(r"\s+", " ", stem).strip()
    m = re.match(r"^[\w.]{1,12}\s+-\s+(.+)$", stem)       # leading code: "LotR0 - ", "01 - "
    if m:
        stem = m.group(1)
    # trailing part markers: "- Part 2", "- Disc 1", "- Book 3", "- 04"
    stem = re.sub(r"\s*-\s*(part|disc|cd|track|chapter|book|vol|volume)\s*\w+$", "", stem, flags=re.I)
    stem = re.sub(r"\s*-\s*\d+$", "", stem)
    return stem.strip(" -") or Path(filename).stem


def _book_files(folder: Path) -> list[Path]:
    return sorted(
        (p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in BOOK_EXTENSIONS),
        key=lambda p: p.name.lower(),
    )


def is_collection(complete_dir: str, name: str) -> bool:
    return (Path(complete_dir) / _safe_name(name)).is_dir()


def preview_split(complete_dir: str, name: str) -> list[dict]:
    """What a collection would be split into: [{'title': ..., 'files': n}], without moving anything."""
    folder = Path(complete_dir) / _safe_name(name)
    if not folder.is_dir():
        return []
    counts: dict[str, int] = {}
    for f in _book_files(folder):
        t = book_title(f.name)
        counts[t] = counts.get(t, 0) + 1
    return [{"title": t, "files": n} for t, n in sorted(counts.items())]


# ---------- author folders ----------

def list_authors(root_dir: str) -> list[str]:
    """Existing author folders in a library folder, for the autocomplete list."""
    root = Path(root_dir)
    if not root.is_dir():
        return []
    return sorted((p.name for p in root.iterdir() if p.is_dir()), key=str.lower)


def find_or_create_author_dir(root_dir: str, author: str) -> Path:
    """Return the existing author folder that matches, or create one with the given name."""
    author = _clean_author(author)
    root = Path(root_dir)
    if not root.is_dir():
        raise RuntimeError(f"library folder is missing: {root}")

    wanted = _key(author)
    if not wanted:
        raise ValueError(f"author name has no letters: {author!r}")
    for existing in root.iterdir():
        if existing.is_dir() and _key(existing.name) == wanted:
            return existing

    new_dir = root / author
    new_dir.mkdir()
    return new_dir


# ---------- moving ----------

def move_to_library(complete_dir: str, name: str, dest_dir: str, author: str) -> Path:
    """Move a single finished download into <dest_dir>/<Author>/<name>.

    Never overwrites anything: if the destination already has that name, it stops.
    """
    src = Path(complete_dir) / _safe_name(name)
    if not src.exists():
        raise FileNotFoundError(f"not found in the complete folder: {src}")

    author_dir = find_or_create_author_dir(dest_dir, author)
    dst = author_dir / name
    if dst.exists():
        raise RuntimeError(f"already exists in the destination: {dst}")

    shutil.move(str(src), str(dst))
    return dst


def move_collection(complete_dir: str, name: str, dest_dir: str, author: str) -> list[Path]:
    """Split a collection folder into <dest_dir>/<Author>/<Book Title>/ and move each book there.

    Checks every destination first, so nothing is moved if any file would overwrite another.
    Covers and other non-book files stay in the complete folder.
    """
    src_root = Path(complete_dir) / _safe_name(name)
    if not src_root.is_dir():
        raise FileNotFoundError(f"not found in the complete folder: {src_root}")

    files = _book_files(src_root)
    if not files:
        raise RuntimeError("no book files found in that folder")

    # Plan every destination before touching anything.
    author_dir = find_or_create_author_dir(dest_dir, author)
    plan = []
    seen = set()
    for f in files:
        dst = author_dir / book_title(f.name) / f.name
        if dst.exists() or dst in seen:
            raise RuntimeError(f"would overwrite: {dst}")
        seen.add(dst)
        plan.append((f, dst))

    moved = []
    for src, dst in plan:
        dst.parent.mkdir(exist_ok=True)
        shutil.move(str(src), str(dst))
        moved.append(dst)

    # Remove the source folders only if they are now empty.
    for d in sorted((p for p in src_root.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        if not any(d.iterdir()):
            d.rmdir()
    if src_root.exists() and not any(src_root.iterdir()):
        src_root.rmdir()
    return moved
