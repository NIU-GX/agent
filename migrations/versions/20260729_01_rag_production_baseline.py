"""add production RAG control plane

Revision ID: 20260729_01
Revises:
Create Date: 2026-07-29
"""

from alembic import op
import sqlalchemy as sa

revision = "20260729_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("tenants", sa.Column("id", sa.String(64), primary_key=True), sa.Column("name", sa.String(256), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("knowledge_bases", sa.Column("id", sa.String(64), primary_key=True), sa.Column("tenant_id", sa.String(64), nullable=False, index=True), sa.Column("name", sa.String(256), nullable=False), sa.Column("status", sa.String(32), nullable=False, server_default="active"), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("api_principals", sa.Column("id", sa.String(64), primary_key=True), sa.Column("tenant_id", sa.String(64), nullable=False, index=True), sa.Column("key_hash", sa.String(128), nullable=False, unique=True), sa.Column("role", sa.String(32), nullable=False, server_default="reader"), sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("knowledge_base_memberships", sa.Column("id", sa.String(64), primary_key=True), sa.Column("principal_id", sa.String(64), nullable=False, index=True), sa.Column("kb_id", sa.String(64), nullable=False, index=True), sa.Column("role", sa.String(32), nullable=False, server_default="reader"), sa.UniqueConstraint("principal_id", "kb_id", name="uq_kb_membership"))
    op.create_table("document_versions", sa.Column("id", sa.String(64), primary_key=True), sa.Column("document_id", sa.String(64), nullable=False, index=True), sa.Column("tenant_id", sa.String(64), nullable=False, index=True), sa.Column("kb_id", sa.String(64), nullable=False, index=True), sa.Column("blob_key", sa.String(1024), nullable=False), sa.Column("content_hash", sa.String(128)), sa.Column("status", sa.String(32), nullable=False, server_default="uploaded"), sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.false()), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("ingest_stage_runs", sa.Column("id", sa.String(64), primary_key=True), sa.Column("document_id", sa.String(64), nullable=False, index=True), sa.Column("document_version_id", sa.String(64), nullable=False, index=True), sa.Column("stage", sa.String(32), nullable=False, index=True), sa.Column("status", sa.String(32), nullable=False, server_default="queued"), sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"), sa.Column("payload_ref", sa.String(1024), nullable=False), sa.Column("content_hash", sa.String(128)), sa.Column("lease_until", sa.DateTime(timezone=True)), sa.Column("error_message", sa.Text()), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False), sa.UniqueConstraint("document_version_id", "stage", name="uq_version_stage"))
    op.create_table("outbox_events", sa.Column("id", sa.String(64), primary_key=True), sa.Column("topic", sa.String(128), nullable=False, index=True), sa.Column("payload_json", sa.Text(), nullable=False), sa.Column("status", sa.String(32), nullable=False, server_default="pending"), sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"), sa.Column("available_at", sa.DateTime(timezone=True), nullable=False), sa.Column("published_at", sa.DateTime(timezone=True)), sa.Column("error_message", sa.Text()), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("parent_chunks", sa.Column("id", sa.String(64), primary_key=True), sa.Column("document_version_id", sa.String(64), nullable=False, index=True), sa.Column("text", sa.Text(), nullable=False), sa.Column("source", sa.String(512), nullable=False, server_default=""))


def downgrade() -> None:
    for table in ("parent_chunks", "outbox_events", "ingest_stage_runs", "document_versions", "knowledge_base_memberships", "api_principals", "knowledge_bases", "tenants"):
        op.drop_table(table)
