"""Initial URL shortener schema."""
from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "links",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("target_url", sa.String(2048), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("click_count", sa.Integer(), nullable=False),
    )
    op.create_index("ix_links_code", "links", ["code"], unique=True)
    op.create_table(
        "click_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("link_id", sa.Integer(), sa.ForeignKey("links.id"), nullable=False),
        sa.Column("clicked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("referrer", sa.String(2048), nullable=True),
        sa.Column("user_agent", sa.String(512), nullable=True),
    )
    op.create_index("ix_click_events_link_id", "click_events", ["link_id"])


def downgrade() -> None:
    op.drop_index("ix_click_events_link_id", table_name="click_events")
    op.drop_table("click_events")
    op.drop_index("ix_links_code", table_name="links")
    op.drop_table("links")
