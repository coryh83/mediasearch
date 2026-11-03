from pathlib import Path
from urllib.parse import quote

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app import config, search, transmission
from app.auth import block_cross_site_posts, require_login

BASE = Path(__file__).parent
app = FastAPI(
    title="mediasearch",
    docs_url=None, redoc_url=None, openapi_url=None,  # no API docs page exposed
    dependencies=[Depends(require_login), Depends(block_cross_site_posts)],
)
templates = Jinja2Templates(directory=BASE / "templates")


def _merged_transmission(current: dict, url: str, username: str, password: str) -> dict:
    # A blank password field means "keep the saved one".
    return {
        "url": url.strip(),
        "username": username.strip(),
        "password": password or current["transmission"].get("password", ""),
    }


def _render_config(request: Request, tx: dict, rows: list[dict], msg: str = "", msg_ok: bool = True, saved: str = ""):
    return templates.TemplateResponse(request, "config.html", {
        "cfg": {"transmission": tx}, "rows": rows, "saved": saved,
        "msg": msg, "msg_ok": msg_ok,
        "has_password": bool(tx.get("password")),  # never send the stored password to the browser
    })


@app.get("/")
async def index(request: Request, q: str = "", msg: str = ""):
    results, errors = [], []
    if q.strip():
        results, errors = await search.search_all(config.load()["indexers"], q.strip())
    return templates.TemplateResponse(request, "index.html", {
        "q": q, "results": results, "errors": errors, "msg": msg,
    })


@app.post("/download")
async def download(q: str = Form(""), link: str = Form(...)):
    try:
        msg = await transmission.add_torrent(config.load()["transmission"], link)
    except Exception as exc:
        msg = f"Failed: {exc}"
    return RedirectResponse(f"/?q={quote(q)}&msg={quote(msg)}", status_code=303)


@app.get("/config")
async def config_page(request: Request, saved: str = ""):
    cfg = config.load()
    rows = cfg["indexers"] + [{"name": "", "url": ""}]  # one blank row; "+ Add" adds more
    return _render_config(request, cfg["transmission"], rows, saved=saved)


@app.post("/config")
async def save_config(
    transmission_url: str = Form(""),
    transmission_username: str = Form(""),
    transmission_password: str = Form(""),
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
    })
    return RedirectResponse("/config?saved=1", status_code=303)


@app.post("/config/test")
async def test_connection(
    request: Request,
    test_row: str = Form(...),  # "transmission" or the indexer row's position
    transmission_url: str = Form(""),
    transmission_username: str = Form(""),
    transmission_password: str = Form(""),
    names: list[str] = Form([]),
    urls: list[str] = Form([]),
):
    # Tests what is on the form right now, without saving anything.
    current = config.load()
    tx = _merged_transmission(current, transmission_url, transmission_username, transmission_password)
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
    return _render_config(request, tx, rows, msg=msg, msg_ok=ok)
