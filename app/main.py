from pathlib import Path
from urllib.parse import quote

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import config, library, search, transmission
from app.auth import block_cross_site_posts, require_login

BASE = Path(__file__).parent
app = FastAPI(
    title="mediasearch",
    docs_url=None, redoc_url=None, openapi_url=None,  # no API docs page exposed
    dependencies=[Depends(require_login), Depends(block_cross_site_posts)],
)
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=BASE / "templates")


def _merged_transmission(current: dict, url: str, username: str, password: str) -> dict:
    # A blank password field means "keep the saved one".
    return {
        "url": url.strip(),
        "username": username.strip(),
        "password": password or current["transmission"].get("password", ""),
    }


def _folders(complete: str, audiobooks: str, ebooks: str) -> dict:
    return {
        "complete": complete.strip(),
        "audiobooks": audiobooks.strip(),
        "ebooks": ebooks.strip(),
    }


def _render_config(request: Request, tx: dict, folders: dict, rows: list[dict],
                   msg: str = "", msg_ok: bool = True, saved: str = ""):
    return templates.TemplateResponse(request, "config.html", {
        "cfg": {"transmission": tx}, "folders": folders, "rows": rows, "saved": saved,
        "msg": msg, "msg_ok": msg_ok,
        "has_password": bool(tx.get("password")),  # never send the stored password to the browser
    })


async def _finished_downloads() -> list[dict]:
    """Finished torrents waiting for a decision. Returns [] if Transmission is unreachable."""
    tx = config.load()["transmission"]
    if not tx.get("url"):
        return []
    try:
        return [t for t in await transmission.list_torrents(tx, timeout=5) if t["done"]]
    except Exception:
        return []


@app.get("/")
async def index(request: Request, q: str = "", msg: str = ""):
    results, errors = [], []
    if q.strip():
        results, errors = await search.search_all(config.load()["indexers"], q.strip())
    return templates.TemplateResponse(request, "index.html", {
        "q": q, "results": results, "errors": errors, "msg": msg,
        "finished": await _finished_downloads(),
    })


@app.post("/download")
async def download(q: str = Form(""), link: str = Form(...)):
    try:
        msg = await transmission.add_torrent(config.load()["transmission"], link)
    except Exception as exc:
        msg = f"Failed: {exc}"
    return RedirectResponse(f"/?q={quote(q)}&msg={quote(msg)}", status_code=303)


@app.get("/downloads")
async def downloads_page(request: Request, msg: str = "", ok: str = "1"):
    cfg = config.load()
    torrents, error = [], ""
    try:
        torrents = await transmission.list_torrents(cfg["transmission"])
    except Exception as exc:
        error = f"Could not reach Transmission: {exc}"
    return templates.TemplateResponse(request, "downloads.html", {
        "torrents": torrents, "error": error, "msg": msg, "msg_ok": ok != "0",
        "folders": cfg["folders"],
    })


@app.post("/downloads/move")
async def move_download(torrent_id: int = Form(..., alias="id"), target: str = Form(...)):
    cfg = config.load()
    try:
        if target not in ("audiobooks", "ebooks"):
            raise ValueError("unknown destination")
        torrents = await transmission.list_torrents(cfg["transmission"])
        torrent = next((t for t in torrents if t["id"] == torrent_id), None)
        if torrent is None:
            raise RuntimeError("that torrent is no longer in Transmission")
        if not torrent["done"]:
            raise RuntimeError("that download has not finished yet")
        dst = library.move_to_library(cfg["folders"]["complete"], torrent["name"], cfg["folders"][target])
    except Exception as exc:
        return RedirectResponse(f"/downloads?msg={quote('Failed: ' + str(exc))}&ok=0", status_code=303)

    # Files are moved. Now drop the torrent from Transmission so it stops seeding the old path.
    try:
        await transmission.remove_torrent(cfg["transmission"], torrent_id)
        msg, ok = f"Moved to {dst}", "1"
    except Exception as exc:
        msg, ok = f"Moved to {dst}, but could not remove it from Transmission: {exc}", "0"
    return RedirectResponse(f"/downloads?msg={quote(msg)}&ok={ok}", status_code=303)


@app.post("/downloads/remove")
async def remove_download(torrent_id: int = Form(..., alias="id")):
    cfg = config.load()
    try:
        await transmission.remove_torrent(cfg["transmission"], torrent_id)
        msg, ok = "Removed from the list. Files were kept in the complete folder.", "1"
    except Exception as exc:
        msg, ok = f"Failed: {exc}", "0"
    return RedirectResponse(f"/downloads?msg={quote(msg)}&ok={ok}", status_code=303)


@app.get("/config")
async def config_page(request: Request, saved: str = ""):
    cfg = config.load()
    rows = cfg["indexers"] + [{"name": "", "url": ""}]  # one blank row; "+ Add" adds more
    return _render_config(request, cfg["transmission"], cfg["folders"], rows, saved=saved)


@app.post("/config")
async def save_config(
    transmission_url: str = Form(""),
    transmission_username: str = Form(""),
    transmission_password: str = Form(""),
    complete_dir: str = Form(""),
    audiobook_dir: str = Form(""),
    ebook_dir: str = Form(""),
    names: list[str] = Form([]),
    urls: list[str] = Form([]),
):
    current = config.load()
    indexers = [
        {"name": n.strip(), "url": u.strip()}
        for n, u in zip(names, urls)
        if n.strip() and u.strip()
    ]
    config.save({
        "indexers": indexers,
        "transmission": _merged_transmission(current, transmission_url, transmission_username, transmission_password),
        "folders": _folders(complete_dir, audiobook_dir, ebook_dir),
    })
    return RedirectResponse("/config?saved=1", status_code=303)


@app.post("/config/test")
async def test_connection(
    request: Request,
    test_row: str = Form(...),  # "transmission" or the indexer row's position
    transmission_url: str = Form(""),
    transmission_username: str = Form(""),
    transmission_password: str = Form(""),
    complete_dir: str = Form(""),
    audiobook_dir: str = Form(""),
    ebook_dir: str = Form(""),
    names: list[str] = Form([]),
    urls: list[str] = Form([]),
):
    # Tests what is on the form right now, without saving anything.
    current = config.load()
    tx = _merged_transmission(current, transmission_url, transmission_username, transmission_password)
    folders = _folders(complete_dir, audiobook_dir, ebook_dir)
    rows = [{"name": n, "url": u} for n, u in zip(names, urls)]
    try:
        if test_row == "transmission":
            msg = await transmission.test_connection(tx)
        else:
            row = rows[int(test_row)]
            if not row["url"].strip():
                raise ValueError("this row has no URL")
            msg = await search.test_indexer({"name": row["name"], "url": row["url"].strip()})
        ok = True
    except Exception as exc:
        msg, ok = f"Failed: {exc}", False
    if not rows:
        rows = [{"name": "", "url": ""}]
    return _render_config(request, tx, folders, rows, msg=msg, msg_ok=ok)
