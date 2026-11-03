import base64
from urllib.parse import urljoin

import httpx

# Transmission status codes: 4 = downloading, 6 = seeding (finished), 0 = stopped.
STATUS_NAMES = {
    0: "Stopped", 1: "Queued to check", 2: "Checking", 3: "Queued",
    4: "Downloading", 5: "Queued to seed", 6: "Seeding",
}


async def _rpc(cfg: dict, body: dict, timeout: float = 20) -> dict:
    """Call Transmission's RPC API, handling its session-id handshake."""
    url = cfg["url"].rstrip("/") + "/transmission/rpc"
    auth = (cfg["username"], cfg["password"]) if cfg.get("username") else None

    async with httpx.AsyncClient(auth=auth, timeout=timeout) as client:
        resp = await client.post(url, json=body)
        if resp.status_code == 409:  # Transmission requires a session id handshake
            session_id = resp.headers["X-Transmission-Session-Id"]
            resp = await client.post(url, json=body, headers={"X-Transmission-Session-Id": session_id})
        resp.raise_for_status()
        return resp.json()


async def _resolve(link: str):
    """Follow Jackett's download redirects ourselves. Returns a magnet string or .torrent bytes.

    Transmission does not follow redirects, so we fetch the file here and hand it over.
    """
    async with httpx.AsyncClient(timeout=30) as client:
        for _ in range(5):
            resp = await client.get(link)
            if resp.status_code in (301, 302, 303, 307, 308):
                nxt = resp.headers.get("location", "")
                if nxt.startswith("magnet:"):
                    return nxt
                link = urljoin(link, nxt)
                continue
            resp.raise_for_status()
            return resp.content
    raise RuntimeError("too many redirects")


async def add_torrent(cfg: dict, link: str) -> str:
    """Send a magnet link or a .torrent download link to Transmission. Returns the torrent's hash."""
    if link.startswith("magnet:"):
        args = {"filename": link}
    else:
        target = await _resolve(link)
        if isinstance(target, str):
            args = {"filename": target}
        else:
            args = {"metainfo": base64.b64encode(target).decode()}
    data = await _rpc(cfg, {"method": "torrent-add", "arguments": args})
    result = data.get("result")
    if result != "success":
        raise RuntimeError(f"Transmission replied: {result}")
    added = data.get("arguments", {})
    info = added.get("torrent-added") or added.get("torrent-duplicate") or {}
    return info.get("hashString", "")


async def test_connection(cfg: dict) -> str:
    """Ask Transmission for its version. Read-only, so nothing is added."""
    data = await _rpc(cfg, {"method": "session-get", "arguments": {"fields": ["version"]}})
    if data.get("result") != "success":
        raise RuntimeError(f"Transmission replied: {data.get('result')}")
    version = data["arguments"].get("version", "unknown version")
    return f"OK: Transmission {version} responded"


async def list_torrents(cfg: dict, timeout: float = 20) -> list[dict]:
    """Return Transmission's torrents with the fields the Downloads page needs."""
    fields = ["id", "name", "hashString", "percentDone", "rateDownload", "status", "totalSize"]
    data = await _rpc(cfg, {"method": "torrent-get", "arguments": {"fields": fields}}, timeout=timeout)
    if data.get("result") != "success":
        raise RuntimeError(f"Transmission replied: {data.get('result')}")
    torrents = []
    for t in data["arguments"]["torrents"]:
        pct = t.get("percentDone", 0)
        torrents.append({
            "id": t["id"],
            "hash": t.get("hashString", ""),
            "name": t["name"],
            "pct": round(pct * 100, 1),
            "done": pct >= 1,
            "status": STATUS_NAMES.get(t.get("status"), "Unknown"),
            "rate": t.get("rateDownload", 0),
        })
    # Unfinished first, then finished, so the ones you need to act on are at the top.
    torrents.sort(key=lambda t: (t["done"], t["name"].lower()))
    return torrents


async def remove_torrent(cfg: dict, torrent_id: int) -> None:
    """Remove a torrent from Transmission. Files on disk are kept (delete-local-data is false)."""
    data = await _rpc(cfg, {
        "method": "torrent-remove",
        "arguments": {"ids": [torrent_id], "delete-local-data": False},
    })
    if data.get("result") != "success":
        raise RuntimeError(f"Transmission replied: {data.get('result')}")
