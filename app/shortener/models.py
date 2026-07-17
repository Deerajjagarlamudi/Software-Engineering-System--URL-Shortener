from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Link(Base):
    __tablename__ = "links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    target_url: Mapped[str] = mapped_column(String(2048))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    click_count: Mapped[int] = mapped_column(Integer, default=0)

    clicks: Mapped[list["ClickEvent"]] = relationship(
        back_populates="link", cascade="all, delete-orphan"
    )

    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        expires = self.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        return utcnow() >= expires


class ClickEvent(Base):
    __tablename__ = "click_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    link_id: Mapped[int] = mapped_column(ForeignKey("links.id"), index=True)
    clicked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    referrer: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)

    link: Mapped[Link] = relationship(back_populates="clicks")
