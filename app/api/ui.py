"""Gate 8 UI routes: HTML shell and static assets."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
INDEX_HTML = WEB_DIR / "index.html"

router = APIRouter(tags=["ui"])


def _html_shell() -> HTMLResponse:
    if not INDEX_HTML.is_file():
        raise HTTPException(status_code=500, detail="UI shell is missing")
    return HTMLResponse(
        content=INDEX_HTML.read_text(encoding="utf-8"),
        media_type="text/html; charset=utf-8",
    )


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def ui_index() -> HTMLResponse:
    """Serve the Gate 8 single-page application."""
    return _html_shell()


@router.get("/jobs/{job_id}", response_class=HTMLResponse, include_in_schema=False)
def ui_job(job_id: str) -> HTMLResponse:
    """SPA shell for a specific job; the client restores state from the API."""
    if ".." in job_id or "/" in job_id or "\\" in job_id:
        raise HTTPException(status_code=404, detail="Not found")
    return _html_shell()


def mount_static(app) -> None:  # type: ignore[no-untyped-def]
    """Mount /static from app/web (CSS/JS only; HTML is served by routes)."""
    if not WEB_DIR.is_dir():
        raise RuntimeError(f"Web asset directory missing: {WEB_DIR}")
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


def security_headers_middleware(app) -> None:  # type: ignore[no-untyped-def]
    """Attach local-safe security headers to all responses."""
    from fastapi import Request

    @app.middleware("http")
    async def add_security_headers(request: Request, call_next):  # type: ignore[no-untyped-def]
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        # Local personal-use CSP: self only; blob for object-URL previews.
        csp = (
            "default-src 'self'; "
            "img-src 'self' blob:; "
            "media-src 'self' blob:; "
            "style-src 'self'; "
            "script-src 'self'; "
            "connect-src 'self'; "
            "object-src 'none'; "
            "base-uri 'self'; "
            "form-action 'self'"
        )
        response.headers.setdefault("Content-Security-Policy", csp)
        return response
