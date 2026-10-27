"""Single working document, public review, immutable published releases."""
from datetime import datetime, timezone
import time
import json


def init(core, db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS document_cycles(doc_id INTEGER PRIMARY KEY REFERENCES documents(id),edit_hours REAL NOT NULL DEFAULT 0,public_hours REAL NOT NULL DEFAULT 0,deadline TEXT,approved_version INTEGER,manager_id INTEGER,last_error TEXT NOT NULL DEFAULT '');
    CREATE TABLE IF NOT EXISTS legacy_materials(doc_id INTEGER PRIMARY KEY REFERENCES documents(id),root_id INTEGER NOT NULL REFERENCES documents(id));
    CREATE TABLE IF NOT EXISTS lifecycle_migrations(id INTEGER PRIMARY KEY);
    ''')
    columns={r['name'] for r in db.execute('PRAGMA table_info(document_cycles)')}
    if 'edit_until' not in columns:
        db.execute('ALTER TABLE document_cycles ADD COLUMN edit_until TEXT')
        db.execute('ALTER TABLE document_cycles ADD COLUMN public_until TEXT')
        db.execute("UPDATE document_cycles SET edit_until=deadline WHERE doc_id IN (SELECT id FROM documents WHERE state='draft')")
        db.execute("UPDATE document_cycles SET public_until=deadline WHERE doc_id IN (SELECT id FROM documents WHERE state='public')")
    if db.execute('SELECT 1 FROM lifecycle_migrations WHERE id=1').fetchone():
        return
    parents = {r['copy_doc_id']: r['parent_doc_id'] for r in db.execute('SELECT * FROM work_copies')}
    for doc, parent in parents.items():
        if parent:
            root, seen = parent, {doc}
            while parents.get(root) and root not in seen:
                seen.add(root)
                root = parents[root]
            db.execute('INSERT OR IGNORE INTO legacy_materials VALUES(?,?)', (doc, root))
    db.execute("UPDATE documents SET state='public' WHERE state IN ('review','approved','scheduled')")
    db.execute("UPDATE documents SET state='published' WHERE state='obsolete'")
    db.execute('INSERT OR IGNORE INTO document_cycles(doc_id) SELECT id FROM documents')
    db.execute("UPDATE schedules SET status='cancelled' WHERE status='pending'")
    # Remove the old automatically-created private owner ACL, retaining explicit ACL changes.
    db.execute("DELETE FROM grants WHERE scope='document' AND target_id NOT IN (SELECT doc_id FROM audit WHERE action='权限变更' AND doc_id IS NOT NULL)")
    db.execute('INSERT INTO lifecycle_migrations VALUES(1)')


def settings(db, doc_id):
    row = db.execute('SELECT * FROM document_cycles WHERE doc_id=?', (doc_id,)).fetchone()
    return dict(row) if row else {'edit_hours': 0, 'public_hours': 0, 'deadline': None, 'approved_version': None, 'last_error': '', 'edit_until':None, 'public_until':None}


def record(core, db, user, doc, action, note):
    db.execute('INSERT INTO transitions(doc_id,version_id,actor,action,note,created) VALUES(?,?,?,?,?,?)', (doc['id'], doc['current_version'], user['username'], action, note, core.now()))
    core.audit(db, user, action, doc['id'], note)
    for u in db.execute("SELECT u.id,u.username,u.role FROM users u WHERE u.role IN ('admin','editor','reviewer') OR EXISTS(SELECT 1 FROM document_members m WHERE m.doc_id=? AND m.phone=u.phone AND u.phone_verified=1 AND m.revoked=0)",(doc['id'],)).fetchall():
        try:
            core.document(db, dict(u), doc['id'])
        except core.Error:
            continue
        db.execute('INSERT INTO todos(user_id,doc_id,message,created) VALUES(?,?,?,?)', (u['id'], doc['id'], doc['title'] + '：' + note, core.now()))


def deadline(core, db, doc_id, state):
    cfg = settings(db, doc_id)
    due = cfg['edit_until'] if state=='draft' else cfg['public_until'] if state=='public' else None
    db.execute("UPDATE document_cycles SET deadline=?,last_error='' WHERE doc_id=?", (due, doc_id))


def transition(core, db, user, doc, data):
    core.accounts.require_manager(core,db,user,doc)
    target = data.get('target')
    note = core.required(data, 'note', 3000)
    if target not in ('draft', 'public', 'published', 'approved'):
        raise core.Error('仅支持编辑、公开、发布三个状态')
    allowed = (doc['state'], target) in (('draft', 'public'), ('public', 'draft'), ('public', 'published'), ('public', 'approved'))
    if not allowed:
        raise core.Error('不允许此状态切换；发布后请开启下一轮编辑', 409)
    core.onlyoffice.guard(core, db, doc, 'phase')
    current = db.execute('SELECT * FROM versions WHERE id=?', (doc['current_version'],)).fetchone()
    live = db.execute('SELECT body FROM collab WHERE doc_id=? AND version_id=?', (doc['id'], current['id'])).fetchone()
    if live and live['body'] != current['body']:
        raise core.Error('请先保存实时协作修改再切换状态', 409)
    db.execute('INSERT OR IGNORE INTO document_cycles(doc_id) VALUES(?)', (doc['id'],))
    if target in ('approved', 'published'):
        if db.execute("SELECT 1 FROM review_issues WHERE doc_id=? AND state='open' AND blocking=1", (doc['id'],)).fetchone():
            raise core.Error('存在未关闭的评审事项', 409)
        cfg = settings(db, doc['id'])
        if target == 'published' and cfg['approved_version'] != current['id']:
            raise core.Error('请先由管理员批准当前稿件发布；编辑后须重新批准', 409)
    if target == 'approved':
        db.execute('UPDATE document_cycles SET approved_version=?,manager_id=?,last_error=\'\' WHERE doc_id=?', (current['id'], user['id'], doc['id']))
    else:
        db.execute('UPDATE documents SET state=?,release_version=CASE WHEN ?=\'published\' THEN current_version ELSE release_version END,updated=? WHERE id=?', (target, target, core.now(), doc['id']))
        db.execute('UPDATE document_cycles SET approved_version=NULL,manager_id=? WHERE doc_id=?', (user['id'], doc['id']))
        if target=='draft':
            db.execute('UPDATE document_cycles SET edit_until=NULL WHERE doc_id=? AND edit_until<=?',(doc['id'],core.now()))
        deadline(core, db, doc['id'], target)
    record(core, db, user, doc, target, note)
    return {'ok': True}


def route(core, db, user, doc, action, data):
    if action == 'transition':
        return transition(core, db, user, doc, data)
    core.accounts.require_manager(core,db,user,doc)
    if action == 'cycle-settings':
        if 'edit_hours' in data or 'public_hours' in data:
            raise core.Error('请设置具体的截止日期时间，不再使用小时数')
        values=[]
        for key in ('edit_until','public_until'):
            value=data.get(key)
            if not value:
                values.append(None)
                continue
            try:
                dt=datetime.fromisoformat(str(value))
                if dt.tzinfo is None:
                    raise ValueError()
                dt=dt.astimezone(timezone.utc)
                if (key=='public_until' or doc['state']=='draft') and dt.timestamp()<=time.time():
                    raise ValueError()
                values.append(dt.isoformat(timespec='seconds'))
            except (ValueError,TypeError):
                raise core.Error('截止时间须为有效的未来日期时间，并包含时区')
        if all(values) and values[1]<=values[0]:
            raise core.Error('公开截止时间须晚于编辑截止时间')
        db.execute('INSERT INTO document_cycles(doc_id,edit_until,public_until,manager_id) VALUES(?,?,?,?) ON CONFLICT(doc_id) DO UPDATE SET edit_until=excluded.edit_until,public_until=excluded.public_until,edit_hours=0,public_hours=0,manager_id=excluded.manager_id',(doc['id'],*values,user['id']))
        deadline(core,db,doc['id'],doc['state'])
        core.audit(db,user,'设置阶段截止时间',doc['id'],json.dumps(dict(zip(('edit_until','public_until'),values))))
        return {'ok': True}
    if action == 'next-cycle':
        if doc['state'] != 'published' or not doc['release_version']:
            raise core.Error('仅最新发布版本可开启下一轮编辑', 409)
        core.onlyoffice.guard(core, db, doc, 'next-cycle')
        source = db.execute('SELECT * FROM versions WHERE id=?', (doc['release_version'],)).fetchone()
        core.add_version(db, user, doc, data, source)
        db.execute('INSERT OR IGNORE INTO document_cycles(doc_id,manager_id) VALUES(?,?)', (doc['id'], user['id']))
        db.execute('UPDATE document_cycles SET approved_version=NULL,manager_id=?,edit_until=NULL,public_until=NULL WHERE doc_id=?', (user['id'], doc['id']))
        deadline(core, db, doc['id'], 'draft')
        record(core, db, user, core.document(db, user, doc['id']), 'draft', '从最新发布版本开启下一轮编辑')
        return {'ok': True}
    raise core.Error('接口不存在', 404)


def tick(core):
    with core.connection() as db:
        db.execute('BEGIN IMMEDIATE')
        for row in db.execute('SELECT c.*,d.state FROM document_cycles c JOIN documents d ON d.id=c.doc_id WHERE c.deadline IS NOT NULL AND c.deadline<=? AND d.id NOT IN (SELECT doc_id FROM legacy_materials) AND d.id NOT IN (SELECT doc_id FROM deleted_documents)', (core.now(),)).fetchall():
            user = db.execute("SELECT id,username,role FROM users WHERE id=?", (row['manager_id'],)).fetchone()
            try:
                if not user:
                    raise core.Error('定时切换管理员权限已失效')
                doc = core.document(db, dict(user), row['doc_id'])
                transition(core, db, dict(user), doc, {'target': 'public' if doc['state']=='draft' else 'published', 'note': '阶段截止时间到期，自动切换'})
            except core.Error as exc:
                if row['last_error'] != exc.message:
                    db.execute('UPDATE document_cycles SET last_error=? WHERE doc_id=?', (exc.message, row['doc_id']))
                    core.audit(db, {'username': 'scheduler'}, '阶段切换等待', row['doc_id'], exc.message)
                    if user:
                        db.execute('INSERT INTO todos(user_id,doc_id,message,created) VALUES(?,?,?,?)', (user['id'], row['doc_id'], '阶段到期未切换：' + exc.message, core.now()))
