"""Redis-backed sessions and request rate limits; SQLite fallback for local tests."""
import hashlib
import os
import time
from functools import lru_cache


@lru_cache(maxsize=1)
def client():
    import redis
    return redis.Redis.from_url(os.environ['REDIS_URL'], decode_responses=True, socket_connect_timeout=5, socket_timeout=5)


def key(kind, value):
    return os.environ.get('REDIS_PREFIX', 'rsms:') + kind + ':' + hashlib.sha256(value.encode()).hexdigest()


def create(db, token, uid, expires):
    if os.environ.get('REDIS_URL'):
        client().set(key('session', token), str(uid), ex=max(1, int(expires - time.time())))
    else:
        db.execute('INSERT INTO sessions VALUES(?,?,?)', (token, uid, expires))


def user_id(db, token):
    if not token:
        return None
    if os.environ.get('REDIS_URL'):
        value = client().get(key('session', token))
        return int(value) if value else None
    row = db.execute('SELECT user_id FROM sessions WHERE token=? AND expires>?', (token, time.time())).fetchone()
    return row['user_id'] if row else None


def remove(db, token):
    if os.environ.get('REDIS_URL'):
        client().delete(key('session', token))
    else:
        db.execute('DELETE FROM sessions WHERE token=?', (token,))


def limited(memory, name, limit, seconds=300):
    if os.environ.get('REDIS_URL'):
        count = client().eval("local n=redis.call('INCR',KEYS[1]); if n==1 then redis.call('EXPIRE',KEYS[1],ARGV[1]) end; return n", 1, key('rate', name), seconds)
        return int(count) > limit
    recent = [t for t in memory.get(name, []) if t > time.time() - seconds]
    blocked = len(recent) >= limit
    if not blocked:
        recent.append(time.time())
    memory[name] = recent
    return blocked
