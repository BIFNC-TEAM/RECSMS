"""Verified mobile identities, scoped document membership, and discussion settings."""
import re


def init(db):
    columns={r['name'] for r in db.execute('PRAGMA table_info(users)')}
    if 'phone' not in columns:
        db.execute('ALTER TABLE users ADD COLUMN phone TEXT')
    if 'phone_verified' not in columns:
        db.execute('ALTER TABLE users ADD COLUMN phone_verified INTEGER NOT NULL DEFAULT 0')
    db.executescript('''
    CREATE UNIQUE INDEX IF NOT EXISTS users_unique_phone ON users(phone) WHERE phone IS NOT NULL;
    CREATE TABLE IF NOT EXISTS document_members(doc_id INTEGER REFERENCES documents(id),phone TEXT NOT NULL,permission TEXT NOT NULL CHECK(permission IN ('manage','edit')),invited_by INTEGER REFERENCES users(id),created TEXT NOT NULL,revoked INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(doc_id,phone));
    CREATE TABLE IF NOT EXISTS document_discussions(doc_id INTEGER PRIMARY KEY REFERENCES documents(id),enabled INTEGER NOT NULL DEFAULT 1);
    CREATE TABLE IF NOT EXISTS document_comments(id INTEGER PRIMARY KEY,doc_id INTEGER REFERENCES documents(id),version_id INTEGER REFERENCES versions(id),user_id INTEGER REFERENCES users(id),body TEXT NOT NULL,created TEXT NOT NULL,deleted INTEGER NOT NULL DEFAULT 0);
    ''')


def phone(core, value):
    number=re.sub(r'[\s()-]','',str(value or ''))
    if number.startswith('+86'):
        number=number[3:]
    if not re.fullmatch(r'1[3-9]\d{9}',number):
        raise core.Error('请输入有效的中国大陆手机号',400)
    return number


def member(db,user,doc_id):
    if not user.get('id'):
        return None
    row=db.execute('SELECT m.permission FROM document_members m JOIN users u ON u.phone=m.phone AND u.phone_verified=1 WHERE m.doc_id=? AND u.id=? AND m.revoked=0',(doc_id,user['id'])).fetchone()
    return row['permission'] if row else None


def manages(db,user,doc):
    return user['role']=='admin' or user.get('id')==doc['owner_id'] or member(db,user,doc['id'])=='manage'


def require_manager(core,db,user,doc):
    if not manages(db,dict(user),doc):
        raise core.Error('需要此文档的管理权限',403)


def discussion(db,doc_id):
    row=db.execute('SELECT enabled FROM document_discussions WHERE doc_id=?',(doc_id,)).fetchone()
    return not row or bool(row['enabled'])


def route(core,db,user,doc,action,method,data):
    user=dict(user)
    if action=='members':
        require_manager(core,db,user,doc)
        if method=='POST':
            number=phone(core,data.get('phone'))
            permission=data.get('permission')
            if permission not in ('manage','edit','remove'):
                raise core.Error('请选择可管理或可编辑')
            if permission=='remove':
                db.execute('UPDATE document_members SET revoked=1 WHERE doc_id=? AND phone=?',(doc['id'],number))
            else:
                db.execute('INSERT INTO document_members VALUES(?,?,?,?,?,0) ON CONFLICT(doc_id,phone) DO UPDATE SET permission=excluded.permission,invited_by=excluded.invited_by,created=excluded.created,revoked=0',(doc['id'],number,permission,user['id'],core.now()))
                account=db.execute('SELECT id FROM users WHERE phone=? AND phone_verified=1',(number,)).fetchone()
                if account:
                    db.execute('INSERT INTO todos(user_id,doc_id,message,created) VALUES(?,?,?,?)',(account['id'],doc['id'],'文档邀请：'+doc['title']+'（'+('可管理' if permission=='manage' else '可编辑')+'）',core.now()))
            core.audit(db,user,'更新文档成员',doc['id'],number[:3]+'****'+number[-4:]+' '+permission)
        return [dict(r) for r in db.execute('SELECT m.phone,m.permission,m.created,u.username,u.id AS user_id FROM document_members m LEFT JOIN users u ON u.phone=m.phone AND u.phone_verified=1 WHERE m.doc_id=? AND m.revoked=0 ORDER BY m.created DESC',(doc['id'],))]
    if action=='discussion-settings':
        if method!='POST':
            raise core.Error('需要POST请求',405)
        require_manager(core,db,user,doc)
        if type(data.get('enabled')) is not bool:
            raise core.Error('讨论开关参数无效')
        db.execute('INSERT INTO document_discussions VALUES(?,?) ON CONFLICT(doc_id) DO UPDATE SET enabled=excluded.enabled',(doc['id'],int(data['enabled'])))
        core.audit(db,user,'开放评审讨论' if data['enabled'] else '暂停评审讨论',doc['id'])
        return {'ok':True}
    raise core.Error('接口不存在',404)



def notify_invites(core,db,uid,number):
    for invitation in db.execute('SELECT d.id,d.title FROM document_members m JOIN documents d ON d.id=m.doc_id WHERE m.phone=? AND m.revoked=0',(number,)).fetchall():
        message='已加入受邀文档：'+invitation['title']
        if not db.execute('SELECT 1 FROM todos WHERE user_id=? AND doc_id=? AND message=?',(uid,invitation['id'],message)).fetchone():
            db.execute('INSERT INTO todos(user_id,doc_id,message,created) VALUES(?,?,?,?)',(uid,invitation['id'],message,core.now()))
