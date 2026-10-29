"""Database abstraction layer supporting both SQLite and PostgreSQL.
Preserves existing SQLite database unchanged.
Enables transparent connection to cloud PostgreSQL when DATABASE_URL is configured.
"""
import os
import re
import datetime
from typing import Optional, Dict, Any, List

# PostgreSQL driver (optional import: only used when DATABASE_URL is provided)
try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
    PSYCOPG2_AVAILABLE = True
except ImportError:
    psycopg2 = None
    RealDictCursor = None
    PSYCOPG2_AVAILABLE = False

import sqlite3

DEFAULT_SQLITE_FILE = os.path.join(os.path.dirname(__file__), "comments.db")

def get_database_url() -> Optional[str]:
    """Returns DATABASE_URL if configured, normalizing postgres:// to postgresql://."""
    url = os.environ.get("DATABASE_URL")
    if url:
        url = url.strip()
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql://", 1)
        return url
    return None

def is_postgres() -> bool:
    """Returns True if a PostgreSQL connection string is configured and driver is available."""
    url = get_database_url()
    return bool(url and (url.startswith("postgresql://") or url.startswith("postgres://")))

class UniversalCursor:
    """Cursor wrapper that transparently adapts query placeholders (?, ON CONFLICT) between SQLite and Postgres."""
    def __init__(self, raw_cursor, is_pg: bool):
        self._cur = raw_cursor
        self._is_pg = is_pg

    def _adapt_query(self, query: str) -> str:
        if not self._is_pg:
            return query
        
        # 1. Adapt SQLite-specific INSERT OR IGNORE to Postgres ON CONFLICT DO NOTHING
        q = re.sub(r'INSERT\s+OR\s+IGNORE\s+INTO\s+comments\s*\(', 'INSERT INTO comments (', query, flags=re.IGNORECASE)
        if 'INSERT INTO comments (' in q and 'ON CONFLICT' not in q:
            # Append PostgreSQL ON CONFLICT DO NOTHING for comments
            q = q.rstrip('; \t\n\r') + " ON CONFLICT (post_id, source_comment_id) DO NOTHING"
        
        q = re.sub(r'INSERT\s+OR\s+IGNORE\s+INTO\s+posts\s*\(', 'INSERT INTO posts (', q, flags=re.IGNORECASE)
        if 'INSERT INTO posts (' in q and 'ON CONFLICT' not in q:
            q = q.rstrip('; \t\n\r') + " ON CONFLICT (canonical_url) DO NOTHING"

        # 2. Adapt SQLite excluded.value to Postgres excluded.value (identical in modern Postgres)
        # 3. Adapt ? parameter placeholders to %s for psycopg2, and escape literal % inside string literals (e.g. LIKE 'real_comment_%')
        parts = []
        in_single = False
        in_double = False
        i = 0
        while i < len(q):
            char = q[i]
            if char == "'" and not in_double:
                in_single = not in_single
                parts.append(char)
            elif char == '"' and not in_single:
                in_double = not in_double
                parts.append(char)
            elif char == '?' and not in_single and not in_double:
                parts.append('%s')
            elif char == '%' and (in_single or in_double):
                # Escape literal % inside string literals so psycopg2 doesn't treat it as a parameter placeholder
                if i + 1 < len(q) and q[i + 1] == '%':
                    parts.append('%%')
                    i += 1
                else:
                    parts.append('%%')
            else:
                parts.append(char)
            i += 1
        return ''.join(parts)

    def execute(self, query: str, params: Optional[Any] = None):
        adapted = self._adapt_query(query)
        # Skip SQLite-specific pragmas on Postgres
        if self._is_pg and adapted.strip().upper().startswith("PRAGMA"):
            return self
        
        if params is not None:
            self._cur.execute(adapted, params)
        else:
            self._cur.execute(adapted)
        return self

    def executemany(self, query: str, params_seq: Any):
        adapted = self._adapt_query(query)
        if self._is_pg and adapted.strip().upper().startswith("PRAGMA"):
            return self
        if self._is_pg:
            from psycopg2.extras import execute_batch
            execute_batch(self._cur, adapted, params_seq, page_size=100)
        else:
            self._cur.executemany(adapted, params_seq)
        return self

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()

    @property
    def rowcount(self):
        return self._cur.rowcount

    def close(self):
        self._cur.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

class UniversalConnection:
    """Connection wrapper ensuring unified commit, rollback, cursor, and close semantics across engines."""
    def __init__(self, raw_conn, is_pg: bool):
        self._conn = raw_conn
        self._is_pg = is_pg

    def cursor(self):
        if self._is_pg:
            raw_cur = self._conn.cursor(cursor_factory=RealDictCursor)
        else:
            raw_cur = self._conn.cursor()
        return UniversalCursor(raw_cur, self._is_pg)

    def execute(self, query: str, params: Optional[Any] = None):
        cur = self.cursor()
        cur.execute(query, params)
        return cur

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            self.rollback()
        else:
            self.commit()
        self.close()

    @property
    def is_postgres(self) -> bool:
        return self._is_pg

def get_connection(db_path: str = DEFAULT_SQLITE_FILE) -> UniversalConnection:
    """Returns a universal database connection: PostgreSQL if DATABASE_URL is set, else SQLite.
    Preserves existing SQLite database file completely intact.
    """
    db_url = get_database_url()
    if db_url and is_postgres():
        if not PSYCOPG2_AVAILABLE:
            raise RuntimeError("PostgreSQL connection requested via DATABASE_URL but psycopg2 is not installed.")
        raw = psycopg2.connect(db_url)
        return UniversalConnection(raw, is_pg=True)
    
    # SQLite fallback
    raw = sqlite3.connect(db_path, timeout=30.0)
    raw.execute("PRAGMA journal_mode=WAL;")
    raw.execute("PRAGMA busy_timeout=30000;")
    raw.execute("PRAGMA foreign_keys=ON;")
    raw.row_factory = sqlite3.Row
    return UniversalConnection(raw, is_pg=False)

def init_db(db_path: str = DEFAULT_SQLITE_FILE):
    """Initializes schema and B-tree indexes on either SQLite or PostgreSQL cleanly without modifying existing data."""
    conn = get_connection(db_path)
    c = conn.cursor()

    # 1. Posts Table
    c.execute("""
    CREATE TABLE IF NOT EXISTS posts (
        id TEXT PRIMARY KEY,
        canonical_url TEXT UNIQUE NOT NULL,
        original_url TEXT NOT NULL,
        urn TEXT,
        title TEXT,
        author_name TEXT,
        author_url TEXT,
        total_comments_count INTEGER DEFAULT 0,
        last_collected_at TEXT,
        created_at TEXT,
        updated_at TEXT
    )""")

    # 2. Comments Table
    c.execute("""
    CREATE TABLE IF NOT EXISTS comments (
        id TEXT PRIMARY KEY,
        post_id TEXT NOT NULL,
        source_comment_id TEXT NOT NULL,
        parent_comment_id TEXT,
        parent_source_id TEXT,
        comment_text TEXT NOT NULL,
        author_name TEXT,
        author_profile_url TEXT,
        author_headline TEXT,
        author_avatar_url TEXT,
        email TEXT,
        phone TEXT,
        company_website TEXT,
        company_linkedin_url TEXT,
        reaction_count INTEGER DEFAULT 0,
        likes_count INTEGER DEFAULT 0,
        commented_at TEXT,
        depth INTEGER DEFAULT 0,
        created_at TEXT,
        updated_at TEXT,
        FOREIGN KEY(post_id) REFERENCES posts(id) ON DELETE CASCADE,
        FOREIGN KEY(parent_comment_id) REFERENCES comments(id) ON DELETE SET NULL,
        UNIQUE(post_id, source_comment_id)
    )""")

    # 3. Collection Runs Table
    c.execute("""
    CREATE TABLE IF NOT EXISTS collection_runs (
        id TEXT PRIMARY KEY,
        post_id TEXT NOT NULL,
        status TEXT NOT NULL,
        provider_name TEXT NOT NULL,
        total_expected_comments INTEGER,
        total_collected_comments INTEGER DEFAULT 0,
        top_level_comments_count INTEGER DEFAULT 0,
        replies_count INTEGER DEFAULT 0,
        started_at TEXT,
        completed_at TEXT,
        error_message TEXT,
        run_metadata TEXT,
        created_at TEXT,
        updated_at TEXT
    )""")

    # 4. Settings Vault Table
    c.execute("""
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""")

    # 5. Production Performance B-Tree Indexes
    c.execute("CREATE INDEX IF NOT EXISTS idx_comments_post_id ON comments(post_id);")
    c.execute("CREATE INDEX IF NOT EXISTS idx_comments_email ON comments(email) WHERE email IS NOT NULL;")
    c.execute("CREATE INDEX IF NOT EXISTS idx_comments_reactions ON comments(reaction_count DESC);")
    c.execute("CREATE INDEX IF NOT EXISTS idx_posts_updated_at ON posts(updated_at DESC);")
    c.execute("CREATE INDEX IF NOT EXISTS idx_runs_post_id ON collection_runs(post_id);")

    conn.commit()
    conn.close()

def get_setting(key: str, default: str = None, db_path: str = DEFAULT_SQLITE_FILE) -> str:
    """Retrieves a configuration setting by key."""
    conn = get_connection(db_path)
    c = conn.cursor()
    c.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = c.fetchone()
    conn.close()
    if not row:
        return default
    return row["value"] if isinstance(row, dict) else row[0]

def set_setting(key: str, value: str, db_path: str = DEFAULT_SQLITE_FILE):
    """Stores or updates a configuration setting in the database."""
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection(db_path)
    c = conn.cursor()
    c.execute("""
        INSERT INTO settings (key, value, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
    """, (key, value, now))
    conn.commit()
    conn.close()

def delete_setting(key: str, db_path: str = DEFAULT_SQLITE_FILE):
    """Deletes a configuration setting from the database."""
    conn = get_connection(db_path)
    c = conn.cursor()
    c.execute("DELETE FROM settings WHERE key = ?", (key,))
    conn.commit()
    conn.close()

DB_FILE = DEFAULT_SQLITE_FILE

if __name__ == "__main__":
    init_db()
    print("Database manager ready (Engine: %s)" % ("PostgreSQL" if is_postgres() else "SQLite"))
