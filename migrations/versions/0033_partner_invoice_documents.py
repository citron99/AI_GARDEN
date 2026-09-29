"""Add immutable partner invoice document snapshots.

Revision ID: 0033_invoice_documents
Revises: 0032_product_commerce
"""

import sqlalchemy as sa
from alembic import op

revision = "0033_invoice_documents"
down_revision = "0032_product_commerce"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("partners") as batch:
        batch.add_column(sa.Column("legal_name", sa.String(length=200), nullable=True))
        batch.add_column(
            sa.Column("registration_number", sa.String(length=80), nullable=True)
        )
        batch.add_column(sa.Column("vat_number", sa.String(length=80), nullable=True))
        batch.add_column(
            sa.Column("billing_address", sa.String(length=500), nullable=True)
        )
        batch.add_column(
            sa.Column("billing_email", sa.String(length=320), nullable=True)
        )

    with op.batch_alter_table("partner_invoices") as batch:
        batch.add_column(
            sa.Column("invoice_number", sa.String(length=40), nullable=True)
        )
        batch.add_column(sa.Column("issuer_snapshot", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("customer_snapshot", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("line_items", sa.JSON(), nullable=True))
        batch.add_column(
            sa.Column("document_version", sa.String(length=40), nullable=True)
        )
        batch.add_column(sa.Column("pdf_sha256", sa.String(length=64), nullable=True))
        batch.create_index(
            "ix_partner_invoices_invoice_number", ["invoice_number"], unique=True
        )


def downgrade() -> None:
    with op.batch_alter_table("partner_invoices") as batch:
        batch.drop_index("ix_partner_invoices_invoice_number")
        batch.drop_column("pdf_sha256")
        batch.drop_column("document_version")
        batch.drop_column("line_items")
        batch.drop_column("customer_snapshot")
        batch.drop_column("issuer_snapshot")
        batch.drop_column("invoice_number")

    with op.batch_alter_table("partners") as batch:
        batch.drop_column("billing_email")
        batch.drop_column("billing_address")
        batch.drop_column("vat_number")
        batch.drop_column("registration_number")
        batch.drop_column("legal_name")
