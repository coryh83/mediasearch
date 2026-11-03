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
    rows = cfg["indexers"] + [{"name": "", "url": ""}] * 3  # spare blank rows to add more
    return templates.TemplateResponse(request, "config.html", {
        "cfg": cfg, "rows": rows, "saved": saved,
        "has_password": bool(cfg["transmission"].get("password")),  # never send the stored password to the browser
    })


@app.post("/config")
async def save_config(
    transmission_url: str = Form(""),
    transmission_username: str = Form(""),
    transmission_password: str = Form(""),
    names: list[str] = Form([]),
    urls: list[str] = Form([]),
):
    current = config.load()
    # A blank password field means "keep the saved one".
    password = transmission_password or current["transmission"].get("password", "")
    indexers = [
        {"name": n.strip(), "url": u.strip()}
        for n, u in zip(names, urls)
        if n.strip() and u.strip()
    ]
    config.save({
        "indexers": indexers,
        "transmission": {
            "url": transmission_url.strip(),
            "username": transmission_username.strip(),
            "password": password,
        },
    })
    return RedirectResponse("/config?saved=1", status_code=303)
