"""documents raised from a job took the job's customer as their customer even for a purchase bill: point them at the party named on the document

Revision ID: 0041_fix_document_party
Revises: 0040_gstr2b
"""
from alembic import op

revision = "0041_fix_document_party"
down_revision = "0040_gstr2b"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        UPDATE documents d SET customer_id = c.id
        FROM customers c
        WHERE c.organization_id = d.organization_id AND c.name = d.party_name
          AND d.customer_id IS DISTINCT FROM c.id
    """)


def downgrade():
    pass
