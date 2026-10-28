"""Standalone standard document management pilot. Python 3.11+, no dependencies."""
import argparse
import base64
import difflib
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import secrets
import subprocess
import shutil
import sqlite3
import time
import threading
import sys
import onlyoffice
import lifecycle
import accounts
import phone_auth
import review_issues
import database
import object_store
import shared_cache
import workspace
import content_pages
import zipfile
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from datetime import datetime, timezone
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse, quote

ROOT = Path(__file__).resolve().parent
DB = Path(os.environ.get('STANDARD_DB', str(ROOT / 'data/platform.db')))
MAX_BODY = 24 * 1024 * 1024
ROLES = ('admin', 'editor', 'reviewer', 'reader')


class Error(Exception):
    def __init__(self, message, status=400):
        self.message, self.status = message, status


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    return salt + ':' + hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 310000).hex()


@contextmanager
def connection():
    with database.connect(DB) as db:
        yield db


def audit(db, user, action, doc=None, detail=''):
    db.execute('INSERT INTO audit(actor,action,doc_id,detail,created) VALUES(?,?,?,?,?)',
               (user['username'], action, doc, detail, now()))


def init():
    DB.parent.mkdir(parents=True, exist_ok=True)
    with connection() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE NOT NULL,password TEXT NOT NULL,role TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY,user_id INTEGER REFERENCES users(id),expires REAL);
        CREATE TABLE IF NOT EXISTS directories(id INTEGER PRIMARY KEY,name TEXT NOT NULL,parent_id INTEGER REFERENCES directories(id));
        CREATE TABLE IF NOT EXISTS documents(id INTEGER PRIMARY KEY,number TEXT UNIQUE NOT NULL,title TEXT NOT NULL,directory_id INTEGER REFERENCES directories(id),owner_id INTEGER REFERENCES users(id),tags TEXT NOT NULL DEFAULT '',extra TEXT NOT NULL DEFAULT '{}',state TEXT NOT NULL DEFAULT 'draft',current_version INTEGER,release_version INTEGER,replacement_id INTEGER REFERENCES documents(id),created TEXT,updated TEXT);
        CREATE TABLE IF NOT EXISTS versions(id INTEGER PRIMARY KEY,doc_id INTEGER NOT NULL REFERENCES documents(id),label TEXT NOT NULL,body TEXT NOT NULL,note TEXT NOT NULL,author TEXT,created TEXT,UNIQUE(doc_id,label));
        CREATE TABLE IF NOT EXISTS attachments(id INTEGER PRIMARY KEY,version_id INTEGER REFERENCES versions(id),name TEXT NOT NULL,data BLOB NOT NULL,sha256 TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS opinions(id INTEGER PRIMARY KEY,doc_id INTEGER REFERENCES documents(id),version_id INTEGER REFERENCES versions(id),body TEXT,author TEXT,status TEXT DEFAULT 'pending',resolution TEXT DEFAULT '',created TEXT);
        CREATE TABLE IF NOT EXISTS transitions(id INTEGER PRIMARY KEY,doc_id INTEGER REFERENCES documents(id),version_id INTEGER,actor TEXT,action TEXT,note TEXT,created TEXT);
        CREATE TABLE IF NOT EXISTS todos(id INTEGER PRIMARY KEY,user_id INTEGER REFERENCES users(id),doc_id INTEGER REFERENCES documents(id),message TEXT,done INTEGER DEFAULT 0,created TEXT);
        CREATE TABLE IF NOT EXISTS grants(id INTEGER PRIMARY KEY,scope TEXT NOT NULL,target_id INTEGER NOT NULL,user_id INTEGER REFERENCES users(id),access TEXT NOT NULL,UNIQUE(scope,target_id,user_id));
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,actor TEXT,action TEXT,doc_id INTEGER,detail TEXT,created TEXT);
        CREATE TABLE IF NOT EXISTS evaluations(id INTEGER PRIMARY KEY,doc_id INTEGER REFERENCES documents(id),version_id INTEGER REFERENCES versions(id),user_id INTEGER REFERENCES users(id),score INTEGER NOT NULL CHECK(score BETWEEN 1 AND 5),body TEXT NOT NULL,created TEXT,UNIQUE(version_id,user_id));
        CREATE TABLE IF NOT EXISTS schedules(id INTEGER PRIMARY KEY,doc_id INTEGER REFERENCES documents(id),version_id INTEGER REFERENCES versions(id),user_id INTEGER REFERENCES users(id),publish_at TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',created TEXT);
        CREATE TABLE IF NOT EXISTS collab(doc_id INTEGER REFERENCES documents(id),version_id INTEGER REFERENCES versions(id),state TEXT NOT NULL,body TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(doc_id,version_id));
        CREATE TABLE IF NOT EXISTS presence(client_id TEXT PRIMARY KEY,doc_id INTEGER,version_id INTEGER,user_id INTEGER REFERENCES users(id),seen REAL);
        CREATE TABLE IF NOT EXISTS work_copies(copy_doc_id INTEGER PRIMARY KEY REFERENCES documents(id),parent_doc_id INTEGER REFERENCES documents(id),base_version_id INTEGER REFERENCES versions(id),status TEXT NOT NULL DEFAULT 'open',merged_version_id INTEGER REFERENCES versions(id));
        CREATE TABLE IF NOT EXISTS share_links(id INTEGER PRIMARY KEY,doc_id INTEGER REFERENCES documents(id),token_hash TEXT UNIQUE NOT NULL,creator_id INTEGER REFERENCES users(id),revoked INTEGER NOT NULL DEFAULT 0,created TEXT);
        CREATE TABLE IF NOT EXISTS share_members(link_id INTEGER REFERENCES share_links(id),user_id INTEGER REFERENCES users(id),joined TEXT,PRIMARY KEY(link_id,user_id));
        CREATE TABLE IF NOT EXISTS share_signing_key(id INTEGER PRIMARY KEY CHECK(id=1),value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS version_edges(child_id INTEGER REFERENCES versions(id),parent_id INTEGER REFERENCES versions(id),kind TEXT NOT NULL,PRIMARY KEY(child_id,parent_id,kind));
        CREATE TABLE IF NOT EXISTS deleted_versions(version_id INTEGER PRIMARY KEY REFERENCES versions(id),user_id INTEGER REFERENCES users(id),deleted_at TEXT);
        CREATE TABLE IF NOT EXISTS deleted_documents(doc_id INTEGER PRIMARY KEY REFERENCES documents(id),user_id INTEGER REFERENCES users(id),deleted_at TEXT);
        CREATE TABLE IF NOT EXISTS branch_archives(doc_id INTEGER PRIMARY KEY REFERENCES documents(id),archived_by INTEGER REFERENCES users(id),archived_at TEXT,note TEXT,restored_at TEXT);
        CREATE INDEX IF NOT EXISTS versions_doc ON versions(doc_id);
        CREATE INDEX IF NOT EXISTS audit_doc ON audit(doc_id,created);
        ''')
        onlyoffice.init_db(db)
        # Older releases always advanced the head on creation. Restore their chronological spine.
        db.execute("INSERT OR IGNORE INTO version_edges SELECT id,previous,'history' FROM (SELECT id,LAG(id) OVER(PARTITION BY doc_id ORDER BY id) AS previous FROM versions) legacy WHERE previous IS NOT NULL AND NOT EXISTS(SELECT 1 FROM version_edges e WHERE e.child_id=legacy.id AND e.kind='history')")
        db.execute("INSERT OR IGNORE INTO version_edges SELECT (SELECT MIN(v.id) FROM versions v WHERE v.doc_id=w.copy_doc_id),w.base_version_id,'fork' FROM work_copies w WHERE w.base_version_id IS NOT NULL AND EXISTS(SELECT 1 FROM versions v WHERE v.doc_id=w.copy_doc_id)")
        db.execute("INSERT OR IGNORE INTO version_edges SELECT w.merged_version_id,(SELECT MAX(v.id) FROM versions v WHERE v.doc_id=w.copy_doc_id AND v.id<w.merged_version_id),'merge' FROM work_copies w WHERE w.merged_version_id IS NOT NULL AND EXISTS(SELECT 1 FROM versions v WHERE v.doc_id=w.copy_doc_id AND v.id<w.merged_version_id)")
        db.execute('INSERT OR IGNORE INTO share_signing_key VALUES(1,?)', (secrets.token_urlsafe(48),))
        if not db.execute('SELECT 1 FROM users').fetchone():
            password = os.environ.get('STANDARD_ADMIN_PASSWORD') or secrets.token_urlsafe(15)
            if len(password) < 12:
                raise RuntimeError('STANDARD_ADMIN_PASSWORD 至少 12 个字符')
            db.execute('INSERT INTO users(username,password,role) VALUES(?,?,?)', ('admin', password_hash(password), 'admin'))
            print('首次初始化管理员：admin（密码取自 STANDARD_ADMIN_PASSWORD）' if database.production() else '首次初始化管理员：admin / ' + password, flush=True)
            db.execute('INSERT INTO directories(name) VALUES(?)', ('标准文件',))
        lifecycle.init(sys.modules[__name__], db)
        accounts.init(db)
        phone_auth.init(db)
        review_issues.init(db)
        workspace.init(sys.modules[__name__], db)
        content_pages.init(db)


def required(data, field, maximum=1000):
    value = data.get(field)
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise Error(field + ' 不能为空或超过长度限制')
    return value.strip()


def require_role(user, *roles):
    if user['role'] not in roles:
        raise Error('当前角色无权执行此操作', 403)


def document(db, user, doc_id, write=False):
    doc = db.execute('SELECT d.*,EXISTS(SELECT 1 FROM branch_archives a WHERE a.doc_id=d.id AND a.restored_at IS NULL) AS is_archived FROM documents d WHERE d.id=?', (doc_id,)).fetchone()
    if not doc or db.execute('SELECT 1 FROM deleted_documents WHERE doc_id=?', (doc_id,)).fetchone():
        raise Error('文档不存在', 404)
    if not write:
        return doc  # Every authenticated account can read documents; write checks remain below.
    if write and db.execute('SELECT 1 FROM legacy_materials WHERE doc_id=?', (doc_id,)).fetchone():
        raise Error('旧流程资料仅保留查阅，请在统一在编稿中编辑', 409)
    if write and doc['state'] not in ('draft', 'public'):
        raise Error('发布版本永久只读，请由管理员开启下一轮编辑', 409)
    user=dict(user)
    scoped=accounts.member(db,user,doc_id)
    manager=accounts.manages(db,user,doc)
    if write and doc['state'] == 'public' and not manager:
        raise Error('公开阶段仅管理员可编辑', 403)
    if write and doc['is_archived']:
        raise Error('分支已归档，请先由平台管理员恢复归档', 409)
    if manager:
        return doc
    if db.execute('SELECT 1 FROM document_members m JOIN users u ON u.phone=m.phone WHERE m.doc_id=? AND u.id=? AND m.revoked=1',(doc_id,user.get('id'))).fetchone():
        raise Error('文档邀请已撤销',403)
    if scoped:
        return doc
    if write and user['role'] != 'editor':
        raise Error('需要起草角色', 403)
    # Explicit ACLs restrict access; absent ACLs are shared with authenticated users.
    scopes = [('document', doc['id'])]
    directory_id = doc['directory_id']
    while directory_id:
        scopes.append(('directory', directory_id))
        parent = db.execute('SELECT parent_id FROM directories WHERE id=?', (directory_id,)).fetchone()
        directory_id = parent['parent_id'] if parent else None
    for scope, target in scopes:
        if target is None:
            continue
        grants = db.execute('SELECT user_id,access FROM grants WHERE scope=? AND target_id=?', (scope, target)).fetchall()
        if grants:
            own = next((g for g in grants if g['user_id'] == user['id']), None)
            if not own or (write and own['access'] != 'edit'):
                raise Error('无文档或目录访问权限', 403)
    return doc


def editable(doc):
    if doc['is_archived']:
        raise Error('分支已归档，请先由平台管理员恢复归档', 409)
    if doc['state'] not in ('draft', 'public'):
        raise Error('发布版本永久只读', 409)


def add_version(db, user, doc, data, source=None):
    label = required(data, 'label', 80)
    note = required(data, 'note', 2000)
    body = data.get('body', source['body'] if source else '')
    if not isinstance(body, str) or len(body) > 2_000_000:
        raise Error('正文格式错误或过大')
    vid = db.execute('INSERT INTO versions(doc_id,label,body,note,author,created) VALUES(?,?,?,?,?,?)',
                     (doc['id'], label, body, note, user['username'], now())).lastrowid
    if source:
        db.execute('INSERT INTO attachments(version_id,name,data,sha256) SELECT ?,name,data,sha256 FROM attachments WHERE version_id=?', (vid, source['id']))
        if source['body'] == body:
            onlyoffice.copy_file(db, source['id'], vid)
    previous = doc['current_version']
    if previous:
        db.execute("INSERT INTO version_edges VALUES(?,?,'history')", (vid, previous))
    if source and source['id'] != previous:
        kind = ('fork' if not previous else 'merge') if source['doc_id'] != doc['id'] else 'restore'
        db.execute('INSERT INTO version_edges VALUES(?,?,?)', (vid, source['id'], kind))
    next_state = 'public' if doc['state'] == 'public' else 'draft'
    db.execute('UPDATE documents SET current_version=?,state=?,updated=? WHERE id=?', (vid, next_state, now(), doc['id']))
    db.execute('UPDATE document_cycles SET approved_version=NULL WHERE doc_id=?', (doc['id'],))
    return vid


def decode_file(data):
    name = required(data, 'name', 200).replace('\\', '/').rsplit('/', 1)[-1]
    if not name or any(ord(c) < 32 for c in name):
        raise Error('文件名无效')
    try:
        content = base64.b64decode(data.get('data', ''), validate=True)
    except (ValueError, TypeError):
        raise Error('文件编码无效')
    if not content or len(content) > 16 * 1024 * 1024:
        raise Error('文件大小须为 1 字节至 16MB')
    return name, content


def extract(name, content):
    if name.lower().endswith(('.txt', '.md', '.csv')):
        try:
            return content.decode('utf-8-sig')
        except UnicodeDecodeError:
            raise Error('文本文件需使用 UTF-8 编码')
    if name.lower().endswith('.docx'):
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as z:
                item = z.getinfo('word/document.xml')
                if item.file_size > 8 * 1024 * 1024:
                    raise Error('文档正文过大')
                root = ET.fromstring(z.read(item))
                ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
                return '\n'.join(''.join(p.itertext()) for p in root.findall('.//w:p', ns))
        except (zipfile.BadZipFile, KeyError, ET.ParseError):
            raise Error('无效的 DOCX 文件')
    raise Error('正文导入支持 TXT、Markdown、CSV、DOCX；其他格式请作为附件上传')


def merge_collab(value):
    node = os.environ.get('STANDARD_NODE') or shutil.which('node')
    if not node:
        raise Error('实时协作需要 Node.js，请安装后重启服务', 503)
    try:
        result = subprocess.run([node, str(ROOT / 'collab-state.cjs')], input=json.dumps(value),
                                capture_output=True, text=True, encoding='utf-8', timeout=10,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    except subprocess.TimeoutExpired:
        raise Error('协作合并超时，本地内容仍保留，请重试', 503)
    if result.returncode:
        raise Error('协作数据无效或组件未安装（请执行 npm ci）', 400)
    return json.loads(result.stdout)


def collab_route(db, user, doc, method, query, data):
    if method == 'GET':
        db.execute('BEGIN IMMEDIATE')
        doc = document(db, user, doc['id'])
    version = data.get('version_id') if method == 'POST' else query.get('version_id')
    if str(version) != str(doc['current_version']):
        raise Error('当前版本已变化，协作窗口保留本地内容；请刷新后进入新版本', 409)
    room = db.execute('SELECT * FROM collab WHERE doc_id=? AND version_id=?', (doc['id'], doc['current_version'])).fetchone()
    if not room:
        current = db.execute('SELECT body FROM versions WHERE id=?', (doc['current_version'],)).fetchone()
        initial = merge_collab({'body': current['body']})
        db.execute('INSERT INTO collab(doc_id,version_id,state,body) VALUES(?,?,?,?)', (doc['id'], doc['current_version'], initial['state'], initial['body']))
        room = db.execute('SELECT * FROM collab WHERE doc_id=? AND version_id=?', (doc['id'], doc['current_version'])).fetchone()
    if method == 'POST':
        document(db, user, doc['id'], True)
        editable(doc)
        client = required(data, 'client_id', 100)
        update = data.get('update')
        if update:
            if not isinstance(update, str) or len(update) > 12 * 1024 * 1024:
                raise Error('协作更新过大', 413)
            merged = merge_collab({'state': room['state'], 'update': update})
            if merged['state'] != room['state']:
                db.execute('UPDATE collab SET state=?,body=?,revision=revision+1 WHERE doc_id=? AND version_id=?', (merged['state'], merged['body'], doc['id'], doc['current_version']))
                audit(db, user, '协作编辑', doc['id'], 'version=' + str(doc['current_version']))
        db.execute('DELETE FROM presence WHERE seen<?', (time.time() - 20,))
        db.execute('INSERT INTO presence(client_id,doc_id,version_id,user_id,seen) VALUES(?,?,?,?,?) ON CONFLICT(client_id) DO UPDATE SET doc_id=excluded.doc_id,version_id=excluded.version_id,user_id=excluded.user_id,seen=excluded.seen', (client, doc['id'], doc['current_version'], user['id'], time.time()))
    room = dict(db.execute('SELECT * FROM collab WHERE doc_id=? AND version_id=?', (doc['id'], doc['current_version'])).fetchone())
    room.pop('body')
    room['writable'] = doc['state'] == 'draft'
    room['people'] = [r['username'] for r in db.execute('SELECT DISTINCT u.username FROM presence p JOIN users u ON u.id=p.user_id WHERE p.doc_id=? AND p.version_id=? AND p.seen>?', (doc['id'], doc['current_version'], time.time() - 15))]
    return room


def route(db, user, method, path, query, data):
    core = sys.modules[__name__]
    parts = path.strip('/').split('/')
    if path == '/api/office-settings':
        return onlyoffice.config_api(core, db, user, method, data)
    if path == '/api/office-health' and method == 'GET':
        return onlyoffice.health(core, db, user)
    if path == '/api/shares/join' and method == 'POST':
        token = required(data, 'token', 200)
        link = db.execute('SELECT * FROM share_links WHERE token_hash=? AND revoked=0', (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
        if not link:
            raise Error('分享链接不存在或已被创建者撤销', 404)
        db.execute('INSERT OR IGNORE INTO share_members VALUES(?,?,?)', (link['id'], user['id'], now()))
        audit(db, user, '通过分享链接加入协作', link['doc_id'], 'share=' + str(link['id']))
        return {'id': link['doc_id']}
    if path == '/api/me':
        return dict(db.execute('SELECT id,username,role,phone,phone_verified FROM users WHERE id=?',(user['id'],)).fetchone()) | {'cloud_storage':database.production(), 'onboarding_state':workspace.onboarding_state(db,user['id'])}
    if path == '/api/onboarding' and method == 'POST':
        return workspace.finish_onboarding(core, db, user, data)
    if path == '/api/help' and method == 'GET':
        return {'html': content_pages.fragment('guide')}
    if path in ('/api/admin-users/search', '/api/admin-users') and method == 'POST':
        return accounts.administrator(core, db, user, path.endswith('/search'), data)
    if path == '/api/users':
        if method == 'POST':
            require_role(user, 'admin')
            role = data.get('role')
            if role not in ROLES:
                raise Error('角色无效')
            password = required(data, 'password', 200)
            if len(password) < 12:
                raise Error('密码至少 12 个字符')
            number=accounts.phone(core,data.get('phone'))
            try:
                db.execute('INSERT INTO users(username,password,role,phone) VALUES(?,?,?,?)', (required(data, 'username', 80), password_hash(password), role,number))
            except sqlite3.IntegrityError:
                raise Error('账号或手机号已被使用',409)
            audit(db, user, '新增用户', detail=data['username'])
        return [dict(r) for r in db.execute('SELECT id,username,role'+(',phone,phone_verified' if user['role']=='admin' else '')+' FROM users')]
    if path == '/api/directories':
        if method == 'POST':
            require_role(user, 'admin')
            raise Error('目录已固定为单层分类，不再提供目录管理',410)
        return [dict(r) for r in db.execute('SELECT * FROM directories ORDER BY id')]
    if path == '/api/todos':
        if method == 'POST':
            db.execute('UPDATE todos SET done=1 WHERE id=? AND user_id=?', (data.get('id'), user['id']))
        return [dict(r) for r in db.execute('SELECT * FROM todos WHERE user_id=? ORDER BY id DESC', (user['id'],))]
    if path == '/api/audit':
        require_role(user, 'admin')
        clauses, args = [], []
        for key, column in [('actor', 'actor'), ('doc_id', 'doc_id')]:
            if query.get(key):
                clauses.append(column + '=?'); args.append(query[key])
        for key, operator in [('from', '>='), ('to', '<=')]:
            if query.get(key):
                clauses.append('created' + operator + '?'); args.append(query[key])
        return [dict(r) for r in db.execute('SELECT * FROM audit' + (' WHERE ' + ' AND '.join(clauses) if clauses else '') + ' ORDER BY id DESC LIMIT 2000', args)]
    if path == '/api/grants' and method == 'POST':
        require_role(user, 'admin')
        scope, target = data.get('scope'), data.get('target_id')
        if scope not in ('document', 'directory') or data.get('access') not in ('read', 'edit'):
            raise Error('权限参数无效')
        table = 'documents' if scope == 'document' else 'directories'
        if not db.execute('SELECT 1 FROM ' + table + ' WHERE id=?', (target,)).fetchone():
            raise Error('授权目标不存在')
        db.execute('INSERT INTO grants(scope,target_id,user_id,access) VALUES(?,?,?,?) ON CONFLICT(scope,target_id,user_id) DO UPDATE SET access=excluded.access',
                   (scope, target, data.get('user_id'), data['access']))
        audit(db, user, '权限变更', target if scope == 'document' else None, json.dumps(data, ensure_ascii=False))
        return {'ok': True}
    if path == '/api/documents':
        if method == 'POST':
            require_role(user, 'admin', 'editor')
            directory = data.get('directory_id')
            if not directory or not db.execute('SELECT 1 FROM directories WHERE id=?', (directory,)).fetchone():
                raise Error('请选择有效目录')
            ancestor = directory
            while ancestor:
                grants = db.execute("SELECT * FROM grants WHERE scope='directory' AND target_id=?", (ancestor,)).fetchall()
                if user['role'] != 'admin' and grants and not any(g['user_id'] == user['id'] and g['access'] == 'edit' for g in grants):
                    raise Error('无目录编辑权限', 403)
                ancestor = db.execute('SELECT parent_id FROM directories WHERE id=?', (ancestor,)).fetchone()['parent_id']
            doc_id = db.execute('INSERT INTO documents(number,title,directory_id,owner_id,tags,extra,created,updated) VALUES(?,?,?,?,?,?,?,?)',
                                (required(data, 'number', 100), required(data, 'title', 300), directory, user['id'], str(data.get('tags', ''))[:1000], json.dumps(data.get('extra', {}), ensure_ascii=False), now(), now())).lastrowid
            doc = document(db, user, doc_id, True)
            add_version(db, user, doc, data)
            db.execute('INSERT OR IGNORE INTO document_cycles(doc_id) VALUES(?)', (doc_id,))
            audit(db, user, '创建文档', doc_id)
            return {'id': doc_id}
        result = []
        favorites = {r['doc_id'] for r in db.execute('SELECT doc_id FROM favorites WHERE user_id=?', (user['id'],))}
        directory_scope = workspace.directory_scope(db, query['directory_id']) if query.get('directory_id') else None
        visible = []
        for row in db.execute("SELECT d.*,v.label,COALESCE((SELECT s.body FROM office_sessions s WHERE s.doc_id=d.id AND s.version_id=d.current_version AND s.saved IS NOT NULL AND s.state IN ('editing','ready','error') ORDER BY s.updated DESC LIMIT 1),v.body) AS body,u.username AS owner FROM documents d JOIN versions v ON v.id=d.current_version JOIN users u ON u.id=d.owner_id WHERE d.id NOT IN (SELECT doc_id FROM legacy_materials) ORDER BY d.updated DESC,d.id DESC"):
            try:
                access = document(db, user, row['id'])
            except Error:
                continue
            item = dict(row); item.pop('body')
            item['favorite'] = row['id'] in favorites
            if query.get('favorite') == '1' and not item['favorite']:
                continue
            item['is_archived'] = bool(access['is_archived'])
            visible.append(item)
            searchable = (row['title'] + row['number'] + row['body']).lower()
            if query.get('q', '').lower() not in searchable:
                continue
            if any(str(row[k]) != query[k] for k in ('state', 'owner_id') if query.get(k)):
                continue
            if directory_scope is not None and row['directory_id'] not in directory_scope:
                continue
            if query.get('tag', '') not in row['tags']:
                continue
            if query.get('from') and row['updated'] < query['from']:
                continue
            if query.get('to') and row['updated'][:10] > query['to']:
                continue
            result.append(item)
        if query.get('sort') == 'title':
            result.sort(key=lambda r: r['title'])
        return result
    if len(parts) >= 3 and parts[:2] == ['api', 'documents']:
        doc_id = int(parts[2])
        doc = document(db, user, doc_id)
        action = parts[3] if len(parts) > 3 else ''
        if action == 'favorite' and method == 'POST':
            return workspace.favorite(core, db, user, doc, data)
        if action in ('issues','issue-reply','issue-state'):
            return review_issues.route(core,db,user,doc,action,method,data)
        if action in ('opinion','resolve','evaluate','comments','comment-settings'):
            raise Error('已统一到评审意见，请使用评审事项与回复',410)
        if action in ('members','discussion-settings'):
            return accounts.route(core, db, user, doc, action, method, data)
        if method == 'POST' and action in ('fork','merge','merge-preview','archive-branch','restore-branch','delete-version','version','restore','schedule','cancel-schedule'):
            raise Error('旧分支及多状态流程已停用，请使用统一在编稿和三阶段流程', 410)
        if method == 'POST' and action in ('transition','cycle-settings','next-cycle'):
            if db.execute('SELECT 1 FROM legacy_materials WHERE doc_id=?', (doc_id,)).fetchone():
                raise Error('旧流程资料只读', 409)
            return lifecycle.route(core, db, user, doc, action, data)

        if action == 'shares':
            document(db, user, doc_id, True)
            if method == 'POST':
                editable(doc)
                key = db.execute('SELECT value FROM share_signing_key WHERE id=1').fetchone()['value']
                def link_token(link_id):
                    return str(link_id) + '.' + hmac.new(key.encode(), (str(doc_id) + ':' + str(link_id)).encode(), hashlib.sha256).hexdigest()
                existing = db.execute('SELECT id,token_hash FROM share_links WHERE doc_id=? AND revoked=0 ORDER BY id DESC LIMIT 1', (doc_id,)).fetchone()
                if existing:
                    token = link_token(existing['id'])
                    if hmac.compare_digest(hashlib.sha256(token.encode()).hexdigest(), existing['token_hash']):
                        return {'token': token, 'id': existing['id']}
                link_id = db.execute('INSERT INTO share_links(doc_id,token_hash,creator_id,created) VALUES(?,?,?,?)', (doc_id, secrets.token_hex(32), user['id'], now())).lastrowid
                token = link_token(link_id)
                db.execute('UPDATE share_links SET token_hash=? WHERE id=?', (hashlib.sha256(token.encode()).hexdigest(), link_id))
                audit(db, user, '生成协作分享链接', doc_id, 'share=' + str(link_id))
                return {'token': token, 'id': link_id}
            return [dict(r) for r in db.execute('SELECT id,revoked,created FROM share_links WHERE doc_id=? ORDER BY id DESC', (doc_id,))]
        if action == 'share-revoke' and method == 'POST':
            document(db, user, doc_id, True)
            if data.get('all') is True:
                db.execute('UPDATE share_links SET revoked=1 WHERE doc_id=?', (doc_id,))
            else:
                db.execute('UPDATE share_links SET revoked=1 WHERE id=? AND doc_id=?', (data.get('id'), doc_id))
            audit(db, user, '撤销协作分享链接', doc_id)
            return {'ok': True}
        if action == 'office-compare' and method == 'POST':
            return onlyoffice.compare(core, db, user, doc, data)
        if action == 'office-open' and method == 'POST':
            return onlyoffice.open_editor(core, db, user, doc, data)
        if action == 'office-status' and method == 'GET':
            return onlyoffice.session_info(core, db, user, doc)
        if action == 'office-checkpoint' and method == 'POST':
            return onlyoffice.checkpoint(core, db, user, doc, data)
        if action == 'office-upload' and method == 'POST':
            return onlyoffice.upload(core, db, user, doc, data)
        if action == 'office-abandon' and method == 'POST':
            require_role(user, 'admin')
            note = required(data, 'note', 2000)
            db.execute("UPDATE office_sessions SET state='committed',updated=? WHERE key=? AND doc_id=?", (now(), data.get('key'), doc_id))
            audit(db, user, '终止 Office 编辑会话', doc_id, note)
            return {'ok': True}
        if action == 'collab':
            onlyoffice.guard(core, db, doc, 'collab')
            return collab_route(db, user, doc, method, query, data)
        if method == 'GET':
            if action == 'diff':
                versions = []
                for key in ('a', 'b'):
                    row = db.execute('SELECT * FROM versions WHERE id=? AND doc_id=?', (query.get(key), doc_id)).fetchone()
                    if not row:
                        raise Error('对比版本不存在', 404)
                    versions.append(row)
                return {'diff': '\n'.join(difflib.unified_diff(versions[0]['body'].splitlines(), versions[1]['body'].splitlines(), fromfile=versions[0]['label'], tofile=versions[1]['label'], lineterm='')) or '正文无差异（附件需单独核查）'}
            if action:
                raise Error('接口不存在', 404)
            result = dict(doc)
            result['cycle'] = lifecycle.settings(db, doc_id)
            result['can_manage'] = accounts.manages(db,dict(user),doc)
            result['discussion_enabled'] = accounts.discussion(db,doc_id)
            result['favorite'] = bool(db.execute('SELECT 1 FROM favorites WHERE user_id=? AND doc_id=?', (user['id'], doc_id)).fetchone())
            result['issues'] = review_issues.listing(db,doc_id)
            archive_row = db.execute('SELECT a.archived_at,a.note,u.username AS archived_by FROM branch_archives a JOIN users u ON u.id=a.archived_by WHERE a.doc_id=? AND a.restored_at IS NULL', (doc_id,)).fetchone()
            result['archive'] = dict(archive_row) if archive_row else None
            result['file_branches'] = []
            try:
                document(db, user, doc_id, True)
                result['can_edit'] = True
            except Error:
                result['can_edit'] = False
            result['work_copy'] = None
            result['legacy_readonly'] = bool(db.execute('SELECT 1 FROM legacy_materials WHERE doc_id=?', (doc_id,)).fetchone())
            result.update(onlyoffice.session_info(core, db, user, doc))
            room = db.execute('SELECT body,revision FROM collab WHERE doc_id=? AND version_id=?', (doc_id, doc['current_version'])).fetchone()
            result['collab_dirty'] = bool(room and room['body'] != db.execute('SELECT body FROM versions WHERE id=?', (doc['current_version'],)).fetchone()['body'])
            result['versions'] = [dict(r) for r in db.execute('SELECT * FROM versions WHERE doc_id=? AND id NOT IN (SELECT version_id FROM deleted_versions) ORDER BY id DESC', (doc_id,))]
            working = db.execute("SELECT body FROM office_sessions WHERE doc_id=? AND version_id=? AND saved IS NOT NULL AND state IN ('editing','ready','error') ORDER BY updated DESC LIMIT 1", (doc_id, doc['current_version'])).fetchone()
            result['working_body'] = working['body'] if working and working['body'] is not None else None
            result['version_graph'] = {'nodes': [], 'edges': []}
            result['legacy_materials'] = []
            for legacy in db.execute('SELECT d.id,d.title FROM legacy_materials l JOIN documents d ON d.id=l.doc_id WHERE l.root_id=?', (doc_id,)):
                try:
                    document(db, user, legacy['id'])
                    result['legacy_materials'].append(dict(legacy))
                except Error:
                    pass
            result['transitions'] = [dict(r) for r in db.execute('SELECT * FROM transitions WHERE doc_id=? ORDER BY id DESC', (doc_id,))]
            result['attachments'] = [{k:r[k] for k in ('id','version_id','name','sha256')} | {'size':object_store.size(r['data'])} for r in db.execute('SELECT a.id,a.version_id,a.name,a.sha256,a.data FROM attachments a JOIN versions v ON a.version_id=v.id WHERE v.doc_id=?', (doc_id,))]
            result['schedules'] = [dict(r) for r in db.execute('SELECT * FROM schedules WHERE doc_id=? ORDER BY id DESC', (doc_id,))]
            return result
        current = db.execute('SELECT * FROM versions WHERE id=?', (doc['current_version'],)).fetchone()
        room = db.execute('SELECT * FROM collab WHERE doc_id=? AND version_id=?', (doc_id, current['id'])).fetchone()
        if action == 'transition' and data.get('target') == 'review' and user['id'] != doc['owner_id']:
            raise Error('只有最初创建者可以提交管理员审核', 403)
        if action in ('save', 'version', 'restore', 'attachment', 'delete-attachment', 'metadata', 'transition', 'collab-checkpoint'):
            onlyoffice.guard(core, db, doc, action)
        if action == 'version' and 'body' in data and data['body'] != current['body'] and db.execute('SELECT 1 FROM office_files WHERE version_id=?', (current['id'],)).fetchone():
            raise Error('Word 新版本的正文请通过 ONLYOFFICE 编辑', 409)
        if action == 'collab-checkpoint':
            document(db, user, doc_id, True)
            editable(doc)
            if data.get('expected_version') != current['id'] or not room or data.get('revision') != room['revision']:
                raise Error('协作内容已更新，请同步后重新保存', 409)
            add_version(db, user, doc, {**data, 'body': room['body']}, current)
        elif action in ('save', 'version', 'restore', 'attachment', 'delete-attachment', 'metadata'):
            document(db, user, doc_id, True)
            if room and room['body'] != current['body'] and action != 'metadata':
                raise Error('存在已同步的协作修改，请先在实时协作窗口保存版本', 409)
            if action not in ('version', 'restore'):
                editable(doc)
            if action in ('version', 'restore'):
                if doc['state'] not in ('draft', 'published', 'approved'):
                    raise Error('当前状态不允许创建新版本', 409)
                source = current
                if action == 'restore':
                    source = db.execute('SELECT * FROM versions WHERE id=? AND doc_id=?', (data.get('source_id'), doc_id)).fetchone()
                    if not source:
                        raise Error('历史版本不存在', 404)
                add_version(db, user, doc, data, source)
            elif action == 'save':
                if data.get('expected_version') != doc['current_version']:
                    raise Error('版本已更新，请刷新后再编辑', 409)
                # Every save produces a snapshot, preventing lost history and concurrent overwrites.
                add_version(db, user, doc, data, current)
            elif action == 'metadata':
                db.execute('UPDATE documents SET title=?,tags=?,extra=?,updated=? WHERE id=?', (required(data, 'title', 300), str(data.get('tags', ''))[:1000], json.dumps(data.get('extra', {}), ensure_ascii=False), now(), doc_id))
            elif action == 'attachment':
                name, content = decode_file(data)
                vid = add_version(db, user, doc, data, current)
                db.execute('DELETE FROM attachments WHERE version_id=? AND name=?', (vid, name))
                db.execute('INSERT INTO attachments(version_id,name,data,sha256) VALUES(?,?,?,?)', (vid, name, object_store.put(content), hashlib.sha256(content).hexdigest()))
            else:
                attachment = db.execute('SELECT * FROM attachments WHERE id=? AND version_id=?', (data.get('attachment_id'), current['id'])).fetchone()
                if not attachment:
                    raise Error('附件不存在', 404)
                vid = add_version(db, user, doc, data, current)
                db.execute('DELETE FROM attachments WHERE version_id=? AND name=?', (vid, attachment['name']))
        else:
            raise Error('接口不存在', 404)
        audit(db, user, action, doc_id, json.dumps({k: v for k, v in data.items() if k not in ('data', 'body')}, ensure_ascii=False)[:4000])
        return {'ok': True}
    if path == '/api/import' and method == 'POST':
        require_role(user, 'admin', 'editor')
        name, content = decode_file(data)
        return {'body': extract(name, content)}
    raise Error('接口不存在', 404)


def publish_due():
    return lifecycle.tick(sys.modules[__name__])

def scheduler():
    while True:
        try:
            publish_due()
        except Exception as exc:
            print('定时发布失败，将重试：' + str(exc), flush=True)
        time.sleep(1)


class Handler(BaseHTTPRequestHandler):
    server_version = 'StandardsPilot/0.1'

    def log_message(self, fmt, *args):
        # Do not log URL parameters or credentials.
        pass

    def send(self, status, body, content_type='application/json; charset=utf-8', headers=None):
        if status < 400 and getattr(self, 'db', None) is not None:
            self.db.commit()
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        with connection() as policy_db:
            office_url = onlyoffice.settings(policy_db).get('public_url', '')
        parsed_office = urlparse(office_url)
        office_origin = (parsed_office.scheme + '://' + parsed_office.netloc) if office_url else ''
        if urlparse(self.path).path=='/captcha.html':
            # The vendor SDK creates transient bootstrap frames. Scope its own loading policy to this page.
            policy="frame-ancestors 'self';"
        else:
            policy="default-src 'self'; img-src 'self' data:; script-src 'self' "+office_origin+"; frame-src 'self' "+office_origin+"; style-src 'self' 'unsafe-inline'; object-src 'none'; frame-ancestors 'none'; base-uri 'self'"
        if policy:
            self.send_header('Content-Security-Policy',policy)
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.handle_request('GET')

    def do_POST(self):
        self.handle_request('POST')

    def handle_request(self, method):
        self.db = None
        try:
            parsed = urlparse(self.path)
            path = parsed.path
            if method == 'GET' and path in ('/legal/terms', '/legal/privacy', '/help'):
                return self.send(200, content_pages.page({'/legal/terms':'terms','/legal/privacy':'privacy','/help':'guide'}[path]), 'text/html; charset=utf-8')
            if method == 'GET' and path == '/api/policies':
                return self.send(200, content_pages.metadata())
            if method == 'GET' and path == '/healthz':
                with connection() as db:
                    db.execute('SELECT 1')
                if os.environ.get('REDIS_URL'):
                    shared_cache.client().ping()
                return self.send(200, {'ok': True})
            if method == 'GET' and path in ('/', '/app.js', '/style.css', '/collab.js', '/office.js','/captcha.html','/captcha.js','/onboarding.js','/logo.png'):
                filename = {'/': 'index.html', '/app.js': 'app.js', '/style.css': 'style.css', '/collab.js': 'collab.js', '/office.js': 'office.js','/captcha.html':'captcha.html','/captcha.js':'captcha.js','/onboarding.js':'onboarding.js','/logo.png':'logo.png'}[path]
                mime = {'/': 'text/html', '/app.js': 'text/javascript', '/style.css': 'text/css', '/collab.js': 'text/javascript', '/office.js': 'text/javascript','/captcha.html':'text/html','/captcha.js':'text/javascript','/onboarding.js':'text/javascript','/logo.png':'image/png'}[path]
                return self.send(200, (ROOT / 'web' / filename).read_bytes(), mime if path=='/logo.png' else mime + '; charset=utf-8')
            data = {}
            if method == 'POST':
                # Custom header + no CORS prevents cross-site state-changing requests.
                if path != '/api/office/callback' and self.headers.get('X-Requested-With') != 'StandardsPlatform':
                    raise Error('请求来源校验失败', 403)
                length = int(self.headers.get('Content-Length', '0'))
                if length < 0 or length > MAX_BODY:
                    raise Error('请求过大', 413)
                data = json.loads(self.rfile.read(length) or '{}')
                if not isinstance(data, dict):
                    raise Error('需要 JSON 对象')
            query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
            with connection() as db:
                self.db = db
                if path == '/api/office/callback' and method == 'POST':
                    return self.send(200, onlyoffice.callback(sys.modules[__name__], db, data, self.headers.get('Authorization', '')))
                if path == '/api/office/file' and method == 'GET':
                    return self.send(200, onlyoffice.file(sys.modules[__name__], db, query), 'application/vnd.openxmlformats-officedocument.wordprocessingml.document')
                cookie = SimpleCookie(self.headers.get('Cookie', ''))
                token = cookie['session'].value if 'session' in cookie else ''
                if path.startswith('/api/wechat/'):
                    raise Error('微信登录已停用，请使用手机号短信登录',410)
                if path == '/api/phone/status' and method == 'GET':
                    return self.send(200,phone_auth.status())
                if path in ('/api/phone/send-code','/api/phone/verify') and method == 'POST':
                    uid=shared_cache.user_id(db,token)
                    if path.endswith('/send-code'):
                        if data.get('purpose', 'login') != 'bind':
                            content_pages.require_acceptance(sys.modules[__name__], data)
                        key='sms:'+phone_auth.client_ip(self.client_address[0],self.headers)
                        if shared_cache.limited(self.server.attempts,key,20):
                            raise Error('请求过于频繁，请稍后重试',429)
                        result=phone_auth.send(sys.modules[__name__],db,data,phone_auth.client_ip(self.client_address[0],self.headers),uid)
                        return self.send(200,result)
                    challenge = db.execute('SELECT purpose FROM sms_challenges WHERE id=?', (str(data.get('challenge_id','')),)).fetchone()
                    binding = challenge and challenge['purpose'] == 'bind'
                    if not binding:
                        content_pages.require_acceptance(sys.modules[__name__], data)
                    result=phone_auth.verify(sys.modules[__name__],db,data,uid)
                    if result['status']!=200:
                        return self.send(result['status'],{'error':result['error']})
                    if not binding:
                        content_pages.record(sys.modules[__name__], db, result['user_id'], 'sms')
                    return self.send(200,{'ok':True},headers={'Set-Cookie':'session='+result['token']+'; HttpOnly; SameSite=Strict; Path=/'+('; Secure' if os.environ.get('STANDARD_COOKIE_SECURE')=='1' else '')})
                if path == '/api/login' and method == 'POST':
                    content_pages.require_acceptance(sys.modules[__name__], data)
                    key = 'login:'+phone_auth.client_ip(self.client_address[0],self.headers)
                    if shared_cache.limited(self.server.attempts,key,15):
                        raise Error('登录尝试过多，请 5 分钟后重试', 429)
                    username, password = required(data, 'username', 80), required(data, 'password', 200)
                    account = db.execute('SELECT * FROM users WHERE username=? OR (phone=? AND phone_verified=1)', (username,username)).fetchone()
                    stored = account['password'] if account else password_hash('dummy')
                    if not hmac.compare_digest(password_hash(password, stored.split(':')[0]), stored) or not account:
                        raise Error('账号或密码错误', 401)
                    content_pages.record(sys.modules[__name__], db, account['id'], 'password')
                    token = secrets.token_urlsafe(32)
                    db.execute('DELETE FROM sessions WHERE expires<?', (time.time(),))
                    shared_cache.create(db,token,account['id'],time.time()+8*3600)
                    audit(db, account, '登录')
                    return self.send(200, {'ok': True}, headers={'Set-Cookie': 'session=' + token + '; HttpOnly; SameSite=Strict; Path=/' + ('; Secure' if os.environ.get('STANDARD_COOKIE_SECURE') == '1' else '')})
                user = db.execute('SELECT id,username,role FROM users WHERE id=?', (shared_cache.user_id(db,token),)).fetchone()
                if not user:
                    raise Error('请先登录', 401)
                if method == 'POST':
                    db.execute('BEGIN IMMEDIATE')
                if path == '/api/logout' and method == 'POST':
                    shared_cache.remove(db,token)
                    return self.send(200, {'ok': True}, headers={'Set-Cookie': 'session=; Max-Age=0; HttpOnly; SameSite=Strict; Path=/'})
                if path == '/api/backup' and method == 'GET':
                    require_role(user, 'admin')
                    if database.production():
                        raise Error('云端备份请使用腾讯云 PostgreSQL 备份及 COS 版本控制；本接口仅用于本地 SQLite',409)
                    audit(db, user, '备份导出')
                    db.commit()
                    with sqlite3.connect(':memory:') as copy:
                        db.backup(copy)
                        copy.execute('PRAGMA secure_delete=ON')
                        copy.execute('DELETE FROM sessions')
                        phone_auth.clear_ephemeral(copy)
                        copy.commit()
                        payload = copy.serialize()
                    return self.send(200, payload, 'application/octet-stream', {'Content-Disposition': 'attachment; filename="standards-backup.db"'})
                if path == '/api/download' and method == 'GET':
                    doc = document(db, user, int(query.get('doc_id', '0')))
                    vid = int(query.get('version_id') or doc['release_version'] or doc['current_version'])
                    if query.get('working') == '1':
                        vid = doc['current_version']
                    if db.execute('SELECT 1 FROM deleted_versions WHERE version_id=?', (vid,)).fetchone():
                        raise Error('版本已删除', 404)
                    version = db.execute('SELECT * FROM versions WHERE id=? AND doc_id=?', (vid, doc['id'])).fetchone()
                    if not version:
                        raise Error('版本不存在', 404)
                    content, name = version['body'].encode(), doc['number'] + '-' + version['label'] + '.txt'
                    office_file = db.execute('SELECT data FROM office_files WHERE version_id=?', (vid,)).fetchone()
                    if office_file and query.get('format') != 'txt':
                        content, name = office_file['data'], doc['number'] + '-' + version['label'] + '.docx'
                    if query.get('working') == '1' and query.get('format') != 'txt':
                        working = db.execute("SELECT saved FROM office_sessions WHERE doc_id=? AND version_id=? AND saved IS NOT NULL AND state IN ('editing','ready','error') ORDER BY updated DESC LIMIT 1", (doc['id'], vid)).fetchone()
                        if working:
                            content, name = working['saved'], doc['number'] + '-' + version['label'] + '.docx'
                    if query.get('office_key'):
                        saved = db.execute('SELECT saved,source FROM office_sessions WHERE key=? AND doc_id=?', (query['office_key'], doc['id'])).fetchone()
                        if not saved:
                            raise Error('Office 会话不存在', 404)
                        content, name = saved['saved'] or saved['source'], doc['number'] + '-office-draft.docx'
                    if query.get('attachment_id'):
                        file = db.execute('SELECT * FROM attachments WHERE id=? AND version_id=?', (query['attachment_id'], vid)).fetchone()
                        if not file:
                            raise Error('附件不存在', 404)
                        content, name = file['data'], file['name']
                    audit(db, user, '下载', doc['id'], 'version=' + str(vid) + '; ' + name)
                    return self.send(200, object_store.get(content), 'application/octet-stream', {'Content-Disposition': "attachment; filename*=UTF-8''" + quote(name)})
                result = route(db, user, method, path, query, data)
                self.send(200, result)
        except Error as exc:
            self.send(exc.status, {'error': exc.message})
        except sqlite3.IntegrityError:
            self.send(409, {'error': '编号、版本号或用户名重复，或关联数据无效'})
        except (ValueError, TypeError, KeyError):
            self.send(400, {'error': '请求参数格式错误'})
        except Exception as exc:
            print(type(exc).__name__ + ': ' + str(exc), flush=True)
            self.send(500, {'error': '服务异常，请查看服务端日志'})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--backup', help='将一致性数据库快照写入新文件，可由任务计划定期调用')
    parser.add_argument('--restore', help='停服后恢复指定数据库；保留恢复前备份')
    args = parser.parse_args()
    if database.production() or os.environ.get('STANDARD_ENV') == 'production':
        if args.restore or args.backup:
            raise RuntimeError('PostgreSQL 请使用腾讯云备份或 pg_dump/pg_restore，不能导入 SQLite 备份')
        from production_check import check
        check()
    if args.restore:
        with sqlite3.connect(args.restore) as source:
            if source.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise RuntimeError('备份完整性校验失败')
            tables = {r[0] for r in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {'users', 'documents', 'versions', 'attachments', 'audit', 'sessions'} <= tables:
                raise RuntimeError('不是平台备份文件')
            DB.parent.mkdir(parents=True, exist_ok=True)
            if DB.exists():
                with sqlite3.connect(DB) as old, sqlite3.connect(str(DB) + '.before-restore-' + str(time.time_ns())) as saved:
                    old.backup(saved)
            with sqlite3.connect(DB) as target:
                source.backup(target)
                target.execute('PRAGMA secure_delete=ON')
                target.execute('DELETE FROM sessions')
                phone_auth.clear_ephemeral(target)
                target.commit()
        print('恢复成功；启动后使用备份中的账号登录')
        return
    init()
    if args.backup:
        target_path = Path(args.backup)
        if target_path.exists():
            raise RuntimeError('备份目标已存在，拒绝覆盖')
        target_path.parent.mkdir(parents=True, exist_ok=True)
        with connection() as source, sqlite3.connect(target_path) as target:
            source.backup(target)
            target.execute('PRAGMA secure_delete=ON')
            target.execute('DELETE FROM sessions')
            phone_auth.clear_ephemeral(target)
            target.commit()
        print('备份成功：' + str(target_path))
        return
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.attempts = {}
    threading.Thread(target=scheduler, daemon=True).start()
    threading.Thread(target=onlyoffice.autosave_loop, args=(sys.modules[__name__],), daemon=True).start()
    print(f'平台已启动：http://{args.host}:{args.port}', flush=True)
    server.serve_forever()


if __name__ == '__main__':
    main()
