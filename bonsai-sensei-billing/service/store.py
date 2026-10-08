import contextlib
import hashlib
import sqlite3
import time
import uuid
from pathlib import Path

from .config import BillingError


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


class Store:
    def __init__(self, path):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS purchases (
                    token_hash TEXT PRIMARY KEY, owner TEXT NOT NULL,
                    product TEXT NOT NULL, kind TEXT NOT NULL,
                    state TEXT NOT NULL, token_cipher BLOB NOT NULL,
                    receipt_id TEXT NOT NULL UNIQUE,
                    created REAL NOT NULL, updated REAL NOT NULL, revoked_at REAL
                );
                CREATE TABLE IF NOT EXISTS outbox (
                    token_hash TEXT PRIMARY KEY REFERENCES purchases(token_hash),
                    attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt REAL NOT NULL DEFAULT 0,
                    lease_until REAL NOT NULL DEFAULT 0,
                    finalize_attempted INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS revoked_tokens (
                    token_hash TEXT PRIMARY KEY, created REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS push_messages (
                    message_id TEXT PRIMARY KEY, token_cipher BLOB NOT NULL,
                    done INTEGER NOT NULL DEFAULT 0, next_attempt REAL NOT NULL DEFAULT 0,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    event_kind TEXT NOT NULL DEFAULT 'refresh'
                );
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS purchase_owner ON purchases(owner);
                CREATE INDEX IF NOT EXISTS purchase_refresh ON purchases(state,updated);
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(push_messages)")}
            if "event_kind" not in columns:
                db.execute("ALTER TABLE push_messages ADD COLUMN event_kind TEXT NOT NULL DEFAULT 'refresh'")

    @contextlib.contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def find(self, hashed):
        with self.connection() as db:
            row = db.execute("SELECT * FROM purchases WHERE token_hash=?", (hashed,)).fetchone()
            return dict(row) if row else None

    def revoked(self, hashed):
        with self.connection() as db:
            return db.execute("SELECT 1 FROM revoked_tokens WHERE token_hash=?", (hashed,)).fetchone() is not None

    def register(self, hashed, owner, product, kind, encrypted, state):
        now = time.time()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM revoked_tokens WHERE token_hash=?", (hashed,)).fetchone():
                raise BillingError("purchase_revoked", 409)
            old = db.execute("SELECT * FROM purchases WHERE token_hash=?", (hashed,)).fetchone()
            if old:
                if old["owner"] != owner or old["product"] != product:
                    raise BillingError("purchase_owner_or_product_mismatch", 409)
                if old["state"] == "revoked":
                    raise BillingError("purchase_revoked", 409)
                # A duplicate request cannot overwrite a verified or revoked state.
                if old["state"] == "pending_purchase" and state == "processing":
                    db.execute("UPDATE purchases SET state=?, updated=? WHERE token_hash=?",
                               (state, now, hashed))
                    db.execute("UPDATE outbox SET next_attempt=0 WHERE token_hash=?", (hashed,))
            else:
                db.execute("INSERT INTO purchases VALUES(?,?,?,?,?,?,?,?,?,NULL)",
                           (hashed, owner, product, kind, state, encrypted, str(uuid.uuid4()), now, now))
                db.execute("INSERT INTO outbox(token_hash) VALUES(?)", (hashed,))
        return self.find(hashed)

    def claim(self, hashed=None):
        now = time.time()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            query = """SELECT p.*,o.finalize_attempted,o.attempts FROM outbox o
                       JOIN purchases p USING(token_hash)
                       WHERE p.state IN ('processing','pending_purchase')
                       AND o.next_attempt<=? AND o.lease_until<=?"""
            args = [now, now]
            if hashed:
                query += " AND p.token_hash=?"
                args.append(hashed)
            row = db.execute(query + " ORDER BY o.next_attempt LIMIT 1", args).fetchone()
            if row:
                db.execute("UPDATE outbox SET lease_until=? WHERE token_hash=?", (now + 90, row["token_hash"]))
            return dict(row) if row else None

    def attempted(self, hashed):
        with self.connection() as db:
            db.execute("UPDATE outbox SET finalize_attempted=1 WHERE token_hash=?", (hashed,))

    def activate(self, hashed):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE purchases SET state='active',updated=? WHERE token_hash=? "
                       "AND state IN ('processing','pending_purchase') AND NOT EXISTS "
                       "(SELECT 1 FROM revoked_tokens WHERE token_hash=?)", (time.time(), hashed, hashed))
            db.execute("DELETE FROM outbox WHERE token_hash=?", (hashed,))

    def retry(self, hashed, attempts):
        delay = min(3600, 5 * 2 ** min(attempts, 10))
        with self.connection() as db:
            db.execute("UPDATE outbox SET attempts=attempts+1,lease_until=0,next_attempt=? WHERE token_hash=?",
                       (time.time() + delay, hashed))

    def revoke(self, hashed):
        now = time.time()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT OR IGNORE INTO revoked_tokens VALUES(?,?)", (hashed, now))
            db.execute("UPDATE purchases SET state='revoked',updated=?,revoked_at=COALESCE(revoked_at,?) "
                       "WHERE token_hash=?", (now, now, hashed))
            db.execute("DELETE FROM outbox WHERE token_hash=?", (hashed,))

    def entitlements(self, owner):
        with self.connection() as db:
            rows = db.execute("SELECT product,kind,state,receipt_id,created,revoked_at FROM purchases "
                              "WHERE owner=? ORDER BY created,receipt_id", (owner,)).fetchall()
        return {
            "permanent_products": sorted({row["product"] for row in rows
                                          if row["kind"] == "permanent" and row["state"] == "active"}),
            "consumable_receipts": [{"receipt_id": row["receipt_id"], "product_id": row["product"],
                                     "state": row["state"], "created_at": row["created"],
                                     "revoked_at": row["revoked_at"]}
                                    for row in rows if row["kind"] == "consumable"],
        }

    def meta(self, key, value=None):
        with self.connection() as db:
            if value is not None:
                db.execute("INSERT INTO metadata VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                           (key, str(value)))
            row = db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
            return row[0] if row else None

    def due_refresh(self):
        with self.connection() as db:
            return [dict(row) for row in db.execute("SELECT * FROM purchases WHERE state='active' "
                                                   "AND updated<? ORDER BY updated LIMIT 20", (time.time()-3600,))]

    def refreshed(self, hashed):
        with self.connection() as db:
            db.execute("UPDATE purchases SET updated=? WHERE token_hash=?", (time.time(), hashed))

    def push(self, message_id, encrypted, event_kind="refresh", revoked_hash=None):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute("INSERT OR IGNORE INTO push_messages(message_id,token_cipher,event_kind) VALUES(?,?,?)",
                                (message_id, encrypted, event_kind))
            if cursor.rowcount and revoked_hash:
                now = time.time()
                db.execute("INSERT OR IGNORE INTO revoked_tokens VALUES(?,?)", (revoked_hash, now))
                db.execute("UPDATE purchases SET state='revoked',updated=?,revoked_at=COALESCE(revoked_at,?) "
                           "WHERE token_hash=?", (now, now, revoked_hash))
                db.execute("DELETE FROM outbox WHERE token_hash=?", (revoked_hash,))

    def push_jobs(self):
        with self.connection() as db:
            return [dict(row) for row in db.execute("SELECT * FROM push_messages WHERE done=0 "
                                                   "AND next_attempt<=? LIMIT 20", (time.time(),))]

    def push_done(self, message_id):
        with self.connection() as db:
            db.execute("UPDATE push_messages SET done=1 WHERE message_id=?", (message_id,))

    def push_retry(self, message_id, attempts):
        with self.connection() as db:
            db.execute("UPDATE push_messages SET attempts=attempts+1,next_attempt=? WHERE message_id=?",
                       (time.time()+min(3600, 5*2**min(attempts, 10)), message_id))
