"""Add audited partner invoice delivery attempts.

Revision ID: 0034_invoice_deliveries
Revises: 0033_invoice_documents
"""

import sqlalchemy as sa
from alembic import op

revision = "0034_invoice_deliveries"
down_revision = "0033_invoice_documents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "partner_invoice_deliveries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("invoice_id", sa.Integer(), nullable=False),
        sa.Column("requested_by_admin_id", sa.Integer(), nullable=True),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("recipient", sa.String(length=320), nullable=False),
        sa.Column(
            "status", sa.String(length=20), nullable=False, server_default="pending"
        ),
        sa.Column("error_type", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["invoice_id"], ["partner_invoices.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_admin_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.UniqueConstraint(
            "invoice_id", "attempt_number", name="uq_invoice_delivery_attempt"
        ),
    )
    op.create_index(
        "ix_partner_invoice_deliveries_invoice_id",
        "partner_invoice_deliveries",
        ["invoice_id"],
    )
    op.create_index(
        "ix_partner_invoice_deliveries_status",
        "partner_invoice_deliveries",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_partner_invoice_deliveries_status", table_name="partner_invoice_deliveries"
    )
    op.drop_index(
        "ix_partner_invoice_deliveries_invoice_id",
        table_name="partner_invoice_deliveries",
    )
    op.drop_table("partner_invoice_deliveries")
