"""Currency master: normalise the Default Currency flag and guard it in the database (one base currency per company).

Existing currency records keep all their data. Default Currency values such as "Yes — Organisation Base" become "Yes"; if a company
has several "Yes" rows the oldest one stays the base currency and the others become "No".

Revision ID: 0022_currency_master_rules
Revises: 0021_sequence_setting
"""
from alembic import op

revision = "0022_currency_master_rules"
down_revision = "0021_sequence_setting"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        UPDATE master_records SET data = (data::jsonb || jsonb_build_object('default_currency', CASE WHEN lower(coalesce(data->>'default_currency','')) LIKE 'yes%' THEN 'Yes' ELSE 'No' END))::json
        WHERE master_type = 'currency'
    """)
    op.execute("""
        UPDATE master_records m SET data = (m.data::jsonb || jsonb_build_object('default_currency', 'No'))::json
        FROM (SELECT id, row_number() OVER (PARTITION BY organization_id ORDER BY id) AS n FROM master_records WHERE master_type = 'currency' AND data->>'default_currency' = 'Yes') r
        WHERE r.id = m.id AND r.n > 1
    """)
    op.execute("""
        UPDATE master_records SET data = (data::jsonb || jsonb_build_object('system_default', CASE WHEN lower(coalesce(data->>'system_default','')) LIKE 'yes%' THEN 'Yes' ELSE 'No' END))::json
        WHERE master_type = 'currency'
    """)
    op.execute("""
        UPDATE master_records m SET data = (m.data::jsonb || jsonb_build_object('system_default', 'No'))::json
        FROM (SELECT id, row_number() OVER (PARTITION BY organization_id ORDER BY id) AS n FROM master_records WHERE master_type = 'currency' AND data->>'system_default' = 'Yes') r
        WHERE r.id = m.id AND r.n > 1
    """)
    op.execute("CREATE UNIQUE INDEX uq_currency_one_base ON master_records (organization_id) WHERE master_type = 'currency' AND (data->>'default_currency') = 'Yes'")
    op.execute("CREATE UNIQUE INDEX uq_currency_one_system_default ON master_records (organization_id) WHERE master_type = 'currency' AND (data->>'system_default') = 'Yes'")


def downgrade():
    op.execute("DROP INDEX IF EXISTS uq_currency_one_system_default")
    op.execute("DROP INDEX IF EXISTS uq_currency_one_base")
