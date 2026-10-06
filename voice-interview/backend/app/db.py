import asyncio
import uuid

import asyncpg

from .config import settings
from .security import hash_password


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash CHAR(64) PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_user_idx ON sessions(user_id);
CREATE TABLE IF NOT EXISTS interviews (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    job_description TEXT NOT NULL,
    experience_summary TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'active' CHECK (state IN ('active', 'complete')),
    current_turn SMALLINT NOT NULL DEFAULT 1 CHECK (current_turn BETWEEN 1 AND 4),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS interviews_user_idx ON interviews(user_id, created_at DESC);
CREATE TABLE IF NOT EXISTS question_requests (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    interview_id UUID NOT NULL REFERENCES interviews(id) ON DELETE CASCADE,
    turn SMALLINT NOT NULL CHECK (turn BETWEEN 1 AND 3),
    status TEXT NOT NULL CHECK (status IN ('running', 'success', 'failure')),
    model_name TEXT NOT NULL,
    is_cold_start BOOLEAN NOT NULL DEFAULT false,
    is_retry BOOLEAN NOT NULL DEFAULT false,
    model_ttft_ms DOUBLE PRECISION,
    first_char_ms DOUBLE PRECISION,
    speech_start_ms DOUBLE PRECISION,
    browser_error TEXT,
    browser_info TEXT,
    failure_reason TEXT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ
);
ALTER TABLE question_requests ADD COLUMN IF NOT EXISTS is_retry BOOLEAN NOT NULL DEFAULT false;
CREATE INDEX IF NOT EXISTS requests_user_idx ON question_requests(user_id, started_at DESC);
CREATE TABLE IF NOT EXISTS rounds (
    interview_id UUID NOT NULL REFERENCES interviews(id) ON DELETE CASCADE,
    turn SMALLINT NOT NULL CHECK (turn BETWEEN 1 AND 3),
    request_id UUID NOT NULL REFERENCES question_requests(id),
    question_state TEXT NOT NULL CHECK (question_state IN ('generating', 'ready')),
    question_text TEXT NOT NULL DEFAULT '',
    answer_text TEXT,
    generated_at TIMESTAMPTZ,
    answered_at TIMESTAMPTZ,
    PRIMARY KEY (interview_id, turn)
);
"""


async def open_pool() -> asyncpg.Pool:
    if not settings.pg_password:
        raise RuntimeError("PGPASSWORD is required")
    last_error = None
    for _ in range(30):
        pool = None
        try:
            pool = await asyncpg.create_pool(
                host=settings.pg_host, user=settings.pg_user,
                password=settings.pg_password, database=settings.pg_database,
                min_size=1, max_size=8,
            )
            async with pool.acquire() as conn:
                await conn.execute(SCHEMA)
                async with conn.transaction():
                    await conn.execute(
                        """UPDATE question_requests SET status = 'failure',
                           failure_reason = '服务在生成过程中重启', finished_at = now()
                           WHERE status = 'running'"""
                    )
                    await conn.execute("DELETE FROM rounds WHERE question_state = 'generating'")
                await seed_accounts(conn)
            return pool
        except (OSError, asyncpg.PostgresError) as exc:
            if pool is not None:
                await pool.close()
            last_error = exc
            await asyncio.sleep(1)
    raise RuntimeError("Database was not ready after 30 attempts") from last_error


async def seed_accounts(conn: asyncpg.Connection) -> None:
    for username, password in (
        ("demo-alice", settings.demo_password_1),
        ("demo-bob", settings.demo_password_2),
    ):
        await conn.execute(
            """INSERT INTO users (id, username, password_hash) VALUES ($1, $2, $3)
               ON CONFLICT (username) DO UPDATE SET password_hash = EXCLUDED.password_hash""",
            uuid.uuid4(), username, hash_password(password),
        )
