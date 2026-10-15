"""job stages simplified to Draft / Confirmed / On Transit / Closed / Reopened

Revision ID: 0027_simple_job_stages
Revises: 0026_job_booking_link
"""
from alembic import op

revision = "0027_simple_job_stages"
down_revision = "0026_job_booking_link"
branch_labels = None
depends_on = None

TRANSIT = "('Departed / Sailed','In Transit','Arrived','Delivery In Progress','Delivered','Financials Pending','Ready for Closure')"


def _map(table, col):
    op.execute(f"""UPDATE {table} SET {col} = CASE
        WHEN {col} IN {TRANSIT} THEN 'On Transit'
        WHEN {col} IN ('Booking Confirmed','Documentation In Progress','Documentation Completed','Cargo Received','Customs In Progress','Customs Cleared') THEN 'Confirmed'
        WHEN {col} IN ('Job Created','Booking In Progress') THEN 'Draft'
        ELSE {col} END""")


def upgrade():
    op.alter_column("shipments", "job_stage", server_default="Draft")
    _map("shipments", "job_stage")
    # the stage history is an immutable audit log: old entries keep the names that were used at the time
    # jobs that came from a booking or an accepted quotation are confirmed; closed / reopened jobs show that
    op.execute("UPDATE shipments SET job_stage = 'Confirmed' WHERE job_stage = 'Draft' AND (booking_id IS NOT NULL OR status NOT IN ('Draft'))")
    op.execute("UPDATE shipments SET job_stage = 'Reopened' WHERE closure_status = 'REOPENED'")
    op.execute("UPDATE shipments SET job_stage = 'Closed' WHERE closure_status = 'CLOSED'")


def downgrade():
    op.alter_column("shipments", "job_stage", server_default="Job Created")
