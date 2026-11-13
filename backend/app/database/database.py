from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, declarative_base
import os
import time
import sqlite3

_DEFAULT_DB = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "purity_beans.db"))
DATABASE_URL = os.getenv("DATABASE_URL")

if DATABASE_URL:
    if DATABASE_URL.startswith("sqlite:///"):
        db_path = DATABASE_URL[10:]
        if not os.path.isabs(db_path):
            backend_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
            if db_path.startswith("./") or db_path.startswith(".\\"):
                db_path = db_path[2:]
            db_path = os.path.abspath(os.path.join(backend_root, db_path))
            DATABASE_URL = f"sqlite:///{db_path}"
else:
    DATABASE_URL = f"sqlite:///{_DEFAULT_DB}"

# SQLite busy wait also set via PRAGMA; connect timeout is a second line of defence
# when the OS-level lock is held longer than a single statement.
_SQLITE_TIMEOUT_S = float(os.getenv("SQLITE_BUSY_TIMEOUT_S", "60"))

_connect_args = {}
if "sqlite" in (DATABASE_URL or ""):
    _connect_args = {
        "check_same_thread": False,
        "timeout": _SQLITE_TIMEOUT_S,
    }

# Pool MUST be sized for FastAPI's threadpool. Route handlers are sync `def`, so
# FastAPI runs them in its threadpool (default 40 workers) and each holds a DB
# session for the life of the request. SQLAlchemy's default pool is only
# 5 + 10 overflow = 15, so >15 concurrent requests queued 30s for a connection
# and then failed with:
#   TimeoutError: QueuePool limit of size 5 overflow 10 reached, connection timed out
# That surfaced as 20-30s latencies, random 500s, and the dashboard never
# rendering. 50 total comfortably exceeds the 40 threadpool workers. SQLite with
# WAL + check_same_thread=False handles these concurrent connections fine.
engine = create_engine(
    DATABASE_URL,
    connect_args=_connect_args,
    pool_size=20,
    max_overflow=30,
    pool_timeout=30,
    pool_recycle=1800,
    pool_pre_ping=True,
)


if "sqlite" in DATABASE_URL:
    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):
        """
        The DB was running in the default `delete` journal mode, where a single
        writer blocks EVERY reader. The Auto-Warm worker writes continuously in
        a background thread, so API reads intermittently stalled or timed out.

        WAL lets one writer and many readers run concurrently; busy_timeout makes
        a brief lock wait rather than raise "database is locked"; synchronous=
        NORMAL is the safe, fast pairing for WAL.

        busy_timeout raised from 10s to 60s (override via SQLITE_BUSY_TIMEOUT_S)
        because purity-api + purity-worker + voice webhooks all
        write the same file; 10s still timed out under concurrent call UPDATEs.
        """
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute(f"PRAGMA busy_timeout={int(_SQLITE_TIMEOUT_S * 1000)}")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def is_sqlite_locked(exc: BaseException) -> bool:
    """True for sqlite3 / SQLAlchemy OperationalError 'database is locked'."""
    msg = str(exc).lower()
    if "database is locked" in msg or "database is busy" in msg:
        return True
    if isinstance(exc, sqlite3.OperationalError) and "locked" in msg:
        return True
    return False


class LockedWriteLost(RuntimeError):
    """A commit hit 'database is locked', was rolled back, and no redo was
    supplied: the pending writes are GONE. Raised instead of the old
    behaviour (retrying commit() on an empty, rolled-back session and
    returning as if it succeeded)."""


def commit_with_retry(db, *, attempts: int = 6, base_delay: float = 0.05,
                      redo=None):
    """
    Commit with exponential backoff on SQLite lock contention.

    fix4: a failed commit leaves the SQLAlchemy session needing rollback,
    which discards every pending change. So on a lock we roll back and then:
      - if ``redo`` (a no-arg callable that re-applies the unit of work to
        ``db``) is given, call it and commit again;
      - otherwise raise LockedWriteLost - never a silent empty commit.
    Do NOT hold the session across network I/O - commit before dials.
    """
    import random as _random
    for i in range(max(1, attempts)):
        try:
            db.commit()
            return
        except Exception as e:  # noqa: BLE001
            if not is_sqlite_locked(e):
                raise
            try:
                db.rollback()
            except Exception:
                pass
            if redo is None:
                raise LockedWriteLost(
                    "database locked; pending writes rolled back and no redo "
                    "supplied: %s" % e) from e
            if i == attempts - 1:
                raise
            time.sleep(base_delay * (2 ** i) + _random.random() * base_delay)
            redo()


def run_with_retry(db, fn, *, attempts: int = 6, base_delay: float = 0.05):
    """Run fn() (which writes to db) and commit; on a lock, roll back and run
    fn() again. Returns fn's result from the attempt that committed."""
    box = {}

    def _do():
        box["r"] = fn()

    _do()
    commit_with_retry(db, attempts=attempts, base_delay=base_delay, redo=_do)
    return box.get("r")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Apply process-wide SQLite/call and trust-confidence patches once the DB layer loads.
# Safe no-op if optional modules are missing; keeps purity-api / worker aligned.
try:
    import app.purity_boot_patches  # noqa: F401
except Exception:
    pass
