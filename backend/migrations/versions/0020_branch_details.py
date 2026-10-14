"""Branch details (Sl.No, address 2, country/state codes, TIN, PAN, contact person, reg no, fax, type, bin no, adcode, remarks, GSTN type).

Existing branches keep all their data; Sl.No is back-filled per organization in creation order.

Revision ID: 0020_branch_details
Revises: 0019_job_lifecycle
"""
from alembic import op
import sqlalchemy as sa

revision = "0020_branch_details"
down_revision = "0019_job_lifecycle"
branch_labels = None
depends_on = None

COLUMNS = [("sl_no", sa.Integer()), ("address2", sa.String(200)), ("country_code", sa.String(5)), ("state_code", sa.String(5)), ("tin_no", sa.String(30)), ("pan_no", sa.String(10)),
           ("contact_person", sa.String(120)), ("reg_no", sa.String(40)), ("fax", sa.String(40)), ("branch_type", sa.String(30)), ("bin_no", sa.String(40)), ("adcode", sa.String(40)),
           ("remarks", sa.String(500)), ("gstn_type", sa.String(60))]


def upgrade():
    for name, type_ in COLUMNS:
        op.add_column("org_branches", sa.Column(name, type_))
    op.execute("UPDATE org_branches b SET sl_no = r.n FROM (SELECT id, row_number() OVER (PARTITION BY customer_id ORDER BY id) AS n FROM org_branches) r WHERE r.id = b.id")


def downgrade():
    for name, _ in reversed(COLUMNS):
        op.drop_column("org_branches", name)
