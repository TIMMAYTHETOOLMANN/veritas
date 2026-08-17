# core/db.py — VERITAS persistent state (SQLite, WAL)
import sqlite3, json, os, time

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "veritas.db")

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS targets(
  address TEXT PRIMARY KEY, chain TEXT, code_size INTEGER, deploy_block INTEGER,
  template_id TEXT, similarity REAL, first_seen INTEGER);
CREATE TABLE IF NOT EXISTS inventory(
  address TEXT, layer TEXT, asset TEXT, amount_wei TEXT, block INTEGER,
  source TEXT, ts INTEGER, PRIMARY KEY(address, layer, asset));
CREATE TABLE IF NOT EXISTS findings(
  id INTEGER PRIMARY KEY AUTOINCREMENT, address TEXT, vclass TEXT,
  tier TEXT, confidence TEXT, status TEXT, evidence TEXT, created INTEGER);
CREATE TABLE IF NOT EXISTS exploitability(
  finding_id INTEGER PRIMARY KEY, recipe TEXT, ceiling_wei TEXT,
  preconditions TEXT, p_success REAL, competition REAL, ev_wei TEXT, rationale TEXT);
CREATE TABLE IF NOT EXISTS probes(
  id INTEGER PRIMARY KEY AUTOINCREMENT, address TEXT, battery TEXT, probe TEXT,
  call_data TEXT, result TEXT, verdict TEXT, ts INTEGER);
CREATE TABLE IF NOT EXISTS lineage(
  template_id TEXT, address TEXT, delta_regions TEXT,
  PRIMARY KEY(template_id, address));
"""

def conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c

def init():
    c = conn(); c.executescript(SCHEMA); c.commit(); c.close()
    return DB_PATH

def now(): return int(time.time())

def put(c, sql, args=()):
    c.execute(sql, args); c.commit()
