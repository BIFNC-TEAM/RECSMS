"""ONLYOFFICE Docs adapter. Files and callbacks are authenticated independently of cookies."""
import base64
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import time
import urllib.error
import urllib.request
import zipfile
import database
import object_store
from urllib.parse import urlparse, urlencode
from xml.sax.saxutils import escape

MAX_FILE = 16 * 1024 * 1024
AUTOSAVE_SECONDS = 5


def init_db(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS office_settings(id INTEGER PRIMARY KEY CHECK(id=1),value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS office_files(version_id INTEGER PRIMARY KEY REFERENCES versions(id),data BLOB NOT NULL,sha256 TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS office_sessions(key TEXT PRIMARY KEY,doc_id INTEGER REFERENCES documents(id),version_id INTEGER REFERENCES versions(id),user_id INTEGER REFERENCES users(id),state TEXT NOT NULL,source BLOB NOT NULL,saved BLOB,body TEXT,revision INTEGER NOT NULL DEFAULT 0,history TEXT,changes BLOB,last_error TEXT DEFAULT '',created TEXT,updated TEXT);
    CREATE INDEX IF NOT EXISTS office_sessions_doc ON office_sessions(doc_id,version_id);
    ''')
    columns = {row['name'] for row in db.execute('PRAGMA table_info(office_sessions)')}
    for name, sql_type in [('active_users', "TEXT NOT NULL DEFAULT '[]'"), ('archived_version_id', 'INTEGER'), ('saved_at', 'TEXT')]:
        if name not in columns:
            db.execute('ALTER TABLE office_sessions ADD COLUMN ' + name + ' ' + sql_type)
    db.execute('UPDATE office_sessions SET saved_at=updated WHERE saved IS NOT NULL AND saved_at IS NULL')


def settings(db):
    row = db.execute('SELECT value FROM office_settings WHERE id=1').fetchone()
    result = json.loads(row['value']) if row else {}
    for key, env in [('public_url', 'ONLYOFFICE_PUBLIC_URL'), ('internal_url', 'ONLYOFFICE_INTERNAL_URL'), ('platform_url', 'ONLYOFFICE_PLATFORM_URL'), ('secret', 'ONLYOFFICE_JWT_SECRET')]:
        if os.environ.get(env):
            result[key] = os.environ[env]
    result.setdefault('internal_url', result.get('public_url', ''))
    return result


def origin(url):
    parsed = urlparse(url)
    return parsed.scheme, parsed.hostname, parsed.port or (443 if parsed.scheme == 'https' else 80)


def validate_url(core, value):
    if not isinstance(value, str):
        raise core.Error('服务地址须为 HTTP 或 HTTPS URL')
    parsed = urlparse(value)
    if any(c.isspace() or c in ';\"\'' for c in value) or parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise core.Error('服务地址须为不带账号、查询或片段的 HTTP / HTTPS URL')
    origin(value)  # Validate port too.
    return value.rstrip('/')


def config_api(core, db, user, method, data):
    core.require_role(user, 'admin')
    current = settings(db)
    if method == 'POST':
        if db.execute("SELECT 1 FROM office_sessions WHERE state='editing'").fetchone():
            raise core.Error('存在未结束的 Office 编辑会话，暂不能更改连接配置', 409)
        value = {key: validate_url(core, data.get(key, '')) for key in ('public_url', 'internal_url', 'platform_url')}
        value['secret'] = data.get('secret') or current.get('secret', '')
        if not isinstance(value['secret'], str) or not 32 <= len(value['secret']) <= 512:
            raise core.Error('JWT 共享密钥至少 32 个字符，且需与 Document Server 保持一致')
        if any(os.environ.get(k) for k in ('ONLYOFFICE_PUBLIC_URL', 'ONLYOFFICE_INTERNAL_URL', 'ONLYOFFICE_PLATFORM_URL', 'ONLYOFFICE_JWT_SECRET')):
            raise core.Error('当前连接由环境变量管理，请修改环境变量并重启')
        db.execute('INSERT INTO office_settings VALUES(1,?) ON CONFLICT(id) DO UPDATE SET value=excluded.value', (json.dumps(value),))
        core.audit(db, user, 'ONLYOFFICE 配置变更', detail=value['public_url'])
        current = value
    return {**{k: current.get(k, '') for k in ('public_url', 'internal_url', 'platform_url')}, 'configured': all(current.get(k) for k in ('public_url', 'internal_url', 'platform_url', 'secret')), 'has_secret': bool(current.get('secret'))}


def ready(core, db):
    value = settings(db)
    if not all(value.get(k) for k in ('public_url', 'internal_url', 'platform_url', 'secret')):
        raise core.Error('尚未配置 ONLYOFFICE，请由管理员在“管理与审计”填写 Document Server 地址和 JWT 密钥', 503)
    return value


def b64(value):
    return base64.urlsafe_b64encode(value).decode().rstrip('=')


def sign(payload, secret):
    data = b64(b'{"alg":"HS256","typ":"JWT"}') + '.' + b64(json.dumps(payload, separators=(',', ':')).encode())
    return data + '.' + b64(hmac.new(secret.encode(), data.encode(), hashlib.sha256).digest())


def verify(core, token, secret):
    try:
        if not isinstance(token, str) or len(token) > 2 * 1024 * 1024:
            raise ValueError()
        header, body, signature = token.split('.')
        decode = lambda s: base64.urlsafe_b64decode(s + '=' * (-len(s) % 4))
        if json.loads(decode(header)).get('alg') != 'HS256':
            raise ValueError()
        expected = hmac.new(secret.encode(), (header + '.' + body).encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(decode(signature), expected):
            raise ValueError()
        payload = json.loads(decode(body))
        if not isinstance(payload, dict) or ('exp' in payload and float(payload['exp']) <= time.time()):
            raise ValueError()
        return payload
    except (ValueError, TypeError, KeyError, AttributeError):
        raise core.Error('ONLYOFFICE 签名无效或已过期', 403)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def download(core, config, url, maximum=MAX_FILE):
    try:
        parsed = urlparse(url)
        allowed = [config['internal_url'], config['public_url']]
        if parsed.username or parsed.password or not any(origin(url) == origin(base) and parsed.path.startswith(urlparse(base).path.rstrip('/') + '/') for base in allowed):
            raise core.Error('回调文件地址不在已配置的 Document Server 范围内', 403)
        opener = urllib.request.build_opener(NoRedirect())
        with opener.open(url, timeout=20) as response:
            content = response.read(maximum + 1)
        if not content or len(content) > maximum:
            raise core.Error('Office 文件为空或超过大小限制')
        return content
    except (urllib.error.URLError, TimeoutError, OSError):
        raise core.Error('无法获取 Document Server 文件，未确认保存成功，请检查服务器互通', 502)


def health(core, db, user):
    core.require_role(user, 'admin')
    config = settings(db)
    if not config.get('internal_url'):
        return {'reachable': False, 'message': '尚未配置 Document Server'}
    try:
        body = download(core, config, config['internal_url'].rstrip('/') + '/healthcheck', 100)
        return {'reachable': body.strip() == b'true', 'message': 'Document Server 健康检查返回：' + body.decode('utf-8', errors='replace')[:80]}
    except core.Error as exc:
        return {'reachable': False, 'message': exc.message}


def make_docx(body):
    paragraphs = ''.join('<w:p><w:r><w:t xml:space="preserve">' + escape(line) + '</w:t></w:r></w:p>' for line in body.split('\n'))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as package:
        package.writestr('[Content_Types].xml', '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        package.writestr('_rels/.rels', '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        package.writestr('word/document.xml', '<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>' + paragraphs + '<w:sectPr/></w:body></w:document>')
    return buffer.getvalue()


def docx_text(core, content):
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as package:
            files = package.infolist()
            if len(files) > 10000 or sum(f.file_size for f in files) > 64 * 1024 * 1024:
                raise core.Error('DOCX 解压后超过处理上限')
            if any(f.filename.lower().endswith('vbaproject.bin') for f in files):
                raise core.Error('不接受含宏的文档')
            if '[Content_Types].xml' not in package.namelist():
                raise core.Error('不是有效的 DOCX 包')
        return core.extract('document.docx', content)
    except zipfile.BadZipFile:
        raise core.Error('Document Server 返回的文件不是有效 DOCX')


def copy_file(db, source_id, target_id):
    db.execute('INSERT OR IGNORE INTO office_files(version_id,data,sha256) SELECT ?,data,sha256 FROM office_files WHERE version_id=?', (target_id, source_id))


def store_file(db, version, content):
    db.execute('INSERT INTO office_files VALUES(?,?,?) ON CONFLICT(version_id) DO UPDATE SET data=excluded.data,sha256=excluded.sha256', (version, object_store.put(content), hashlib.sha256(content).hexdigest()))


def guard(core, db, doc, action):
    pending = db.execute("SELECT 1 FROM office_sessions WHERE doc_id=? AND state IN ('editing','ready','error')", (doc['id'],)).fetchone()
    if pending and action != 'metadata':
        raise core.Error('Office 编辑尚未完成，请等待所有编辑窗口关闭并自动保存完成', 409)
    if action in ('collab', 'collab-checkpoint', 'save') and db.execute('SELECT 1 FROM office_files WHERE version_id=?', (doc['current_version'],)).fetchone():
        raise core.Error('当前为 Word 文档，请使用 ONLYOFFICE 编辑以保留版式', 409)


def open_editor(core, db, user, doc, data):
    config = ready(core, db)
    version_id = int(data.get('version_id') or doc['current_version'])
    version = db.execute('SELECT * FROM versions WHERE id=? AND doc_id=? AND id NOT IN (SELECT version_id FROM deleted_versions)', (version_id, doc['id'])).fetchone()
    if not version:
        raise core.Error('文档版本不存在', 404)
    writable = not doc['is_archived'] and doc['state'] in ('draft', 'public') and version_id == doc['current_version'] and not data.get('view')
    if writable:
        try:
            core.document(db, user, doc['id'], True)
        except core.Error:
            writable = False
    room = db.execute('SELECT body FROM collab WHERE doc_id=? AND version_id=?', (doc['id'], version_id)).fetchone()
    if writable and room and room['body'] != version['body']:
        raise core.Error('请先将 Yjs 协作内容保存为版本，再打开 Word 编辑', 409)
    # Probe before creating a session so an unavailable engine cannot block the document.
    if download(core, config, config['internal_url'] + '/healthcheck', 100).strip() != b'true':
        raise core.Error('ONLYOFFICE 服务健康检查未通过', 503)
    session = db.execute("SELECT * FROM office_sessions WHERE doc_id=? AND version_id=? AND state IN ('editing','ready','error') ORDER BY created DESC LIMIT 1", (doc['id'], version_id)).fetchone() if writable else None
    if session and session['state'] != 'editing':
        raise core.Error('上一轮 Office 编辑尚未形成版本，请先保存返回的文件', 409)
    if not session:
        row = db.execute('SELECT data FROM office_files WHERE version_id=?', (version_id,)).fetchone()
        content = object_store.get(row['data']) if row else make_docx(version['body'])
        if not writable and data.get('working') and version_id == doc['current_version']:
            saved = db.execute("SELECT saved FROM office_sessions WHERE doc_id=? AND version_id=? AND saved IS NOT NULL AND state IN ('editing','ready','error') ORDER BY updated DESC LIMIT 1", (doc['id'], version_id)).fetchone()
            if saved:
                content = object_store.get(saved['saved'])
        if writable:
            store_file(db, version_id, content)
        key = 'standards-' + secrets.token_hex(24)
        db.execute('INSERT INTO office_sessions(key,doc_id,version_id,user_id,state,source,created,updated) VALUES(?,?,?,?,?,?,?,?)', (key, doc['id'], version_id, user['id'], 'editing' if writable else 'view', object_store.put(content), core.now(), core.now()))
        if writable:
            db.execute('UPDATE office_sessions SET active_users=? WHERE key=?', (json.dumps([str(user['id'])]), key))
            db.execute('UPDATE office_sessions SET saved_at=? WHERE key=?',(core.now(),key))
        session = db.execute('SELECT * FROM office_sessions WHERE key=?', (key,)).fetchone()
    token = sign({'kind': 'office-file', 'key': session['key'], 'exp': int(time.time()) + 24 * 3600}, config['secret'])
    direct_edit = False  # All edits use review permissions so Track Changes cannot be disabled.
    editor = {'documentType': 'word', 'type': 'desktop', 'width': '100%', 'height': '100%',
              'document': {'fileType': 'docx', 'key': session['key'], 'title': doc['number'] + '-' + version['label'] + '.docx',
                           'url': config['platform_url'] + '/api/office/file?' + urlencode({'token': token}),
                           'permissions': {'edit': direct_edit, 'review': writable, 'comment': writable, 'download': True, 'print': True,
                                           **({'reviewGroups': []} if writable and not core.accounts.manages(db,dict(user),doc) else {})}},
              'editorConfig': {'mode': 'edit' if writable else 'view', 'lang': 'zh-CN', 'user': {'id': str(user['id']), 'name': user['username']},
                               'callbackUrl': config['platform_url'] + '/api/office/callback',
                               'coEditing': {'mode': 'fast', 'change': False},
                               'customization': {'autosave': True, 'forcesave': True, 'macros': False,
                                                 'reviewDisplay': 'markup' if writable else 'final',
                                                 'review': {'trackChanges': writable, 'reviewDisplay': 'markup' if writable else 'final'}}}}
    editor['token'] = sign(editor, config['secret'])
    core.audit(db, user, '打开 ONLYOFFICE' if writable else '预览 Word', doc['id'], session['key'])
    return {'config': editor, 'script_url': config['public_url'] + '/web-apps/apps/api/documents/api.js?rsms_review=3', 'key': session['key'], 'writable': writable}


def compare(core, db, user, doc, data):
    versions = []
    for name in ('a', 'b'):
        row = db.execute('SELECT id,label FROM versions WHERE id=? AND doc_id=? AND id NOT IN (SELECT version_id FROM deleted_versions)', (data.get(name), doc['id'])).fetchone()
        if not row:
            raise core.Error('对比版本不存在', 404)
        versions.append(row)
    if versions[0]['id'] == versions[1]['id']:
        raise core.Error('请选择两个不同版本', 400)
    original, revised = [open_editor(core, db, user, doc, {'version_id': v['id'], 'view': True, 'working': True}) for v in versions]
    request = {'c': 'compare', 'fileType': 'docx', 'url': revised['config']['document']['url']}
    request['token'] = sign(request, ready(core, db)['secret'])
    original['comparison'] = request
    original['comparison_labels'] = [v['label'] for v in versions]
    core.audit(db, user, '修订式版本比较', doc['id'], str(versions[0]['id']) + ' -> ' + str(versions[1]['id']))
    return original


def file(core, db, query):
    config = ready(core, db)
    payload = verify(core, query.get('token'), config['secret'])
    if payload.get('kind') != 'office-file':
        raise core.Error('文件访问令牌用途错误', 403)
    session = db.execute('SELECT * FROM office_sessions WHERE key=?', (payload.get('key'),)).fetchone()
    if not session or session['state'] in ('committed', 'cancelled'):
        raise core.Error('编辑会话已失效', 403)
    core.document(db, {'role': 'admin'}, session['doc_id'])
    if db.execute('SELECT 1 FROM deleted_versions WHERE version_id=?', (session['version_id'],)).fetchone():
        raise core.Error('版本已删除', 403)
    # If the editor cache is lost, its next source fetch resumes the last durable autosave.
    return object_store.get(session['saved'] if session['saved'] is not None else session['source'])


def archive(core, db, session):
    """Atomically freeze a completed session; retries must not create duplicate versions."""
    if session['state'] != 'ready':
        return
    doc = core.document(db, {'role': 'admin'}, session['doc_id'])
    if doc['is_archived'] or doc['state'] not in ('draft', 'public') or doc['current_version'] != session['version_id']:
        raise core.Error('工作版本已变化，不能归档旧编辑结果', 409)
    source = db.execute('SELECT * FROM versions WHERE id=?', (session['version_id'],)).fetchone()
    content = object_store.get(session['saved'] or session['source'])
    vid = source['id']
    actor = db.execute('SELECT id,username,role FROM users WHERE id=?', (session['user_id'],)).fetchone()
    if content != object_store.get(session['source']):
        stem = re.sub(r'\.r\d+$', '', source['label'])[:65]
        revision = 1
        while db.execute('SELECT 1 FROM versions WHERE doc_id=? AND label=?', (doc['id'], stem + '.r' + str(revision))).fetchone():
            revision += 1
        vid = core.add_version(db, actor, doc, {'label': stem + '.r' + str(revision), 'note': '自动保存协作修改', 'body': docx_text(core, content)}, source)
        store_file(db, vid, content)
    db.execute("UPDATE office_sessions SET state='committed',active_users='[]',archived_version_id=?,updated=? WHERE key=?", (vid, core.now(), session['key']))
    core.audit(db, actor, 'Office 自动归档', doc['id'], 'version=' + str(vid))


def request_save(core, config, key):
    command = {'c': 'forcesave', 'key': key, 'userdata': 'automatic-save'}
    token = sign({**command, 'operation': 'command', 'exp': int(time.time()) + 120}, config['secret'])
    request = urllib.request.Request(config['internal_url'] + '/command', data=json.dumps({**command, 'token': token}).encode(), headers={'Content-Type': 'application/json'})
    with urllib.request.build_opener(NoRedirect()).open(request, timeout=15) as response:
        result = json.loads(response.read(65536))
    if result.get('error') == 1:
        raise core.Error('协作会话暂不可用，已保存的稿件仍保留，请检查连接或重新打开文档',502)
    if result.get('error') not in (0, 4):  # 4: no new changes.
        raise core.Error('Office 自动保存请求失败', 502)
    return result.get('error')


def autosave_once(core):
    with core.connection() as db:
        db.execute('BEGIN IMMEDIATE')
        for row in db.execute("SELECT * FROM office_sessions WHERE state='ready'").fetchall():
            try:
                archive(core,db,row)
            except core.Error as exc:
                db.execute("UPDATE office_sessions SET state='error',last_error=? WHERE key=?",(exc.message,row['key']))
        config=settings(db)
        keys=[r['key'] for r in db.execute("SELECT key FROM office_sessions WHERE state IN ('editing','error')")]
    # Never hold a write lock while the document engine downloads or calls back.
    if config.get('internal_url') and config.get('secret'):
        for key in keys:
            try:
                result=request_save(core,config,key)
                if result==4:
                    with core.connection() as db:
                        db.execute("UPDATE office_sessions SET last_error='' WHERE key=? AND state='editing'",(key,))
            except (core.Error,OSError,ValueError):
                with core.connection() as db:
                    db.execute("UPDATE office_sessions SET last_error=? WHERE key=? AND state IN ('editing','error')",('自动保存暂未成功，正在重试；请检查连接，暂勿关闭编辑器',key))


def autosave_loop(core):
    while True:
        try:
            with database.job_lock('autosave') as acquired:
                if acquired:
                    autosave_once(core)
        except Exception as exc:
            print('Office 自动保存异常，将重试：'+type(exc).__name__,flush=True)
        time.sleep(AUTOSAVE_SECONDS)


def callback(core, db, data, authorization):
    config = ready(core, db)
    token = authorization[7:] if authorization.startswith('Bearer ') else data.get('token')
    claims = verify(core, token, config['secret'])
    if claims.get('operation') not in (None, 'callback'):
        raise core.Error('回调令牌用途错误', 403)
    payload = claims.get('payload', claims)
    if not isinstance(payload, dict) or payload.get('key') != data.get('key') or payload.get('status') != data.get('status'):
        raise core.Error('回调签名与正文不一致', 403)
    for k in ('url', 'changesurl', 'history', 'users', 'actions'):
        if k in data and payload.get(k) != data[k]:
            raise core.Error('回调签名未覆盖保存参数', 403)
    session = db.execute('SELECT * FROM office_sessions WHERE key=?', (payload.get('key'),)).fetchone()
    if not session:
        raise core.Error('Office 会话不存在', 404)
    status = payload.get('status')
    if type(status) is not int or status not in (1, 2, 3, 4, 6, 7):
        raise core.Error('未知的 Office 回调状态')
    content = body = changes = None
    if status in (2, 6):
        if not isinstance(payload.get('url'), str):
            raise core.Error('回调缺少已签名文件地址')
        content = download(core, config, payload['url'])
        body = docx_text(core, content)
        if payload.get('changesurl'):
            changes = download(core, config, payload['changesurl'], 8 * 1024 * 1024)
    db.execute('BEGIN IMMEDIATE')
    session = db.execute('SELECT * FROM office_sessions WHERE key=?', (payload['key'],)).fetchone()
    if session['state'] == 'cancelled':
        raise core.Error('版本已删除，拒绝编辑回写', 409)
    if session['state'] == 'committed':
        if content is None or content == object_store.get(session['saved']):
            return {'error': 0}
        raise core.Error('旧会话已归档，拒绝迟到的修改', 409)
    if session['state'] == 'view':
        if status in (2, 6):
            raise core.Error('只读会话禁止写入', 403)
        return {'error': 0}
    doc = core.document(db, {'role': 'admin'}, session['doc_id'])
    if doc['current_version'] != session['version_id'] or doc['state'] not in ('draft', 'public'):
        raise core.Error('版本已冻结，拒绝旧编辑会话回写', 409)
    account = db.execute('SELECT id,username,role FROM users WHERE id=?', (session['user_id'],)).fetchone()
    core.document(db, account, doc['id'], True)
    for user_id in payload.get('users', []):
        participant = db.execute('SELECT id,username,role FROM users WHERE id=?', (user_id,)).fetchone()
        if not participant:
            raise core.Error('回调包含未知的编辑用户', 403)
        core.document(db, participant, doc['id'], True)
    if status == 1:
        db.execute('UPDATE office_sessions SET active_users=?,updated=? WHERE key=?', (json.dumps(payload.get('users', [])), core.now(), session['key']))
    if content is not None:
        if session['state'] == 'ready' and content != object_store.get(session['saved']):
            raise core.Error('编辑会话已结束，拒绝乱序回调', 409)
        if content != object_store.get(session['saved']):
            db.execute('UPDATE office_sessions SET saved=?,body=?,revision=revision+1,history=?,changes=?,last_error=\'\',saved_at=?,updated=? WHERE key=?', (object_store.put(content), body, json.dumps(payload.get('history', {})), object_store.put(changes), core.now(),core.now(), session['key']))
            db.execute('UPDATE document_cycles SET approved_version=NULL WHERE doc_id=?',(doc['id'],))
            db.execute('UPDATE documents SET updated=? WHERE id=?',(core.now(),doc['id']))
            core.audit(db, account, 'ONLYOFFICE 保存回调', doc['id'], 'key=' + session['key'] + '; status=' + str(status))
        else:
            db.execute("UPDATE office_sessions SET last_error='' WHERE key=?",(session['key'],))
    if status in (2, 4):
        db.execute("UPDATE office_sessions SET state='ready',active_users='[]',saved=COALESCE(saved,source),updated=? WHERE key=?", (core.now(), session['key']))
        archive(core, db, db.execute('SELECT * FROM office_sessions WHERE key=?', (session['key'],)).fetchone())
    elif status == 6:
        db.execute("UPDATE office_sessions SET state='editing',last_error='' WHERE key=?", (session['key'],))
    elif status in (3, 7):
        db.execute("UPDATE office_sessions SET state='error',last_error=?,updated=? WHERE key=?", ('Document Server 报告保存错误：' + str(status), core.now(), session['key']))
    return {'error': 0}


def session_info(core, db, user, doc):
    row = db.execute("SELECT key,state,revision,version_id,last_error,updated,saved_at,active_users,saved IS NOT NULL AS has_saved FROM office_sessions WHERE doc_id=? AND state IN ('editing','ready','error') ORDER BY created DESC LIMIT 1", (doc['id'],)).fetchone()
    status = 'complete' if not row else ('error' if row['last_error'] else 'editing' if json.loads(row['active_users']) else 'saving')
    saved=row['saved_at'] if row else db.execute("SELECT MAX(saved_at) FROM office_sessions WHERE doc_id=? AND saved_at IS NOT NULL",(doc['id'],)).fetchone()[0]
    return {'state': doc['state'], 'cycle': core.lifecycle.settings(db, doc['id']), 'session': dict(row) if row else None, 'office_status': status, 'last_saved_at':saved,'autosave_seconds':AUTOSAVE_SECONDS,'is_archived': bool(doc['is_archived']), 'current_version': doc['current_version'], 'has_docx': bool(db.execute('SELECT 1 FROM office_files WHERE version_id=?', (doc['current_version'],)).fetchone())}


def checkpoint(core, db, user, doc, data):
    core.document(db, user, doc['id'], True)
    core.editable(doc)
    session = db.execute('SELECT * FROM office_sessions WHERE key=? AND doc_id=?', (data.get('key'), doc['id'])).fetchone()
    if session and session['state'] == 'committed' and session['archived_version_id']:
        if data.get('revision') != session['revision']:
            raise core.Error('Office 保存状态已更新，请刷新后重试', 409)
        return {'ok': True, 'version_id': session['archived_version_id']}
    if not session or session['version_id'] != doc['current_version'] or session['state'] != 'ready':
        raise core.Error('请先关闭所有 Office 编辑窗口，等待最终保存回调完成', 409)
    if data.get('revision') != session['revision']:
        raise core.Error('Office 保存状态已更新，请刷新后重试', 409)
    content = object_store.get(session['saved'] or session['source'])
    body = docx_text(core, content)
    source = db.execute('SELECT * FROM versions WHERE id=?', (doc['current_version'],)).fetchone()
    vid = core.add_version(db, user, doc, {**data, 'body': body}, source)
    store_file(db, vid, content)
    db.execute("UPDATE office_sessions SET state='committed',updated=? WHERE key=?", (core.now(), session['key']))
    core.audit(db, user, 'Office 形成新版本', doc['id'], 'version=' + str(vid))
    return {'ok': True, 'version_id': vid}


def export_pdf(core,db,user,doc,vid,content):
    config=ready(core,db)
    key='export-'+secrets.token_hex(24)
    db.execute("INSERT INTO office_sessions(key,doc_id,version_id,user_id,state,source,created,updated) VALUES(?,?,?,?,?,?,?,?)",(key,doc['id'],vid,user['id'],'view',object_store.put(content),core.now(),core.now()))
    db.commit()  # The converter retrieves this immutable snapshot through a separate connection.
    token=sign({'kind':'office-file','key':key,'exp':int(time.time())+600},config['secret'])
    payload={'async':False,'filetype':'docx','outputtype':'pdf','key':key,'url':config['platform_url']+'/api/office/file?'+urlencode({'token':token})}
    payload['token']=sign(payload,config['secret'])
    req=urllib.request.Request(config['internal_url']+'/converter',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json','Accept':'application/json'})
    try:
        with urllib.request.build_opener(NoRedirect).open(req,timeout=90) as response:result=json.load(response)
    except Exception:
        raise core.Error('PDF转换服务暂不可用，请稍后重试',502)
    if not result.get('endConvert') or not result.get('fileUrl'):raise core.Error('PDF转换未完成，请稍后重试',502)
    output=download(core,config,result['fileUrl'])
    if not output.startswith(b'%PDF-'):raise core.Error('转换结果不是有效PDF',502)
    return output


def upload(core, db, user, doc, data):
    core.document(db, user, doc['id'], True)
    core.editable(doc)
    guard(core, db, doc, 'upload')
    current = db.execute('SELECT * FROM versions WHERE id=?', (doc['current_version'],)).fetchone()
    room = db.execute('SELECT body FROM collab WHERE doc_id=? AND version_id=?', (doc['id'], doc['current_version'])).fetchone()
    if room and room['body'] != current['body']:
        raise core.Error('请先保存已有 Yjs 协作修改', 409)
    name, content = core.decode_file(data)
    if not name.lower().endswith('.docx'):
        raise core.Error('正文文件请使用 DOCX 格式')
    body = docx_text(core, content)
    vid = core.add_version(db, user, doc, {**data, 'body': body}, current)
    store_file(db, vid, content)
    core.audit(db, user, '导入 DOCX 原文件', doc['id'], 'version=' + str(vid))
    return {'ok': True, 'version_id': vid}
