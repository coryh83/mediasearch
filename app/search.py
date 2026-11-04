import asyncio
import hashlib
import re
import xml.etree.ElementTree as ET
from collections import OrderedDict

import httpx

from app import transmission
from app.library import guess_author

TORZNAB_ATTR = "{http://torznab.com/schemas/2015/feed}attr"

# Torznab category IDs: 3030 = Audio/Audiobook, 7000 = Books (ebooks).
# Only these are searched, so results stay limited to books and audiobooks.
BOOK_CATEGORIES = "3030,7000"

# Known formats, looked for in the torrent name.
AUDIO_FORMATS = ["M4B", "MP3", "M4A", "FLAC", "OGG", "OPUS", "AAC", "WMA"]
EBOOK_FORMATS = ["EPUB", "PDF", "MOBI", "AZW3", "AZW", "CBZ", "CBR", "DJVU", "FB2", "DOCX", "TXT"]
BITRATE = re.compile(r"\b(\d{2,3})\s?kbps\b", re.IGNORECASE)

# Limits for the file list read from a .torrent file.
MAX_TORRENT_BYTES = 10 * 1024 * 1024
MAX_FILES_SHOWN = 200

# Results are referred to by a short key, so the browser never sends a raw indexer URL back to us.
_LINKS: "OrderedDict[str, str]" = OrderedDict()
_LINK_CAP = 2000


def remember(link: str) -> str:
    key = hashlib.sha256(link.encode()).hexdigest()[:16]
    _LINKS[key] = link
    _LINKS.move_to_end(key)
    while len(_LINKS) > _LINK_CAP:
        _LINKS.popitem(last=False)
    return key


def link_for(key: str) -> str | None:
    return _LINKS.get(key)


def human_size(num: int) -> str:
    size = float(num)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return str(num)


def title_facts(title: str) -> dict:
    """Pull bitrate, abridgement and formats out of a torrent name, e.g. '[M4B 64kbps Unabridged]'."""
    upper = title.upper()
    facts = {"bitrate": "", "abridged": "", "formats": []}
    m = BITRATE.search(title)
    if m:
        facts["bitrate"] = f"{m.group(1)} kbps"
    if re.search(r"\bUNABRIDGED\b", upper):
        facts["abridged"] = "Unabridged"
    elif re.search(r"\bABRIDGED\b", upper):
        facts["abridged"] = "Abridged"
    facts["formats"] = [f for f in AUDIO_FORMATS + EBOOK_FORMATS if re.search(rf"\b{f}\b", upper)]
    return facts


def parse_torznab(indexer_name: str, xml_bytes: bytes) -> list[dict]:
    root = ET.fromstring(xml_bytes)
    results = []
    for item in root.iter("item"):
        link = item.findtext("link") or ""
        enclosure = item.find("enclosure")
        if not link and enclosure is not None:
            link = enclosure.get("url", "")
        if not link:
            continue

        title = item.findtext("title") or "(untitled)"
        size = int(item.findtext("size") or 0)
        seeders = 0
        cats = []
        attrs = {}
        for attr in item.iter(TORZNAB_ATTR):
            name, value = attr.get("name", ""), attr.get("value") or ""
            if name == "seeders":
                try:
                    seeders = int(value or 0)
                except ValueError:
                    pass
            if name == "size" and not size:
                size = int(value or 0)
            if name == "category":
                try:
                    cats.append(int(value or 0))
                except ValueError:
                    pass
            if name and name != "category":
                attrs.setdefault(name, value)

        facts = title_facts(title)
        author = attrs.get("author", "") or guess_author(title)
        description = (item.findtext("description") or "").strip()
        if len(description) > 600:
            description = description[:600].rstrip() + "…"

        results.append({
            "key": remember(link),
            "link": link,
            "title": title,
            "size": size,
            "size_human": human_size(size),
            "seeders": seeders,
            "indexer": indexer_name,
            "kind": kind_from_categories(cats),
            "author": author,
            "quality": " · ".join(x for x in [facts["bitrate"], facts["abridged"]] if x),
            "formats": facts["formats"],
            "description": description,
            "info": _info_rows(item, attrs),
        })
    return results


def _info_rows(item, attrs: dict) -> list[tuple[str, str]]:
    """Label/value pairs for the expanded tile. Only rows the indexer actually sent are kept."""
    rows = [
        ("Publisher", attrs.get("publisher", "")),
        ("Year", attrs.get("year", "")),
        ("Language", attrs.get("language", "")),
        ("Pages", attrs.get("pages", "")),
        ("Grabs", attrs.get("grabs", "")),
        ("Files", attrs.get("files", "")),
        ("Uploaded", item.findtext("pubDate") or attrs.get("publishdate", "")),
        ("Info hash", attrs.get("infohash", "")),
    ]
    return [(label, value) for label, value in rows if value and value != "0"]


def kind_from_categories(cats: list[int]) -> str:
    """Map Torznab category IDs to 'audiobook', 'ebook', or 'other'."""
    if any(3000 <= c < 3100 for c in cats) and 3030 in cats:
        return "audiobook"
    if any(7000 <= c < 8000 for c in cats):
        return "ebook"
    return "other"


async def _query_one(client: httpx.AsyncClient, indexer: dict, q: str):
    try:
        resp = await client.get(indexer["url"], params={"t": "search", "q": q, "cat": BOOK_CATEGORIES})
        resp.raise_for_status()
        return parse_torznab(indexer["name"], resp.content), None
    except Exception as exc:  # one bad indexer should not break the search
        return [], f"{indexer['name']}: {exc}"


async def search_all(indexers: list[dict], q: str):
    if not indexers:
        return [], ["No indexers configured. Add some on the Config page."]
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        outcomes = await asyncio.gather(*(_query_one(client, i, q) for i in indexers))

    results, errors = [], []
    for found, err in outcomes:
        results.extend(found)
        if err:
            errors.append(err)
    results.sort(key=lambda r: r["seeders"], reverse=True)
    return results, errors


async def test_indexer(indexer: dict) -> str:
    """Ask the feed for its capabilities (t=caps). Read-only; no search is run."""
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        resp = await client.get(indexer["url"], params={"t": "caps"})
    resp.raise_for_status()
    root = ET.fromstring(resp.content)
    if root.tag != "caps":
        raise RuntimeError("URL responded, but it is not a Torznab feed")
    server = root.find("server")
    title = server.get("title", "indexer") if server is not None else "indexer"
    return f"OK: {title} responded"


# ---------- file list, read from the .torrent file ----------

def bdecode(data: bytes):
    """Decode bencoded data (the .torrent file format). Raises ValueError if it is not valid."""
    def parse(i: int):
        c = data[i:i + 1]
        if c == b"i":
            end = data.index(b"e", i)
            return int(data[i + 1:end]), end + 1
        if c in (b"l", b"d"):
            out, i = ([], i + 1) if c == b"l" else ({}, i + 1)
            while data[i:i + 1] != b"e":
                if c == b"l":
                    value, i = parse(i)
                    out.append(value)
                else:
                    key, i = parse(i)
                    value, i = parse(i)
                    out[key] = value
            return out, i + 1
        if c.isdigit():
            colon = data.index(b":", i)
            length = int(data[i:colon])
            start = colon + 1
            return data[start:start + length], start + length
        raise ValueError("not a torrent file")

    value, _ = parse(0)
    return value


def _text(raw: bytes) -> str:
    return raw.decode("utf-8", errors="replace")


async def file_list(key: str) -> dict:
    """Files inside a result's torrent. Returns {'files', 'total', 'types', 'note'}.

    Fetches the .torrent file from the indexer. A magnet link has no file list until Transmission
    fetches its metadata, so that case returns a note instead.
    """
    link = link_for(key)
    if link is None:
        raise KeyError("unknown result")
    empty = {"files": [], "total": 0, "types": "", "note": ""}

    target = await transmission._resolve(link)
    if isinstance(target, str):
        return {**empty, "note": "This result is a magnet link, so its file list is not known until the torrent loads."}
    if len(target) > MAX_TORRENT_BYTES:
        raise ValueError("the .torrent file is too large")

    info = bdecode(target).get(b"info", {})
    if b"files" in info:
        entries = [
            ("/".join(_text(p) for p in f.get(b"path", [])), f.get(b"length", 0))
            for f in info[b"files"]
        ]
    else:
        entries = [(_text(info.get(b"name", b"")), info.get(b"length", 0))]
    entries.sort(key=lambda e: e[0].lower())

    counts: dict[str, int] = {}
    for path, _ in entries:
        name = path.rsplit("/", 1)[-1]
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if ext:
            counts[ext] = counts.get(ext, 0) + 1
    types = " · ".join(f"{ext} ×{n}" for ext, n in sorted(counts.items(), key=lambda kv: -kv[1]))

    return {
        "files": [{"path": p, "size": human_size(s)} for p, s in entries[:MAX_FILES_SHOWN]],
        "total": len(entries),
        "types": types,
        "note": "",
    }
