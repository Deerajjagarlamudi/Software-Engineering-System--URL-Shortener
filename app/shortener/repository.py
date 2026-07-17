"""Persistence boundary for URL-shortener domain operations."""

from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.shortener.models import ClickEvent, Link


class LinkRepository:
    def __init__(self, session: Session):
        self.session = session

    def get(self, code: str) -> Link | None:
        return self.session.scalar(select(Link).where(Link.code == code))

    def add(self, link: Link) -> Link:
        self.session.add(link)
        self.session.commit()
        return link

    def increment_clicks(self, link: Link, referrer: str | None, user_agent: str | None) -> None:
        self.session.execute(
            update(Link).where(Link.id == link.id).values(click_count=Link.click_count + 1)
        )
        self.session.add(ClickEvent(link_id=link.id, referrer=referrer, user_agent=user_agent))
        self.session.commit()
        self.session.refresh(link)

    def delete(self, link: Link) -> None:
        self.session.delete(link)
        self.session.commit()

    def recent_clicks(self, link: Link, limit: int = 20) -> list[ClickEvent]:
        return (
            self.session.query(ClickEvent)
            .filter(ClickEvent.link_id == link.id)
            .order_by(ClickEvent.clicked_at.desc())
            .limit(limit)
            .all()
        )
