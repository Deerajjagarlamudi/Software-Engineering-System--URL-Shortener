"""Domain logic for the URL shortener."""
import re
import secrets
import string
from datetime import datetime
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.shortener.models import ClickEvent, Link

ALPHABET = string.ascii_letters + string.digits
CODE_LENGTH = 7
MAX_URL_LENGTH = 2048
MAX_COLLISION_RETRIES = 5
RESERVED_CODES = {"api", "health", "console", "docs", "openapi.json", "redoc", "static"}
ALIAS_RE = re.compile(r"^[A-Za-z0-9_-]{3,32}$")


class ShortenerError(Exception):
    status_code = 400

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class InvalidURL(ShortenerError):
    pass


class InvalidAlias(ShortenerError):
    pass


class AliasTaken(ShortenerError):
    status_code = 409


class NotFound(ShortenerError):
    status_code = 404


class LinkExpired(ShortenerError):
    status_code = 410


def validate_url(url: str) -> str:
    if len(url) > MAX_URL_LENGTH:
        raise InvalidURL(f"URL exceeds {MAX_URL_LENGTH} characters")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise InvalidURL("Only http/https URLs are allowed")
    if not parsed.netloc:
        raise InvalidURL("URL must include a host")
    return url


def generate_code(length: int = CODE_LENGTH) -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(length))


def create_link(
    session: Session,
    target_url: str,
    custom_alias: str | None = None,
    expires_at: datetime | None = None,
) -> Link:
    validate_url(target_url)

    if custom_alias is not None:
        if not ALIAS_RE.match(custom_alias):
            raise InvalidAlias("Alias must be 3-32 chars of [A-Za-z0-9_-]")
        if custom_alias.lower() in RESERVED_CODES:
            raise InvalidAlias("Alias is reserved")
        # Idempotency: same alias + same target returns the existing link.
        existing = session.scalar(select(Link).where(Link.code == custom_alias))
        if existing is not None:
            if existing.target_url == target_url:
                return existing
            raise AliasTaken("Alias already in use")
        link = Link(code=custom_alias, target_url=target_url, expires_at=expires_at)
        session.add(link)
        session.commit()
        return link

    for _ in range(MAX_COLLISION_RETRIES):
        code = generate_code()
        link = Link(code=code, target_url=target_url, expires_at=expires_at)
        session.add(link)
        try:
            session.commit()
            return link
        except IntegrityError:
            session.rollback()
    raise ShortenerError("Could not allocate a unique code; retry")


def get_link(session: Session, code: str) -> Link:
    link = session.scalar(select(Link).where(Link.code == code))
    if link is None:
        raise NotFound("Link not found")
    return link


def resolve_link(
    session: Session,
    code: str,
    referrer: str | None = None,
    user_agent: str | None = None,
) -> Link:
    link = get_link(session, code)
    if link.is_expired():
        raise LinkExpired("Link has expired")
    link.click_count += 1
    session.add(ClickEvent(link_id=link.id, referrer=referrer, user_agent=user_agent))
    session.commit()
    return link


def delete_link(session: Session, code: str) -> None:
    link = get_link(session, code)
    session.delete(link)
    session.commit()


def get_analytics(session: Session, code: str) -> dict:
    link = get_link(session, code)
    recent = (
        session.query(ClickEvent)
        .filter(ClickEvent.link_id == link.id)
        .order_by(ClickEvent.clicked_at.desc())
        .limit(20)
        .all()
    )
    return {
        "code": link.code,
        "target_url": link.target_url,
        "click_count": link.click_count,
        "created_at": link.created_at,
        "expires_at": link.expires_at,
        "recent_clicks": [
            {
                "clicked_at": c.clicked_at,
                "referrer": c.referrer,
                "user_agent": c.user_agent,
            }
            for c in recent
        ],
    }
