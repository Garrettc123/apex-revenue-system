# apex_ledger_sqlite.py
# Persists APEX ledger to SQLite (Supabase drop-in)

import json, sqlite3, time
from pathlib import Path
from apex_agent_deep import ApexAgent, mastery_label

DB_PATH = Path('apex_ledger.db')

def init_db(path=DB_PATH):
    conn = sqlite3.connect(path)
    conn.execute('CREATE TABLE IF NOT EXISTS ledger (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, event TEXT, skill TEXT, competence REAL, mastery TEXT, hash TEXT, parent TEXT, payload TEXT)')
    conn.execute('CREATE TABLE IF NOT EXISTS skills (name TEXT PRIMARY KEY, competence REAL, mastery TEXT, evidence_count INTEGER, parent TEXT, updated_ts REAL)')
    conn.commit()
    return conn

def persist_agent(agent, conn):
    for entry in agent.expertise.ledger:
        conn.execute('INSERT INTO ledger (ts, event, skill, competence, mastery, hash, parent, payload) VALUES (?,?,?,?,?,?,?,?)',
                     (entry.get('ts', time.time()), entry.get('event'), entry.get('skill'), entry.get('competence'), entry.get('mastery'), entry.get('hash'), entry.get('parent'), json.dumps(entry)))
    for name, skill in agent.expertise.skills.items():
        conn.execute('INSERT OR REPLACE INTO skills (name, competence, mastery, evidence_count, parent, updated_ts) VALUES (?,?,?,?,?,?)',
                     (name, skill.competence, mastery_label(skill.competence), len(skill.evidence), skill.parent, time.time()))
    conn.commit()

if __name__ == '__main__':
    print('Ledger module ready. Call persist_agent(agent, conn) after encounters.')
