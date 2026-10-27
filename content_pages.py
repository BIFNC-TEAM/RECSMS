"""Public policy previews with versioned, explicit login acknowledgements."""
import hashlib
from pathlib import Path

ROOT = Path(__file__).parent / 'web' / 'docs'
VERSION = '2026-10-27-preview.1'
PLATFORM = '机器人与国产操作系统标准文件协作编辑及版本管理平台'


def fragment(name):
    if name not in ('terms', 'privacy', 'guide'):
        raise ValueError('Unknown content page')
    return (ROOT / (name + '.html')).read_text(encoding='utf-8')


def metadata():
    return {'terms_version': VERSION + '.' + hashlib.sha256(fragment('terms').encode()).hexdigest()[:12],
            'privacy_version': VERSION + '.' + hashlib.sha256(fragment('privacy').encode()).hexdigest()[:12], 'preview': True,
            'terms_url': '/legal/terms', 'privacy_url': '/legal/privacy'}


def require_acceptance(core, data):
    if data.get('accepted') is not True:
        raise core.Error('请先阅读并勾选接受服务协议和隐私政策', 400)
    current = metadata()
    if data.get('terms_version') != current['terms_version'] or data.get('privacy_version') != current['privacy_version']:
        raise core.Error('协议版本已变化，请刷新页面并重新阅读确认', 409)


def init(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS policy_acceptances(user_id INTEGER REFERENCES users(id),terms_version TEXT NOT NULL,privacy_version TEXT NOT NULL,terms_hash TEXT NOT NULL,privacy_hash TEXT NOT NULL,accepted_at TEXT NOT NULL,method TEXT NOT NULL,preview INTEGER NOT NULL,PRIMARY KEY(user_id,terms_version,privacy_version));
    ''')


def record(core, db, uid, method):
    # A preview acknowledgement is explicitly distinct from a published legal agreement.
    hashes = [hashlib.sha256(fragment(name).encode()).hexdigest() for name in ('terms', 'privacy')]
    versions = metadata()
    db.execute('INSERT OR IGNORE INTO policy_acceptances VALUES(?,?,?,?,?,?,?,1)', (uid, versions['terms_version'], versions['privacy_version'], *hashes, core.now(), method))


def page(name):
    title = {'terms': '服务协议（预览草案）', 'privacy': '隐私政策（预览草案）', 'guide': '使用说明'}[name]
    return '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>' + title + ' · ' + PLATFORM + '</title><link rel="icon" type="image/png" href="/logo.png"><link rel="stylesheet" href="/style.css"></head><body class="info-page"><nav class="info-nav"><img class="info-logo" src="/logo.png" alt="平台标志" width="40" height="40"><a href="/">返回平台</a><a href="/help">使用说明</a><a href="/legal/terms">服务协议</a><a href="/legal/privacy">隐私政策</a></nav><main class="info-document">' + fragment(name) + '</main></body></html>'
