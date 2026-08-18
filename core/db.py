# core/db.py — VERITAS persistent state (SQLite, WAL)
import sqlite3, json, os, time

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "veritas.db")

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS targets(
  address TEXT PRIMARY KEY, 
  chain_id INTEGER,
  chain TEXT, 
  code_size INTEGER, 
  deploy_block INTEGER,
  bytecode_hash TEXT,
  template_id TEXT, 
  similarity REAL,
  denom TEXT,
  root TEXT,
  levels TEXT,
  deposit_sel BOOLEAN,
  withdraw_sel BOOLEAN,
  nullif_sel BOOLEAN,
  setver_sel BOOLEAN,
  updatever_sel BOOLEAN,
  verified_sel BOOLEAN,
  getroot_sel BOOLEAN,
  roots_sel BOOLEAN,
  token_sel BOOLEAN,
  ecrecover_sel BOOLEAN,
  analyzed_ts INTEGER,
  first_seen INTEGER,
  status TEXT);
CREATE TABLE IF NOT EXISTS inventory(
  address TEXT, 
  layer TEXT, 
  asset TEXT, 
  amount_wei TEXT, 
  block INTEGER,
  source TEXT, 
  ts INTEGER, 
  PRIMARY KEY(address, layer, asset));
CREATE TABLE IF NOT EXISTS findings(
  id INTEGER PRIMARY KEY AUTOINCREMENT, 
  address TEXT, 
  vclass TEXT,
  tier TEXT, 
  confidence TEXT, 
  status TEXT, 
  evidence TEXT, 
  created INTEGER);
CREATE TABLE IF NOT EXISTS exploitability(
  finding_id INTEGER PRIMARY KEY, 
  recipe TEXT, 
  ceiling_wei TEXT,
  preconditions TEXT, 
  p_success REAL, 
  competition REAL, 
  ev_wei TEXT, 
  rationale TEXT);
CREATE TABLE IF NOT EXISTS probes(
  id INTEGER PRIMARY KEY AUTOINCREMENT, 
  address TEXT, 
  battery TEXT, 
  probe TEXT,
  call_data TEXT, 
  result TEXT, 
  verdict TEXT, 
  ts INTEGER);
CREATE TABLE IF NOT EXISTS lineage(
  template_id TEXT, 
  address TEXT, 
  delta_regions TEXT,
  PRIMARY KEY(template_id, address));
CREATE TABLE IF NOT EXISTS walker_state(
  chain_id INTEGER PRIMARY KEY, 
  cur_block INTEGER,
  processed_count INTEGER, 
  status TEXT, 
  ts INTEGER);
CREATE TABLE IF NOT EXISTS emitters(
  chain_id INTEGER, 
  address TEXT, 
  deposits INTEGER, 
  withdrawals INTEGER,
  first_block INTEGER, 
  last_block INTEGER, 
  ts INTEGER,
  PRIMARY KEY(chain_id, address));
-- ==== T4/T5 extension (zk differential fuzzer + economic impact) ====
CREATE TABLE IF NOT EXISTS vk_registry(
  address TEXT PRIMARY KEY,
  chain_id INTEGER,
  curve TEXT,
  proof_system TEXT,
  vk_hash TEXT,
  alpha TEXT, alpha_pair TEXT, beta2 TEXT, gamma2 TEXT, delta2 TEXT,
  ic_count INTEGER,
  ic_points TEXT,
  g1_point_count INTEGER,
  extracted_from TEXT,
  extracted_ts INTEGER);
CREATE TABLE IF NOT EXISTS circuit_configs(
  config_id INTEGER PRIMARY KEY AUTOINCREMENT,
  source TEXT,
  fmt TEXT,
  n_constraints INTEGER,
  n_wires INTEGER,
  n_pub_inputs INTEGER,
  n_labels INTEGER,
  under_constrained_wires TEXT,
  wire_stats TEXT,
  parsed_ts INTEGER);
CREATE TABLE IF NOT EXISTS impact_sims(
  finding_id INTEGER PRIMARY KEY,
  address TEXT,
  fork_block INTEGER,
  pre_tvl_wei TEXT,
  post_tvl_wei TEXT,
  attacker_delta_wei TEXT,
  financially_exploitable INTEGER,
  artifacts TEXT,
  ts INTEGER);
CREATE TABLE IF NOT EXISTS fuzz_campaigns(
  campaign_id INTEGER PRIMARY KEY AUTOINCREMENT,
  address TEXT,
  vk_hash TEXT,
  corpus_size INTEGER,
  sent INTEGER,
  accepted INTEGER,
  rejected INTEGER,
  reverted INTEGER,
  backend TEXT,
  findings_json TEXT,
  ts INTEGER);
"""

# user_version gates destructive/migrating steps. v0 (legacy) init() dropped the
# targets table on EVERY run — wiping T1 analysis state each pipeline start.
# v1+ never drops; it only CREATEs missing tables and ALTERs new columns in.
USER_VERSION = 1

def _migrate(c):
    v = c.execute("PRAGMA user_version").fetchone()[0]
    if v < 1:
        # legacy DBs (or fresh) — ensure targets exists with full schema; DO NOT DROP
        # existing rows. If a legacy 7-col targets exists, rebuild preserving rows.
        cols = [r[1] for r in c.execute("PRAGMA table_info(targets)").fetchall()]
        if cols and len(cols) != 23:
            c.execute("ALTER TABLE targets RENAME TO targets_legacy")
            c.executescript(SCHEMA)
            shared = [x for x in cols if x in (
              'address','chain_id','chain','code_size','deploy_block','bytecode_hash',
              'template_id','similarity','denom','root','levels','deposit_sel',
              'withdraw_sel','nullif_sel','setver_sel','updatever_sel','verified_sel',
              'getroot_sel','roots_sel','token_sel','ecrecover_sel','analyzed_ts',
              'first_seen','status')]
            sel = ", ".join(shared)
            c.execute(f"INSERT OR IGNORE INTO targets ({sel}) SELECT {sel} FROM targets_legacy")
            c.execute("DROP TABLE targets_legacy")
    c.execute(f"PRAGMA user_version={USER_VERSION}")
    return v

def conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c

def init():
    c = conn()
    # Enable foreign key support
    c.execute("PRAGMA foreign_keys=ON;")
    # DEFECT FIX: init() used to DROP TABLE targets unconditionally, wiping all
    # T1 analysis state on every pipeline run. Migration is now version-gated
    # and row-preserving (see _migrate).
    _migrate(c)
    c.executescript(SCHEMA)
    c.commit()
    c.close()
    return DB_PATH

def now(): return int(time.time())

def put(c, sql, args=()):
    c.execute(sql, args); c.commit()