import os
from pathlib import Path
from urllib.parse import quote

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import auth, config, library, search, transmission, users
from app.auth import Forbidden, LoginRequired, block_cross_site_posts, require_admin, require_user

BASE = Path(__file__).parent
app = FastAPI(
    title="mediasearch",
    docs_url=None, redoc_url=None, openapi_url=None,  # no API docs page exposed
    dependencies=[Depends(block_cross_site_posts)],
)
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=BASE / "templates")

# First run only: the APP_USER / APP_PASSWORD env vars create the first admin account.
users.bootstrap(os.environ.get("APP_USER", "admin"), os.environ.get("APP_PASSWORD", ""))


@app.exception_handler(LoginRequired)
async def _to_login(request: Request, exc: LoginRequired):
    return RedirectResponse(f"/login?next={quote(request.url.path)}", status_code=303)


@app.exception_handler(Forbidden)
async def _forbidden(request: Request, exc: Forbidden):
    return HTMLResponse("<p style='font-family:system-ui;padding:2rem'>Admins only. "
                        "<a href='/'>Back to search</a></p>", status_code=403)


def _render(request: Request, name: str, user: dict | None, **ctx):
    return templates.TemplateResponse(request, name, {"user": user, **ctx})


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


def _render_config(request: Request, user: dict, tx: dict, folders: dict, rows: list[dict],
                   msg: str = "", msg_ok: bool = True, saved: str = ""):
    return _render(request, "config.html", user, **{
        "cfg": {"transmission": tx}, "folders": folders, "rows": rows, "saved": saved,
        "msg": msg, "msg_ok": msg_ok,
        "has_password": bool(tx.get("password")),  # never send the stored password to the browser
    })


def _visible(user: dict, torrent: dict) -> bool:
    """Admins see and act on everything. Other users only on torrents they added."""
    return user["admin"] or users.owner_of(torrent.get("hash", "")) == user["username"]


async def _finished_downloads(user: dict) -> list[dict]:
    """Finished torrents waiting for a decision. Returns [] if Transmission is unreachable."""
    tx = config.load()["transmission"]
    if not tx.get("url"):
        return []
    try:
        torrents = await transmission.list_torrents(tx, timeout=5)
    except Exception:
        return []
    return [t for t in torrents if t["done"] and _visible(user, t)]


# ---------- login, logout, account ----------

@app.get("/login")
async def login_page(request: Request, next: str = "/"):
    return _render(request, "login.html", None, next=next, error="")


@app.post("/login")
async def login(request: Request, username: str = Form(""), password: str = Form(""), next: str = Form("/")):
    if not users.check_login(username, password):
        return _render(request, "login.html", None, next=next,
                       error="Wrong username or password, or too many tries. Wait 15 minutes and try again.")
    target = next if next.startswith("/") and not next.startswith("//") else "/"
    response = RedirectResponse(target, status_code=303)
    response.set_cookie(auth.COOKIE, auth.make_token(username.strip().lower()),
                        max_age=auth.MAX_AGE, httponly=True, samesite="lax", path="/")
    return response


@app.post("/logout")
async def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(auth.COOKIE, path="/")
    return response


@app.get("/account")
async def account_page(request: Request, user: dict = Depends(require_user), msg: str = "", ok: str = "1"):
    return _render(request, "account.html", user, msg=msg, msg_ok=ok != "0")


@app.post("/account/password")
async def change_password(
    user: dict = Depends(require_user),
    current: str = Form(""),
    new: str = Form(""),
    confirm: str = Form(""),
):
    try:
        if new != confirm:
            raise ValueError("the new passwords do not match")
        users.change_own_password(user["username"], current, new)
        msg, ok = "Password changed.", "1"
    except ValueError as exc:
        msg, ok = str(exc), "0"
    return RedirectResponse(f"/account?msg={quote(msg)}&ok={ok}", status_code=303)


# ---------- admin: users ----------

@app.get("/users")
async def users_page(request: Request, user: dict = Depends(require_admin), msg: str = "", ok: str = "1"):
    accounts = [{"username": name, "admin": rec["admin"]} for name, rec in sorted(users.load_users().items())]
    return _render(request, "users.html", user, accounts=accounts, msg=msg, msg_ok=ok != "0")


@app.post("/users/add")
async def add_user(
    user: dict = Depends(require_admin),
    username: str = Form(""),
    password: str = Form(""),
    admin: str = Form(""),
):
    try:
        name = users.create_user(username, password, admin=bool(admin))
        msg, ok = f"Added {name}.", "1"
    except ValueError as exc:
        msg, ok = str(exc), "0"
    return RedirectResponse(f"/users?msg={quote(msg)}&ok={ok}", status_code=303)


@app.post("/users/delete")
async def delete_user(user: dict = Depends(require_admin), username: str = Form(...)):
    try:
        users.delete_user(username, user["username"])
        msg, ok = f"Removed {username}.", "1"
    except ValueError as exc:
        msg, ok = str(exc), "0"
    return RedirectResponse(f"/users?msg={quote(msg)}&ok={ok}", status_code=303)


@app.post("/users/password")
async def reset_password(user: dict = Depends(require_admin), username: str = Form(...), password: str = Form(...)):
    try:
        users.set_password(username, password)
        msg, ok = f"Password reset for {username}.", "1"
    except ValueError as exc:
        msg, ok = str(exc), "0"
    return RedirectResponse(f"/users?msg={quote(msg)}&ok={ok}", status_code=303)


# ---------- search and download ----------

@app.get("/")
async def index(request: Request, user: dict = Depends(require_user), q: str = "", msg: str = ""):
    results, errors = [], []
    if q.strip():
        results, errors = await search.search_all(config.load()["indexers"], q.strip())
    return _render(request, "index.html", user, **{
        "q": q, "results": results, "errors": errors, "msg": msg,
        "finished": await _finished_downloads(user),
    })


@app.post("/download")
async def download(user: dict = Depends(require_user), q: str = Form(""), link: str = Form(...)):
    try:
        torrent_hash = await transmission.add_torrent(config.load()["transmission"], link)
        users.record_owner(torrent_hash, user["username"])
        msg = "Sent to Transmission"
    except Exception as exc:
        msg = f"Failed: {exc}"
    return RedirectResponse(f"/?q={quote(q)}&msg={quote(msg)}", status_code=303)


# ---------- downloads ----------

@app.get("/downloads")
async def downloads_page(request: Request, user: dict = Depends(require_user), msg: str = "", ok: str = "1"):
    cfg = config.load()
    torrents, error = [], ""
    try:
        torrents = [t for t in await transmission.list_torrents(cfg["transmission"]) if _visible(user, t)]
    except Exception as exc:
        error = f"Could not reach Transmission: {exc}"
    for t in torrents:
        t["guess"] = library.guess_author(t["name"])
        t["split"] = library.preview_split(cfg["folders"]["complete"], t["name"]) if t["done"] else []
    authors = sorted(
        set(library.list_authors(cfg["folders"]["audiobooks"])) | set(library.list_authors(cfg["folders"]["ebooks"])),
        key=str.lower,
    )
    return _render(request, "downloads.html", user, **{
        "torrents": torrents, "error": error, "msg": msg, "msg_ok": ok != "0",
        "folders": cfg["folders"], "authors": authors,
    })


@app.post("/downloads/move")
async def move_download(
    user: dict = Depends(require_user),
    torrent_id: int = Form(..., alias="id"),
    target: str = Form(...),
    author: str = Form(""),
):
    cfg = config.load()
    try:
        if target not in ("audiobooks", "ebooks"):
            raise ValueError("unknown destination")
        if not author.strip():
            raise ValueError("enter the author's name first")
        torrents = await transmission.list_torrents(cfg["transmission"])
        torrent = next((t for t in torrents if t["id"] == torrent_id), None)
        if torrent is None:
            raise RuntimeError("that torrent is no longer in Transmission")
        if not _visible(user, torrent):
            raise RuntimeError("that download was started by another user")
        if not torrent["done"]:
            raise RuntimeError("that download has not finished yet")
        if library.is_collection(cfg["folders"]["complete"], torrent["name"]):
            moved = library.move_collection(
                cfg["folders"]["complete"], torrent["name"], cfg["folders"][target], author,
            )
            where = f"{len(moved)} books in {author.strip()}"
        else:
            dst = library.move_to_library(
                cfg["folders"]["complete"], torrent["name"], cfg["folders"][target], author,
            )
            where = str(dst)
    except Exception as exc:
        return RedirectResponse(f"/downloads?msg={quote('Failed: ' + str(exc))}&ok=0", status_code=303)

    # Files are moved. Now drop the torrent from Transmission so it stops seeding the old path.
    try:
        await transmission.remove_torrent(cfg["transmission"], torrent_id)
        msg, ok = f"Moved to {where}", "1"
    except Exception as exc:
        msg, ok = f"Moved to {where}, but could not remove it from Transmission: {exc}", "0"
    return RedirectResponse(f"/downloads?msg={quote(msg)}&ok={ok}", status_code=303)


@app.post("/downloads/remove")
async def remove_download(user: dict = Depends(require_user), torrent_id: int = Form(..., alias="id")):
    cfg = config.load()
    try:
        torrents = await transmission.list_torrents(cfg["transmission"])
        torrent = next((t for t in torrents if t["id"] == torrent_id), None)
        if torrent is not None and not _visible(user, torrent):
            raise RuntimeError("that download was started by another user")
        await transmission.remove_torrent(cfg["transmission"], torrent_id)
        msg, ok = "Removed from the list. Files were kept in the complete folder.", "1"
    except Exception as exc:
        msg, ok = f"Failed: {exc}", "0"
    return RedirectResponse(f"/downloads?msg={quote(msg)}&ok={ok}", status_code=303)


# ---------- admin: config ----------

@app.get("/config")
async def config_page(request: Request, user: dict = Depends(require_admin), saved: str = ""):
    cfg = config.load()
    rows = cfg["indexers"] + [{"name": "", "url": ""}]  # one blank row; "+ Add" adds more
    return _render_config(request, user, cfg["transmission"], cfg["folders"], rows, saved=saved)


@app.post("/config")
async def save_config(
    user: dict = Depends(require_admin),
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
    user: dict = Depends(require_admin),
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
    return _render_config(request, user, tx, folders, rows, msg=msg, msg_ok=ok)
