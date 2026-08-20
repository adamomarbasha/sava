"""Postgres integration.

Everything else in the suite runs on SQLite, which is fine for logic but proves
nothing about the two things production actually depends on:

  * `FOR UPDATE SKIP LOCKED` — the claim path that lets many workers share one
    queue without double-processing. SQLite never exercises it; it takes the
    serialised-write branch instead.
  * pgvector + HNSW — the reason search does not have to load every embedding
    into Python.

Both are Postgres-only code paths guarded by `IS_POSTGRES`, so on SQLite they
are literally never run. A test suite that is green without touching them is
green about the wrong database.

Skipped unless `SAVA_TEST_POSTGRES_URL` points at a reachable server with the
`vector` extension available. Nothing here needs a hosted database — a local
Postgres is enough.
"""
from __future__ import annotations

import os
import threading

import pytest

PG_URL = os.getenv("SAVA_TEST_POSTGRES_URL")

pytestmark = pytest.mark.skipif(
    not PG_URL, reason="set SAVA_TEST_POSTGRES_URL to run Postgres integration tests")


@pytest.fixture(scope="module")
def pg_engine():
    from sqlalchemy import create_engine, text

    engine = create_engine(PG_URL, pool_pre_ping=True)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS sava_test CASCADE"))
        conn.execute(text("CREATE SCHEMA sava_test"))
        conn.execute(text("SET search_path TO sava_test, public"))
    engine.dispose()

    engine = create_engine(PG_URL, pool_pre_ping=True,
                           connect_args={"options": "-csearch_path=sava_test,public"})
    yield engine
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS sava_test CASCADE"))
    engine.dispose()


class TestPgvector:
    def test_extension_is_available(self, pg_engine):
        from sqlalchemy import text

        with pg_engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            installed = conn.execute(text(
                "SELECT extversion FROM pg_extension WHERE extname='vector'")).scalar()
        assert installed, "pgvector must be installed for production search"

    def test_hnsw_index_builds_and_is_used(self, pg_engine):
        """The index has to exist *and* the planner has to choose it."""
        from sqlalchemy import text

        with pg_engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            conn.execute(text("DROP TABLE IF EXISTS vec_probe"))
            conn.execute(text(
                "CREATE TABLE vec_probe (id serial primary key, embedding vector(8))"))
            for i in range(200):
                vec = [float((i + j) % 7) / 7.0 for j in range(8)]
                conn.execute(text("INSERT INTO vec_probe (embedding) VALUES (:v)"),
                             {"v": str(vec)})
            conn.execute(text(
                "CREATE INDEX hnsw_vec_probe ON vec_probe "
                "USING hnsw (embedding vector_cosine_ops) WITH (m=16, ef_construction=64)"))
            conn.execute(text("ANALYZE vec_probe"))

            query = [0.5] * 8
            # On 200 rows the planner correctly prefers a sequential scan, so
            # asserting on an unhinted plan would test the planner rather than
            # the index. Disabling seqscan proves the index is present, valid,
            # and usable for this operator — which is the deployable property.
            conn.execute(text("SET LOCAL enable_seqscan = off"))
            plan = conn.execute(text(
                "EXPLAIN SELECT id FROM vec_probe ORDER BY embedding <=> :v LIMIT 5"),
                {"v": str(query)}).fetchall()
            plan_text = " ".join(row[0] for row in plan)

            rows = conn.execute(text(
                "SELECT id, 1 - (embedding <=> :v) AS sim FROM vec_probe "
                "ORDER BY embedding <=> :v LIMIT 5"), {"v": str(query)}).fetchall()

        assert len(rows) == 5
        assert all(-1.0 <= float(r.sim) <= 1.0 for r in rows)
        # Ordering and limiting happen in the database, not in Python.
        assert "hnsw_vec_probe" in plan_text, plan_text


class TestSkipLockedClaiming:
    """The concurrency guarantee the whole worker fleet rests on."""

    def _make_jobs(self, engine, n: int):
        from sqlalchemy import text

        with engine.begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS job_probe"))
            conn.execute(text("""
                CREATE TABLE job_probe (
                    id serial primary key,
                    state text NOT NULL DEFAULT 'queued',
                    locked_by text,
                    priority int NOT NULL DEFAULT 100
                )
            """))
            for _ in range(n):
                conn.execute(text("INSERT INTO job_probe DEFAULT VALUES"))

    def test_no_job_is_claimed_twice_under_contention(self, pg_engine):
        from sqlalchemy import text

        total = 300
        self._make_jobs(pg_engine, total)
        claimed_by_worker: dict = {}
        lock = threading.Lock()

        def worker(name: str):
            mine = []
            while True:
                with pg_engine.begin() as conn:
                    row = conn.execute(text("""
                        UPDATE job_probe SET state='running', locked_by=:w
                        WHERE id = (
                            SELECT id FROM job_probe WHERE state='queued'
                            ORDER BY priority, id
                            FOR UPDATE SKIP LOCKED LIMIT 1
                        ) RETURNING id
                    """), {"w": name}).first()
                if row is None:
                    break
                mine.append(row.id)
            with lock:
                claimed_by_worker[name] = mine

        threads = [threading.Thread(target=worker, args=(f"w{i}",)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        all_claimed = [jid for ids in claimed_by_worker.values() for jid in ids]
        assert len(all_claimed) == total, "every job must be claimed exactly once"
        assert len(set(all_claimed)) == total, "a job was claimed by two workers"
        # Work actually spread rather than one thread winning everything.
        assert sum(1 for ids in claimed_by_worker.values() if ids) > 1

    def test_claiming_does_not_block_other_workers(self, pg_engine):
        """SKIP LOCKED, not FOR UPDATE: a busy row is stepped over, not waited on."""
        from sqlalchemy import text

        self._make_jobs(pg_engine, 5)
        holder = pg_engine.connect()
        trans = holder.begin()
        first = holder.execute(text(
            "SELECT id FROM job_probe WHERE state='queued' ORDER BY id "
            "FOR UPDATE SKIP LOCKED LIMIT 1")).scalar()

        try:
            with pg_engine.begin() as conn:
                second = conn.execute(text(
                    "SELECT id FROM job_probe WHERE state='queued' ORDER BY id "
                    "FOR UPDATE SKIP LOCKED LIMIT 1")).scalar()
            assert second is not None and second != first
        finally:
            trans.rollback()
            holder.close()


class TestSchemaOnPostgres:
    def test_full_schema_creates_cleanly(self, pg_engine):
        """Including the tables added by this pass."""
        from sqlalchemy import inspect, text

        from api.models import Base

        with pg_engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        Base.metadata.create_all(bind=pg_engine)

        tables = set(inspect(pg_engine).get_table_names(schema="sava_test"))
        for required in ("canonical_content", "content_chunks", "content_assets",
                         "content_comments", "jobs", "bookmarks"):
            assert required in tables, f"{required} missing from Postgres schema"

    def test_canonical_key_is_unique(self, pg_engine):
        """Dedup is a database constraint, not a hopeful application check."""
        from sqlalchemy import text
        from sqlalchemy.exc import IntegrityError

        from api.models import Base

        with pg_engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        Base.metadata.create_all(bind=pg_engine)

        from sqlalchemy.orm import sessionmaker

        from api.models import CanonicalContent

        Session = sessionmaker(bind=pg_engine)
        session = Session()
        try:
            session.query(CanonicalContent).filter(
                CanonicalContent.content_key == "youtube:dup").delete()
            session.commit()

            # Written through the ORM, which is how the application writes and
            # therefore what the constraint has to hold against.
            session.add(CanonicalContent(content_key="youtube:dup", platform="youtube",
                                         canonical_url="u", media_kind="video"))
            session.commit()

            with pytest.raises(IntegrityError):
                session.add(CanonicalContent(content_key="youtube:dup", platform="youtube",
                                             canonical_url="u2", media_kind="video"))
                session.commit()
            session.rollback()
        finally:
            session.close()
