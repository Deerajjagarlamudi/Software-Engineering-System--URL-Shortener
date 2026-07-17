"""Application entrypoint: URL shortener APIs + orchestration APIs + console."""
import os
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app.db import init_db
from app.orchestrator.api import router as orchestrator_router
from app.shortener import service
from app.shortener.api import router as shortener_router

CONSOLE_PATH = os.path.join(os.path.dirname(__file__), "console", "templates", "console.html")


def create_app() -> FastAPI:
    app = FastAPI(title="Agentic URL Shortener", version="0.1.0")
    init_db()

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        request.state.request_id = uuid.uuid4().hex[:12]
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.exception_handler(service.ShortenerError)
    async def shortener_error_handler(request: Request, exc: service.ShortenerError):
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": type(exc).__name__, "detail": exc.message,
                     "request_id": getattr(request.state, "request_id", None)},
        )

    @app.get("/console", response_class=HTMLResponse, include_in_schema=False)
    def console():
        with open(CONSOLE_PATH) as f:
            return f.read()

    app.include_router(orchestrator_router)
    app.include_router(shortener_router)  # last: contains catch-all /{code}
    return app


app = create_app()
