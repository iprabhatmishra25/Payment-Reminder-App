from alembic import context
from app.db import Base, engine

target_metadata = Base.metadata
if context.is_offline_mode():
    context.configure(url=engine.url.render_as_string(hide_password=False), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
