import httpx


async def _rpc(cfg: dict, body: dict) -> dict:
    """Call Transmission's RPC API, handling its session-id handshake."""
    url = cfg["url"].rstrip("/") + "/transmission/rpc"
    auth = (cfg["username"], cfg["password"]) if cfg.get("username") else None

    async with httpx.AsyncClient(auth=auth, timeout=20) as client:
        resp = await client.post(url, json=body)
        if resp.status_code == 409:  # Transmission requires a session id handshake
            session_id = resp.headers["X-Transmission-Session-Id"]
            resp = await client.post(url, json=body, headers={"X-Transmission-Session-Id": session_id})
        resp.raise_for_status()
        return resp.json()


async def add_torrent(cfg: dict, link: str) -> str:
    """Send a magnet or .torrent URL to Transmission."""
    data = await _rpc(cfg, {"method": "torrent-add", "arguments": {"filename": link}})
    result = data.get("result")
    if result != "success":
        raise RuntimeError(f"Transmission replied: {result}")
    return "Sent to Transmission"


async def test_connection(cfg: dict) -> str:
    """Ask Transmission for its version. Read-only, so nothing is added."""
    data = await _rpc(cfg, {"method": "session-get", "arguments": {"fields": ["version"]}})
    if data.get("result") != "success":
        raise RuntimeError(f"Transmission replied: {data.get('result')}")
    version = data["arguments"].get("version", "unknown version")
    return f"OK: Transmission {version} responded"
