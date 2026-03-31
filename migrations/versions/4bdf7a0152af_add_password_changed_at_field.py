"""Add password_changed_at field

Revision ID: 4bdf7a0152af
Revises: 
Create Date: 2025-05-20 23:34:51.151815

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '4bdf7a0152af'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_names = inspector.get_table_names()

    if 'user' not in table_names:
        op.create_table(
            'user',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('username', sa.String(length=64), nullable=False),
            sa.Column('password_hash', sa.String(length=128), nullable=False),
            sa.Column('is_first_login', sa.Boolean(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=True),
            sa.Column('last_login', sa.DateTime(), nullable=True),
            sa.Column('password_changed_at', sa.DateTime(), nullable=True),
            sa.UniqueConstraint('username', name='uq_user_username')
        )
        return

    column_names = {column['name'] for column in inspector.get_columns('user')}
    with op.batch_alter_table('user', schema=None) as batch_op:
        if 'password_changed_at' not in column_names:
            batch_op.add_column(sa.Column('password_changed_at', sa.DateTime(), nullable=True))


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if 'user' not in inspector.get_table_names():
        return

    column_names = {column['name'] for column in inspector.get_columns('user')}
    if 'password_changed_at' in column_names:
        with op.batch_alter_table('user', schema=None) as batch_op:
            batch_op.drop_column('password_changed_at')
