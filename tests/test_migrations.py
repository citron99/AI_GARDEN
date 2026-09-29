from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


def test_migrations_upgrade_and_downgrade(tmp_path):
    database_path = (tmp_path / "migrations.db").as_posix()
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")

    command.upgrade(config, "head")

    engine = create_engine(f"sqlite:///{database_path}")
    inspector = inspect(engine)
    assert {
        "alembic_version",
        "users",
        "gardens",
        "plants",
        "plant_photos",
        "diagnoses",
        "diagnosis_photos",
        "diagnosis_questions",
        "diagnosis_answers",
        "diagnosis_revisions",
        "diagnosis_feedback",
        "ai_request_logs",
        "care_events",
        "reminders",
        "diagnosis_jobs",
        "partners",
        "products",
        "product_leads",
        "partner_members",
        "subscriptions",
        "billing_events",
        "telegram_accounts",
        "telegram_link_tokens",
        "telegram_updates",
        "knowledge_sources",
        "knowledge_chunks",
        "user_notifications",
        "product_recommendation_rules",
        "auth_sessions",
        "admin_audit_logs",
        "account_action_tokens",
        "partner_invoices",
        "partner_invoice_deliveries",
        "partner_payments",
        "regulated_product_registrations",
        "ai_safety_adjustments",
        "account_deletion_requests",
        "storage_deletion_outbox",
        "storage_migration_records",
        "catalog_taxonomy_aliases",
    }.issubset(set(inspector.get_table_names()))
    assert inspector.get_pk_constraint("diagnosis_photos")["constrained_columns"] == [
        "diagnosis_id",
        "photo_id",
    ]
    assert "is_admin" in {column["name"] for column in inspector.get_columns("users")}
    assert "size_bytes" in {
        column["name"] for column in inspector.get_columns("plant_photos")
    }
    assert "previous_refresh_token_hash" in {
        column["name"] for column in inspector.get_columns("auth_sessions")
    }
    feedback_unique = {
        tuple(item["column_names"])
        for item in inspector.get_unique_constraints("diagnosis_feedback")
    }
    assert ("diagnosis_id", "user_id") in feedback_unique
    assert {"billing_plan", "monthly_fee_cents", "confirmed_lead_price_cents"}.issubset(
        {column["name"] for column in inspector.get_columns("partners")}
    )
    assert {
        "legal_name",
        "registration_number",
        "vat_number",
        "billing_address",
        "billing_email",
    }.issubset({column["name"] for column in inspector.get_columns("partners")})
    assert {
        "invoice_number",
        "issuer_snapshot",
        "customer_snapshot",
        "line_items",
        "document_version",
        "snapshot_sha256",
        "pdf_sha256",
        "pdf_storage_backend",
        "pdf_storage_key",
        "pdf_file_path",
        "pdf_size_bytes",
        "pdf_content_type",
        "pdf_generator_version",
        "cancellation_number",
        "cancellation_reason",
        "cancellation_document_version",
        "cancellation_snapshot_sha256",
        "cancellation_pdf_sha256",
        "cancellation_storage_backend",
        "cancellation_storage_key",
        "cancellation_file_path",
        "cancellation_size_bytes",
        "cancellation_content_type",
        "cancellation_generator_version",
        "cancelled_at",
        "cancelled_by_admin_id",
    }.issubset({column["name"] for column in inspector.get_columns("partner_invoices")})
    assert {
        "invoice_id",
        "requested_by_admin_id",
        "attempt_number",
        "recipient",
        "status",
        "error_type",
        "completed_at",
    }.issubset(
        {
            column["name"]
            for column in inspector.get_columns("partner_invoice_deliveries")
        }
    )
    assert {
        "invoice_id",
        "source",
        "external_id",
        "booking_date",
        "amount_cents",
        "currency",
        "reference",
        "status",
        "resolution_note",
        "resolved_by_admin_id",
        "resolved_at",
    }.issubset({column["name"] for column in inspector.get_columns("partner_payments")})
    assert {"sku", "image_url", "price_cents", "currency", "in_stock"}.issubset(
        {column["name"] for column in inspector.get_columns("products")}
    )
    assert "postback_secret_hash" in {
        column["name"] for column in inspector.get_columns("partners")
    }
    assert {
        "status",
        "partner_reference",
        "invoice_id",
        "billable_amount_cents",
    }.issubset({column["name"] for column in inspector.get_columns("product_leads")})
    assert {"click_id", "idempotency_key", "redirected_at", "redirect_count"}.issubset(
        {column["name"] for column in inspector.get_columns("product_leads")}
    )
    assert "registry_entry_id" in {
        column["name"]
        for column in inspector.get_columns("product_recommendation_rules")
    }
    assert "taxon_id" in {column["name"] for column in inspector.get_columns("plants")}
    assert {"plant_taxon_id", "problem_code", "country_code"}.issubset(
        {
            column["name"]
            for column in inspector.get_columns("product_recommendation_rules")
        }
    )
    assert {"next_review_at", "review_role", "usage_basis"}.issubset(
        {column["name"] for column in inspector.get_columns("knowledge_sources")}
    )
    assert {
        "ck_invoice_total_consistent",
        "ck_invoice_currency_iso",
        "ck_invoice_status",
    }.issubset(
        {
            constraint["name"]
            for constraint in inspector.get_check_constraints("partner_invoices")
        }
    )
    assert "ck_invoice_delivery_status" in {
        constraint["name"]
        for constraint in inspector.get_check_constraints("partner_invoice_deliveries")
    }
    assert "ck_partner_payment_amount_positive" in {
        constraint["name"]
        for constraint in inspector.get_check_constraints("partner_payments")
    }
    engine.dispose()

    command.downgrade(config, "base")
    engine = create_engine(f"sqlite:///{database_path}")
    assert set(inspect(engine).get_table_names()) == {"alembic_version"}
    engine.dispose()
