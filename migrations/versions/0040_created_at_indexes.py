"""Индексы created_at для списков админа и мониторинга качества.

Revision ID: 0040_created_at_indexes
Revises: 0039_commerce_constraints
"""

from alembic import op

revision = "0040_created_at_indexes"
down_revision = "0039_commerce_constraints"
branch_labels = None
depends_on = None

INDEXES = (
    ("ix_diagnoses_created_at", "diagnoses"),
    ("ix_diagnosis_jobs_created_at", "diagnosis_jobs"),
    ("ix_ai_request_logs_created_at", "ai_request_logs"),
    ("ix_product_leads_created_at", "product_leads"),
)


def upgrade() -> None:
    for name, table in INDEXES:
        op.create_index(name, table, ["created_at"])


def downgrade() -> None:
    for name, table in reversed(INDEXES):
        op.drop_index(name, table_name=table)
