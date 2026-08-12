from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, declarative_base
import os

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
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {},
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
        """
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=10000")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
