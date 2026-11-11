"""skin photos and consents

皮膚照片 (Phase 1b): two new tables — ``symptom_photos`` (metadata of privately stored, re-encoded
photos; pending → attached to a symptom record, or discarded when never sent) and ``patient_consents``
(which version of the photo consent text a patient accepted, with the text's SHA-256, and when it was
revoked). No existing table or row is changed, nothing is backfilled. Downgrade drops the two tables and
their metadata; the stored image files are not touched by Alembic (operational rule: never downgrade once
real photos exist — docs/skin-photo-deployment.md).

Revision ID: e7a2c4f9d1b3
Revises: c3e9a1f4b7d2
Create Date: 2026-11-12 10:00:00

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e7a2c4f9d1b3'
down_revision = 'c3e9a1f4b7d2'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('symptom_photos',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('public_id', sa.String(length=36), nullable=False),
    sa.Column('patient_id', sa.Integer(), nullable=False),
    sa.Column('symptom_record_id', sa.Integer(), nullable=True),
    sa.Column('uploaded_by', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('storage_backend', sa.String(length=20), nullable=False),
    sa.Column('storage_key', sa.String(length=200), nullable=False),
    sa.Column('thumb_storage_key', sa.String(length=200), nullable=False),
    sa.Column('content_type', sa.String(length=50), nullable=False),
    sa.Column('file_size', sa.Integer(), nullable=False),
    sa.Column('width', sa.Integer(), nullable=False),
    sa.Column('height', sa.Integer(), nullable=False),
    sa.Column('thumb_file_size', sa.Integer(), nullable=False),
    sa.Column('checksum_sha256', sa.String(length=64), nullable=False),
    sa.Column('source_sha256', sa.String(length=64), nullable=False),
    sa.Column('body_site', sa.String(length=50), nullable=True),
    sa.Column('attached_at', sa.DateTime(), nullable=True),
    sa.Column('deleted_at', sa.DateTime(), nullable=True),
    sa.Column('deleted_by', sa.Integer(), nullable=True),
    sa.Column('delete_reason', sa.String(length=30), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint("status IN ('pending', 'attached', 'discarded')", name=op.f('ck_symptom_photos_status')),
    sa.CheckConstraint(
        "(status = 'attached' AND symptom_record_id IS NOT NULL AND attached_at IS NOT NULL AND deleted_at IS NULL)"
        " OR (status = 'pending' AND symptom_record_id IS NULL AND deleted_at IS NULL)"
        " OR (status = 'discarded' AND symptom_record_id IS NULL AND deleted_at IS NOT NULL AND delete_reason IS NOT NULL)",
        name=op.f('ck_symptom_photos_status_fields')),
    sa.CheckConstraint("delete_reason IS NULL OR delete_reason IN ('removed_before_submit', 'expired_unattached')",
                       name=op.f('ck_symptom_photos_delete_reason')),
    sa.ForeignKeyConstraint(['patient_id'], ['patient_profiles.id'], name=op.f('fk_symptom_photos_patient_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['symptom_record_id'], ['symptom_records.id'], name=op.f('fk_symptom_photos_symptom_record_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['uploaded_by'], ['users.id'], name=op.f('fk_symptom_photos_uploaded_by'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['deleted_by'], ['users.id'], name=op.f('fk_symptom_photos_deleted_by'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_symptom_photos')),
    sa.UniqueConstraint('public_id', name=op.f('uq_symptom_photos_public_id')),
    sa.UniqueConstraint('storage_key', name=op.f('uq_symptom_photos_storage_key')),
    sa.UniqueConstraint('thumb_storage_key', name=op.f('uq_symptom_photos_thumb_storage_key')),
    )
    with op.batch_alter_table('symptom_photos', schema=None) as batch_op:
        batch_op.create_index('ix_symptom_photos_patient_id_status_created_at', ['patient_id', 'status', 'created_at'], unique=False)
        batch_op.create_index('ix_symptom_photos_symptom_record_id', ['symptom_record_id'], unique=False)
        batch_op.create_index('ix_symptom_photos_uploaded_by_created_at', ['uploaded_by', 'created_at'], unique=False)
        batch_op.create_index('ix_symptom_photos_status_created_at', ['status', 'created_at'], unique=False)

    op.create_table('patient_consents',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('patient_id', sa.Integer(), nullable=False),
    sa.Column('consent_type', sa.String(length=30), nullable=False),
    sa.Column('version', sa.String(length=40), nullable=False),
    sa.Column('content_sha256', sa.String(length=64), nullable=False),
    sa.Column('accepted_by', sa.Integer(), nullable=False),
    sa.Column('accepted_at', sa.DateTime(), nullable=False),
    sa.Column('revoked_at', sa.DateTime(), nullable=True),
    sa.Column('revoked_by', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint("consent_type IN ('skin_photo')", name=op.f('ck_patient_consents_consent_type')),
    sa.CheckConstraint("(revoked_at IS NULL) = (revoked_by IS NULL)", name=op.f('ck_patient_consents_revoked_fields')),
    sa.ForeignKeyConstraint(['patient_id'], ['patient_profiles.id'], name=op.f('fk_patient_consents_patient_id'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['accepted_by'], ['users.id'], name=op.f('fk_patient_consents_accepted_by'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['revoked_by'], ['users.id'], name=op.f('fk_patient_consents_revoked_by'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_patient_consents')),
    )
    with op.batch_alter_table('patient_consents', schema=None) as batch_op:
        batch_op.create_index('ix_patient_consents_patient_id_consent_type_accepted_at', ['patient_id', 'consent_type', 'accepted_at'], unique=False)
        batch_op.create_index('uq_patient_consents_active', ['patient_id', 'consent_type'], unique=True,
                              sqlite_where=sa.text('revoked_at IS NULL'), postgresql_where=sa.text('revoked_at IS NULL'))


def downgrade():
    with op.batch_alter_table('patient_consents', schema=None) as batch_op:
        batch_op.drop_index('uq_patient_consents_active')
        batch_op.drop_index('ix_patient_consents_patient_id_consent_type_accepted_at')
    op.drop_table('patient_consents')
    with op.batch_alter_table('symptom_photos', schema=None) as batch_op:
        batch_op.drop_index('ix_symptom_photos_status_created_at')
        batch_op.drop_index('ix_symptom_photos_uploaded_by_created_at')
        batch_op.drop_index('ix_symptom_photos_symptom_record_id')
        batch_op.drop_index('ix_symptom_photos_patient_id_status_created_at')
    op.drop_table('symptom_photos')
