"""care follow-ups

護理師追蹤管理 (Phase 1c): two new tables — ``care_follow_ups`` (open → completed | cancelled, one open
follow-up per symptom record through a partial unique index, optimistic lock ``version``, completion =
a new signed nursing assessment, chain through ``previous_follow_up_id``) and ``care_follow_up_events``
(append-only history: created / rescheduled / completed / cancelled). No existing table or row changes;
existing ``nursing_assessments.next_follow_up_at`` values are **not** converted (no data migration).

Downgrade drops both tables. It refuses while they hold any row (the follow-up history would be lost),
unless the environment variable ALLOW_DROP_CARE_FOLLOW_UPS=yes is set on purpose.

Revision ID: a9d4e2b7c5f1
Revises: e7a2c4f9d1b3
Create Date: 2026-11-12 12:00:00

"""
import os

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a9d4e2b7c5f1'
down_revision = 'e7a2c4f9d1b3'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('care_follow_ups',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('public_id', sa.String(length=36), nullable=False),
    sa.Column('patient_id', sa.Integer(), nullable=False),
    sa.Column('symptom_record_id', sa.Integer(), nullable=False),
    sa.Column('review_assessment_id', sa.Integer(), nullable=False),
    sa.Column('due_at', sa.DateTime(), nullable=False),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('created_by', sa.Integer(), nullable=False),
    sa.Column('completed_by', sa.Integer(), nullable=True),
    sa.Column('completed_at', sa.DateTime(), nullable=True),
    sa.Column('completion_assessment_id', sa.Integer(), nullable=True),
    sa.Column('cancelled_by', sa.Integer(), nullable=True),
    sa.Column('cancelled_at', sa.DateTime(), nullable=True),
    sa.Column('cancellation_reason', sa.Text(), nullable=True),
    sa.Column('previous_follow_up_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint("status IN ('open', 'completed', 'cancelled')", name=op.f('ck_care_follow_ups_status')),
    sa.CheckConstraint(
        "(status = 'open' AND completed_at IS NULL AND completed_by IS NULL AND completion_assessment_id IS NULL"
        " AND cancelled_at IS NULL AND cancelled_by IS NULL AND cancellation_reason IS NULL)"
        " OR (status = 'completed' AND completed_at IS NOT NULL AND completed_by IS NOT NULL AND completion_assessment_id IS NOT NULL"
        " AND cancelled_at IS NULL AND cancelled_by IS NULL AND cancellation_reason IS NULL)"
        " OR (status = 'cancelled' AND cancelled_at IS NOT NULL AND cancelled_by IS NOT NULL AND cancellation_reason IS NOT NULL"
        " AND completed_at IS NULL AND completed_by IS NULL AND completion_assessment_id IS NULL)",
        name=op.f('ck_care_follow_ups_status_fields')),
    sa.CheckConstraint('version >= 1', name=op.f('ck_care_follow_ups_version')),
    sa.ForeignKeyConstraint(['patient_id'], ['patient_profiles.id'], name=op.f('fk_care_follow_ups_patient_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['symptom_record_id'], ['symptom_records.id'], name=op.f('fk_care_follow_ups_symptom_record_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['review_assessment_id'], ['nursing_assessments.id'], name=op.f('fk_care_follow_ups_review_assessment_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['completion_assessment_id'], ['nursing_assessments.id'], name=op.f('fk_care_follow_ups_completion_assessment_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_care_follow_ups_created_by'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['completed_by'], ['users.id'], name=op.f('fk_care_follow_ups_completed_by'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['cancelled_by'], ['users.id'], name=op.f('fk_care_follow_ups_cancelled_by'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['previous_follow_up_id'], ['care_follow_ups.id'], name=op.f('fk_care_follow_ups_previous_follow_up_id'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_care_follow_ups')),
    sa.UniqueConstraint('public_id', name=op.f('uq_care_follow_ups_public_id')),
    )
    with op.batch_alter_table('care_follow_ups', schema=None) as batch_op:
        batch_op.create_index('uq_care_follow_ups_open_record', ['symptom_record_id'], unique=True,
                              sqlite_where=sa.text("status = 'open'"), postgresql_where=sa.text("status = 'open'"))
        batch_op.create_index('ix_care_follow_ups_patient_id_status_due_at', ['patient_id', 'status', 'due_at'], unique=False)
        batch_op.create_index('ix_care_follow_ups_status_due_at', ['status', 'due_at'], unique=False)
        batch_op.create_index('ix_care_follow_ups_symptom_record_id', ['symptom_record_id'], unique=False)

    op.create_table('care_follow_up_events',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('follow_up_id', sa.Integer(), nullable=False),
    sa.Column('event_type', sa.String(length=20), nullable=False),
    sa.Column('old_due_at', sa.DateTime(), nullable=True),
    sa.Column('new_due_at', sa.DateTime(), nullable=True),
    sa.Column('actor_id', sa.Integer(), nullable=False),
    sa.Column('occurred_at', sa.DateTime(), nullable=False),
    sa.Column('reason', sa.Text(), nullable=True),
    sa.Column('related_assessment_id', sa.Integer(), nullable=True),
    sa.Column('related_follow_up_id', sa.Integer(), nullable=True),
    sa.CheckConstraint("event_type IN ('created', 'rescheduled', 'completed', 'cancelled')", name=op.f('ck_care_follow_up_events_event_type')),
    sa.ForeignKeyConstraint(['follow_up_id'], ['care_follow_ups.id'], name=op.f('fk_care_follow_up_events_follow_up_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['actor_id'], ['users.id'], name=op.f('fk_care_follow_up_events_actor_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['related_assessment_id'], ['nursing_assessments.id'], name=op.f('fk_care_follow_up_events_related_assessment_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['related_follow_up_id'], ['care_follow_ups.id'], name=op.f('fk_care_follow_up_events_related_follow_up_id'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_care_follow_up_events')),
    )
    with op.batch_alter_table('care_follow_up_events', schema=None) as batch_op:
        batch_op.create_index('ix_care_follow_up_events_follow_up_id_id', ['follow_up_id', 'id'], unique=False)


def downgrade():
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT COUNT(*) FROM care_follow_ups")).scalar() + \
        conn.execute(sa.text("SELECT COUNT(*) FROM care_follow_up_events")).scalar()
    if rows and os.environ.get("ALLOW_DROP_CARE_FOLLOW_UPS") != "yes":
        raise RuntimeError(
            f"Refusing to downgrade: care_follow_ups / care_follow_up_events hold {rows} row(s) of follow-up history "
            "that would be deleted. Back up first; set ALLOW_DROP_CARE_FOLLOW_UPS=yes to drop them on purpose.")
    with op.batch_alter_table('care_follow_up_events', schema=None) as batch_op:
        batch_op.drop_index('ix_care_follow_up_events_follow_up_id_id')
    op.drop_table('care_follow_up_events')
    with op.batch_alter_table('care_follow_ups', schema=None) as batch_op:
        batch_op.drop_index('ix_care_follow_ups_symptom_record_id')
        batch_op.drop_index('ix_care_follow_ups_status_due_at')
        batch_op.drop_index('ix_care_follow_ups_patient_id_status_due_at')
        batch_op.drop_index('uq_care_follow_ups_open_record')
    op.drop_table('care_follow_ups')
