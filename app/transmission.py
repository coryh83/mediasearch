import httpx


async def add_torrent(cfg: dict, link: str) -> str:
    """Send a magnet or .torrent URL to Transmission via its RPC API."""
    url = cfg["url"].rstrip("/") + "/transmission/rpc"
    auth = (cfg["username"], cfg["password"]) if cfg.get("username") else None
    body = {"method": "torrent-add", "arguments": {"filename": link}}

    async with httpx.AsyncClient(auth=auth, timeout=20) as client:
        resp = await client.post(url, json=body)
        if resp.status_code == 409:  # Transmission requires a session id handshake
            session_id = resp.headers["X-Transmission-Session-Id"]
            resp = await client.post(url, json=body, headers={"X-Transmission-Session-Id": session_id})
        resp.raise_for_status()
        result = resp.json().get("result")
        if result != "success":
            raise RuntimeError(f"Transmission replied: {result}")
        return "Sent to Transmission"
