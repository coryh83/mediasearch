import asyncio
import xml.etree.ElementTree as ET

import httpx

TORZNAB_ATTR = "{http://torznab.com/schemas/2015/feed}attr"

# Torznab category IDs: 3030 = Audio/Audiobook, 7000 = Books (ebooks).
# Only these are searched, so results stay limited to books and audiobooks.
BOOK_CATEGORIES = "3030,7000"


def human_size(num: int) -> str:
    size = float(num)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return str(num)


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

        size = int(item.findtext("size") or 0)
        seeders = 0
        for attr in item.iter(TORZNAB_ATTR):
            if attr.get("name") == "seeders":
                try:
                    seeders = int(attr.get("value") or 0)
                except ValueError:
                    pass
            if attr.get("name") == "size" and not size:
                size = int(attr.get("value") or 0)

        results.append({
            "title": item.findtext("title") or "(untitled)",
            "link": link,
            "size": size,
            "size_human": human_size(size),
            "seeders": seeders,
            "indexer": indexer_name,
        })
    return results


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
