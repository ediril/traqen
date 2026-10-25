"""Application factory.

Run with a single worker (`uvicorn traqen.app:app`): the background refresher
runs inside the process.
"""

import asyncio
import ipaddress
import logging
from contextlib import asynccontextmanager, suppress
from datetime import timedelta
from urllib.parse import quote, urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from . import auth, capture, connections, web
from . import db as dbm
from .config import Settings, load_settings
from .crypto import TokenCipher
from .live import engine
from .templating import STATIC_DIR, templates

log = logging.getLogger("traqen")
REFRESH_TICK_SECONDS = 60


async def refresh_due(settings: Settings, cipher: TokenCipher) -> None:
    db = dbm.connect(settings.database_path)
    try:
        links = engine.due_links(db, timedelta(minutes=settings.refresh_minutes))
        await engine.refresh(db, settings, cipher, links)
    finally:
        db.close()


async def background_refresher(settings: Settings, cipher: TokenCipher) -> None:
    """Keep links in open traqs current."""
    while True:
        try:
            await refresh_due(settings, cipher)
        except Exception:
            log.exception("Background refresh failed")
        await asyncio.sleep(REFRESH_TICK_SECONDS)


def is_local(request: Request) -> bool:
    """From this computer, addressed by one of its own names."""
    host = urlsplit("//" + request.headers.get("host", "")).hostname
    try:
        from_here = ipaddress.ip_address(request.client.host).is_loopback
    except (AttributeError, ValueError):
        from_here = False
    return from_here and host in ("localhost", "127.0.0.1", "::1")


def create_app(settings: Settings, *, background: bool = True) -> FastAPI:
    dbm.init(settings.database_path)
    cipher = TokenCipher(settings.encryption_key)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        task = (
            asyncio.create_task(background_refresher(settings, cipher))
            if background
            else None
        )
        yield
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.cipher = cipher
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        session_cookie="traqen_session",
        max_age=60 * 60 * 24 * 30,
        same_site="lax",
        https_only=settings.secure_cookies,
    )
    if not settings.sign_in_enabled:
        # With sign-in off, whoever reaches the app is the local user, so answer
        # only this computer, under its own name. That refuses other machines,
        # and a web page that points its own domain at 127.0.0.1 (DNS
        # rebinding) to read and change your traqs as if it were Traqen.
        @app.middleware("http")
        async def local_only(request: Request, call_next):
            if not is_local(request):
                return PlainTextResponse(
                    "Sign-in is off, so Traqen only answers on this computer: "
                    f"open {settings.base_url}",
                    403,
                )
            return await call_next(request)

    app.mount(
        "/static",
        StaticFiles(directory=STATIC_DIR),
        name="static",
    )
    app.include_router(auth.router)
    app.include_router(web.router)
    app.include_router(connections.router)
    app.include_router(capture.router)

    @app.exception_handler(auth.LoginRequired)
    async def login_required(request: Request, exc: auth.LoginRequired):
        return RedirectResponse(f"/login?next={quote(exc.next_url)}", 303)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        return templates.TemplateResponse(
            request,
            "error.html",
            {"message": exc.detail, "status": exc.status_code},
            status_code=exc.status_code,
        )

    return app


def __getattr__(name: str):
    # `uvicorn traqen.app:app` builds the app from the environment on first use,
    # so importing this module (e.g. in tests) needs no configuration.
    if name == "app":
        return create_app(load_settings())
    raise AttributeError(name)
