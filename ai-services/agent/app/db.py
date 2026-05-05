"""
db.py - Shared database connection for agent service
"""
import os
from psycopg import AsyncConnection
from psycopg.rows import dict_row

CHECKPOINT_DB_URI = os.getenv("CHECKPOINT_DB_URI", "").strip()


async def get_db_connection() -> AsyncConnection:
    if not CHECKPOINT_DB_URI:
        raise RuntimeError("CHECKPOINT_DB_URI is not configured")
    return await AsyncConnection.connect(
        CHECKPOINT_DB_URI,
        autocommit=True,
        row_factory=dict_row,
    )
