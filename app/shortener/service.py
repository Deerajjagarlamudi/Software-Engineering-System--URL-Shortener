"""URL-shortener domain logic with validation and persistence isolation."""

from __future__ import annotations

import os
import re
import secrets
import string
import threading
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.shortener.models import Link
from app.shortener.repository import LinkRepository

ALPHABET = string.ascii_letters + string.digits
CODE_LENGTH = 7
MAX_URL_LENGTH = 2048
MAX_COLLISION_RETRIES = 5
ANALYTICS_RECENT_LIMIT = 20
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


class RateLimited(ShortenerError):
    status_code = 429


class NotFound(ShortenerError):
    status_code = 404


class LinkExpired(ShortenerError):
    status_code = 410


class CreationRateLimiter:
    """Single-process demo limiter; use a Redis/gateway adapter when scaled out."""

    def __init__(self) -> None:
        self.limit = int(os.environ.get("LINK_CREATE_RATE_LIMIT", "60"))
        self.window = float(os.environ.get("LINK_CREATE_RATE_WINDOW_SECONDS", "60"))
        self._events: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def check(self, client_key: str) -> None:
        now = time.monotonic()
        with self._lock:
            events = [
                stamp for stamp in self._events.get(client_key, []) if now - stamp < self.window
            ]
            if len(events) >= self.limit:
                self._events[client_key] = events
                raise RateLimited("link creation rate exceeded")
            events.append(now)
            self._events[client_key] = events


creation_limiter = CreationRateLimiter()


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def validate_url(url: str) -> str:
    normalized = url.strip()
    if not normalized or len(normalized) > MAX_URL_LENGTH:
        raise InvalidURL(f"URL must be 1-{MAX_URL_LENGTH} characters")
    if any(ord(char) < 32 or ord(char) == 127 for char in normalized):
        raise InvalidURL("URL contains control characters")
    parsed = urlsplit(normalized)
    if parsed.scheme not in ("http", "https"):
        raise InvalidURL("Only http/https URLs are allowed")
    if not parsed.hostname or parsed.username or parsed.password:
        raise InvalidURL("URL must include a credential-free host")
    return urlunsplit(
        (parsed.scheme.lower(), parsed.netloc, parsed.path, parsed.query, parsed.fragment)
    )


def generate_code(length: int = CODE_LENGTH) -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(length))


def create_link(
    session: Session,
    target_url: str,
    custom_alias: str | None = None,
    expires_at: datetime | None = None,
) -> Link:
    target_url = validate_url(target_url)
    expires_at = _utc(expires_at)
    if expires_at is not None and expires_at <= datetime.now(timezone.utc):
        raise InvalidURL("expires_at must be in the future")
    repo = LinkRepository(session)

    if custom_alias is not None:
        if not ALIAS_RE.fullmatch(custom_alias) or custom_alias.lower() in RESERVED_CODES:
            raise InvalidAlias("Alias must be 3-32 chars of [A-Za-z0-9_-] and not reserved")
        existing = repo.get(custom_alias)
        if existing is not None:
            if existing.target_url == target_url and _utc(existing.expires_at) == expires_at:
                return existing
            raise AliasTaken("Alias already in use")
        return repo.add(Link(code=custom_alias, target_url=target_url, expires_at=expires_at))

    for _ in range(MAX_COLLISION_RETRIES):
        link = Link(code=generate_code(), target_url=target_url, expires_at=expires_at)
        session.add(link)
        try:
            session.commit()
            return link
        except IntegrityError:
            session.rollback()
    raise ShortenerError("Could not allocate a unique code; retry")


def get_link(session: Session, code: str) -> Link:
    link = LinkRepository(session).get(code)
    if link is None:
        raise NotFound("Link not found")
    return link


def resolve_link(
    session: Session, code: str, referrer: str | None = None, user_agent: str | None = None
) -> Link:
    link = get_link(session, code)
    if link.is_expired():
        raise LinkExpired("Link has expired")
    LinkRepository(session).increment_clicks(link, referrer, user_agent)
    return link


def delete_link(session: Session, code: str) -> None:
    LinkRepository(session).delete(get_link(session, code))


def get_analytics(session: Session, code: str) -> dict:
    repo = LinkRepository(session)
    link = get_link(session, code)
    recent = repo.recent_clicks(link, ANALYTICS_RECENT_LIMIT)
    return {
        "code": link.code,
        "target_url": link.target_url,
        "click_count": link.click_count,
        "created_at": link.created_at,
        "expires_at": link.expires_at,
        "recent_clicks": [
            {"clicked_at": c.clicked_at, "referrer": c.referrer, "user_agent": c.user_agent}
            for c in recent
        ],
        "retention": {
            "recent_click_limit": ANALYTICS_RECENT_LIMIT,
            "raw_metadata": "retained with each recent event",
        },
    }
