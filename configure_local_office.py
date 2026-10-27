"""Configure the existing native platform for a local Docker Document Server."""
import argparse
from contextlib import closing
from datetime import datetime
from pathlib import Path
import secrets
import sqlite3
import server
import onlyoffice


def prepare():
    server.init()
    folder = server.DB.parent
    env_file = folder / 'onlyoffice.env'
    previous = {}
    if env_file.exists():
        previous = dict(line.split('=', 1) for line in env_file.read_text().splitlines() if '=' in line)
    with server.connection() as db:
        if db.execute("SELECT 1 FROM office_sessions WHERE state='editing'").fetchone():
            raise RuntimeError('存在 Office 编辑会话，请结束后再配置')
        current = onlyoffice.settings(db)
        value = {'public_url': 'http://127.0.0.1:8080', 'internal_url': 'http://127.0.0.1:8080',
                 'platform_url': 'http://host.docker.internal:8765',
                 'secret': current.get('secret') or previous.get('ONLYOFFICE_JWT_SECRET') or secrets.token_urlsafe(48)}
        admin = db.execute("SELECT id,username,role FROM users WHERE role='admin' ORDER BY id LIMIT 1").fetchone()
        if not admin:
            raise RuntimeError('请先创建平台管理员')
        backup = folder / ('pre-local-office-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.db')
        with closing(sqlite3.connect(backup)) as saved:
            db.backup(saved)
            saved.execute('DELETE FROM sessions')
            saved.commit()
        onlyoffice.config_api(server, db, admin, 'POST', value)
        temporary = env_file.with_suffix('.env.tmp')
        temporary.write_text('ONLYOFFICE_JWT_SECRET=' + value['secret'] + '\n', encoding='utf-8')
        temporary.replace(env_file)
    print('连接地址已配置；JWT 已生成并保存在本地，不在终端输出。')
    print('Document Server: http://127.0.0.1:8080')
    print('平台回调地址: http://host.docker.internal:8765')
    print('环境文件: ' + str(env_file))
    print('配置前备份: ' + str(backup))


if __name__ == '__main__':
    argparse.ArgumentParser(description=__doc__).parse_args()
    prepare()
