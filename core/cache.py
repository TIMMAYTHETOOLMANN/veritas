# core/cache.py — bytecode cache with staleness check
import json, os, hashlib, zlib
from core.db import conn, now

CACHE_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cache")
BYTECODE_CACHE_LIFETIME = 86400  # 24h — bytecode doesn't change unless contract upgraded
SIZELIMIT = 100_000_000         # 100MB max cache size

class BytecodeCache:
    def __init__(self):
        self.disk = os.path.join(CACHE_ROOT, "bytecode")
        os.makedirs(self.disk, exist_ok=True)
        self.meta = os.path.join(CACHE_ROOT, "cache.meta.json")
        self.meta_load()

    def meta_load(self):
        if os.path.exists(self.meta):
            with open(self.meta) as f:
                self._meta = json.load(f)
        else:
            self._meta = {}
        # purge expired entries
        now_ts = now()
        expired = [k for k, v in self._meta.items() if now_ts - v.get("ts", 0) > BYTECODE_CACHE_LIFETIME]
        for k in expired:
            self._delete(k)
            del self._meta[k]
        self._save_meta()

    def _save_meta(self):
        with open(self.meta, "w") as f:
            json.dump(self._meta, f)

    def _hash(self, addr):
        return addr.lower().replace("0x", "")[:64]

    def _path(self, addr):
        h = self._hash(addr)
        return os.path.join(self.disk, h[:2], h[2:] + ".bin"), addr

    def _delete(self, addr_key):
        path, _ = self._path_by_key(addr_key)
        if os.path.exists(path):
            os.remove(path)

    def _path_by_key(self, addr_key):
        h = addr_key
        return os.path.join(self.disk, h[:2], h[2:] + ".bin"), h

    def get(self, addr):
        """Return cached bytecode bytes or None if missing/expired."""
        path, _ = self._path(addr)
        if not os.path.exists(path):
            return None
        age = now() - os.path.getmtime(path)
        if age > BYTECODE_CACHE_LIFETIME:
            os.remove(path)
            return None
        with open(path, "rb") as f:
            raw = f.read()
        if not raw:
            return None
        buf = zlib.decompress(raw)
        # validate magic
        if buf[:6] != b"VERITAS":
            return None  # corrupt
        payload = buf[6:]
        return payload

    def put(self, addr, bytecode):
        """Store bytecode (bytes) in cache."""
        path, _ = self._path(addr)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        buf = b"VERITAS" + bytecode
        compressed = zlib.compress(buf)
        if len(compressed) > SIZELIMIT:
            return False  # too big
        with open(path, "wb") as f:
            f.write(compressed)
        self._meta[addr.lower()] = {"ts": now(), "size": len(bytecode)}
        self._save_meta()
        return True

    def clear_expired(self):
        """Purge all expired entries."""
        now_ts = now()
        expired = [k for k, v in self._meta.items() if now_ts - v.get("ts", 0) > BYTECODE_CACHE_LIFETIME]
        for k in expired:
            self._delete(k)
            del self._meta[k]
        self._save_meta()

cache = BytecodeCache()
