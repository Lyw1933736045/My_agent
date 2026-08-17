# Legacy SQLite archive

`my_agent_20260814.db` is a read-only backup of the former local SQLite runtime
database. It is not used by the application and was not migrated to PostgreSQL.

The active application database is configured with `DATABASE_URL` and is managed
by Alembic.
