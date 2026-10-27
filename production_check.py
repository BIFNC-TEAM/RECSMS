"""Fail closed when cloud dependencies are missing; never fall back to a local production database."""
import os
import secrets
import database
import object_store
import shared_cache

REQUIRED = ('DATABASE_URL', 'REDIS_URL', 'COS_REGION', 'COS_BUCKET', 'COS_SECRET_ID', 'COS_SECRET_KEY', 'STANDARD_ADMIN_PASSWORD', 'ONLYOFFICE_JWT_SECRET', 'ONLYOFFICE_PUBLIC_URL', 'ONLYOFFICE_INTERNAL_URL', 'ONLYOFFICE_PLATFORM_URL')


def check():
    missing = [name for name in REQUIRED if not os.environ.get(name) or 'REPLACE' in os.environ[name]]
    if missing:
        raise RuntimeError('请填写生产环境变量：' + ', '.join(missing))
    if not os.environ['DATABASE_URL'].startswith(('postgresql://', 'postgres://', 'host=')):
        raise RuntimeError('DATABASE_URL 必须为 PostgreSQL 连接配置')
    if len(os.environ['STANDARD_ADMIN_PASSWORD']) < 12 or len(os.environ['ONLYOFFICE_JWT_SECRET']) < 32:
        raise RuntimeError('管理员密码至少12位，ONLYOFFICE密钥至少32位')
    with database.connect(None) as db:
        db.execute('SELECT 1')
    shared_cache.client().ping()
    # Exercise the exact private object write/read path without touching existing data.
    data = ('rsms-readiness-' + secrets.token_hex(16)).encode()
    ref = object_store.put(data)
    assert object_store.get(ref) == data
    digest = object_store.reference(ref)[2].decode()
    object_store.client().delete_object(Bucket=os.environ['COS_BUCKET'], Key=object_store.key(digest))
    print('PostgreSQL / Redis / COS 连接及读写检查通过', flush=True)


if __name__ == '__main__':
    check()
