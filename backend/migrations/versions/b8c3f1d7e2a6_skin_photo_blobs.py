"""skin photo blobs

皮膚照片 (Phase 1b, PostgreSQL photo storage — PHOTO_STORAGE_BACKEND=database): one new table
``symptom_photo_blobs`` with the re-encoded image bytes (BYTEA) of each photo, one row for the full image and
one for the thumbnail, referencing ``symptom_photos`` (RESTRICT). ``symptom_photos`` is unchanged (metadata
only; list and history queries never read this table). No existing table or row changes. On PostgreSQL the
``data`` column uses STORAGE EXTERNAL: JPEG does not compress, so no compression is attempted.

Downgrade drops the table. It refuses while it holds any row (the images would be lost), unless the
environment variable ALLOW_DROP_PHOTO_BLOBS=yes is set on purpose.

Revision ID: b8c3f1d7e2a6
Revises: a9d4e2b7c5f1
Create Date: 2026-11-12 18:00:00

"""
import os

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b8c3f1d7e2a6'
down_revision = 'a9d4e2b7c5f1'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('symptom_photo_blobs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('photo_id', sa.Integer(), nullable=False),
    sa.Column('variant', sa.String(length=10), nullable=False),
    sa.Column('storage_key', sa.String(length=200), nullable=False),
    sa.Column('content_type', sa.String(length=50), nullable=False),
    sa.Column('byte_size', sa.Integer(), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('data', sa.LargeBinary(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.CheckConstraint("variant IN ('full', 'thumb')", name=op.f('ck_symptom_photo_blobs_variant')),
    sa.CheckConstraint("content_type = 'image/jpeg'", name=op.f('ck_symptom_photo_blobs_content_type')),
    sa.CheckConstraint('byte_size > 0 AND byte_size <= 5242880', name=op.f('ck_symptom_photo_blobs_byte_size')),
    sa.CheckConstraint('length(data) = byte_size', name=op.f('ck_symptom_photo_blobs_data_size')),
    sa.ForeignKeyConstraint(['photo_id'], ['symptom_photos.id'], name=op.f('fk_symptom_photo_blobs_photo_id'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_symptom_photo_blobs')),
    sa.UniqueConstraint('photo_id', 'variant', name=op.f('uq_symptom_photo_blobs_photo_id_variant')),
    sa.UniqueConstraint('storage_key', name=op.f('uq_symptom_photo_blobs_storage_key'))
    )
    if op.get_bind().dialect.name == 'postgresql':
        op.execute('ALTER TABLE symptom_photo_blobs ALTER COLUMN data SET STORAGE EXTERNAL')


def downgrade():
    rows = op.get_bind().execute(sa.text('SELECT COUNT(*) FROM symptom_photo_blobs')).scalar()
    if rows and os.environ.get('ALLOW_DROP_PHOTO_BLOBS') != 'yes':
        raise RuntimeError(
            f'Refusing to downgrade: symptom_photo_blobs holds {rows} stored image(s) that would be deleted. '
            'Back up first; set ALLOW_DROP_PHOTO_BLOBS=yes to drop them on purpose.')
    op.drop_table('symptom_photo_blobs')
