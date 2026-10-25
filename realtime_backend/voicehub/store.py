"""Small, bounded local SQLite journal. Audio and credentials are never journalled."""
import json
import sqlite3
import time
from pathlib import Path


class Store:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=NORMAL;
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY, device_id TEXT NOT NULL, created REAL, updated REAL);
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY, session_id TEXT, turn_id TEXT, role TEXT,
                content TEXT, status TEXT, created REAL);
            CREATE INDEX IF NOT EXISTS message_session ON messages(session_id, id);
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY, session_id TEXT, turn_id TEXT, type TEXT,
                payload TEXT, created REAL);
            CREATE INDEX IF NOT EXISTS event_session ON events(session_id, id);
        ''')

    def session(self, sid, device):
        self.db.execute('INSERT INTO sessions VALUES (?,?,?,?) ON CONFLICT(id) '
                        'DO UPDATE SET updated=excluded.updated', (sid, device, time.time(), time.time()))
        self.db.commit()

    def owner(self, sid):
        row = self.db.execute('SELECT device_id FROM sessions WHERE id=?', (sid,)).fetchone()
        return row[0] if row else None

    def message(self, sid, tid, role, content, status):
        self.db.execute('INSERT INTO messages(session_id,turn_id,role,content,status,created) '
                        'VALUES (?,?,?,?,?,?)', (sid, tid, role, content, status, time.time()))
        self.db.execute('UPDATE sessions SET updated=? WHERE id=?', (time.time(), sid))
        self.db.commit()

    def messages(self, sid, before=None, limit=100):
        rows = self.db.execute('SELECT * FROM messages WHERE session_id=? AND id<? '
                               'ORDER BY id DESC LIMIT ?', (sid, before or 2**63-1, min(limit, 200)))
        return list(reversed([dict(r) for r in rows]))

    def sessions(self, before=None):
        return [dict(r) for r in self.db.execute('SELECT * FROM sessions WHERE updated<? '
                'ORDER BY updated DESC LIMIT 100', (before or float('inf'),))]

    def event(self, event):
        payload = {k: v for k, v in event.items() if k not in ('session_id', 'turn_id', 'type')}
        self.db.execute('INSERT INTO events(session_id,turn_id,type,payload,created) VALUES(?,?,?,?,?)',
                        (event.get('session_id'), event.get('turn_id'), event['type'],
                         json.dumps(payload, ensure_ascii=False), time.time()))
        self.db.commit()

    def events(self, sid=None, before=None, limit=100):
        rows = self.db.execute('SELECT * FROM events WHERE (? IS NULL OR session_id=?) AND id<? '
                'ORDER BY id DESC LIMIT ?', (sid, sid, before or 2**63-1, min(limit, 200)))
        return [dict(r, payload=json.loads(r['payload'])) for r in rows]

    def prune(self, days=30):
        cutoff = time.time() - days * 86400
        self.db.execute('DELETE FROM events WHERE created<?', (cutoff,))
        self.db.execute('DELETE FROM messages WHERE created<?', (cutoff,))
        self.db.execute('DELETE FROM sessions WHERE updated<?', (cutoff,))
        self.db.commit()

    def close(self):
        self.db.close()
