from datetime import datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session
from app.shortener import service

router = APIRouter()


class CreateLinkRequest(BaseModel):
    target_url: str = Field(..., max_length=2048)
    custom_alias: str | None = None
    expires_at: datetime | None = None


class LinkResponse(BaseModel):
    code: str
    short_url: str
    target_url: str
    created_at: datetime
    expires_at: datetime | None
    click_count: int


def _to_response(link, request: Request) -> LinkResponse:
    return LinkResponse(
        code=link.code,
        short_url=str(request.base_url) + link.code,
        target_url=link.target_url,
        created_at=link.created_at,
        expires_at=link.expires_at,
        click_count=link.click_count,
    )


@router.post("/api/v1/links", response_model=LinkResponse, status_code=201)
def create_link(body: CreateLinkRequest, request: Request, session: Session = Depends(get_session)):
    link = service.create_link(session, body.target_url, body.custom_alias, body.expires_at)
    return _to_response(link, request)


@router.get("/api/v1/links/{code}", response_model=LinkResponse)
def inspect_link(code: str, request: Request, session: Session = Depends(get_session)):
    return _to_response(service.get_link(session, code), request)


@router.get("/api/v1/links/{code}/analytics")
def link_analytics(code: str, session: Session = Depends(get_session)):
    return service.get_analytics(session, code)


@router.delete("/api/v1/links/{code}", status_code=204)
def delete_link(code: str, session: Session = Depends(get_session)):
    service.delete_link(session, code)


@router.get("/health/live")
def health_live():
    return {"status": "ok"}


@router.get("/health/ready")
def health_ready(session: Session = Depends(get_session)):
    from sqlalchemy import text

    session.execute(text("SELECT 1"))
    return {"status": "ready"}


@router.get("/{code}")
def redirect(code: str, request: Request, session: Session = Depends(get_session)):
    link = service.resolve_link(
        session,
        code,
        referrer=request.headers.get("referer"),
        user_agent=request.headers.get("user-agent"),
    )
    return RedirectResponse(link.target_url, status_code=307)
