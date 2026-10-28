"""Fixed, flat standard categories and account-scoped workspace settings."""
import json

TAXONOMY = ('基础通用标准', '机器人标准', '国产操作系统标准', '适配与互操作标准', '测试与评价标准', '编制评审与发布材料', '参考与配套资料')


def init(core, db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS workspace_migrations(key TEXT PRIMARY KEY);
    CREATE TABLE IF NOT EXISTS favorites(user_id INTEGER REFERENCES users(id),doc_id INTEGER REFERENCES documents(id),created TEXT NOT NULL,PRIMARY KEY(user_id,doc_id));
    CREATE TABLE IF NOT EXISTS user_onboarding(user_id INTEGER PRIMARY KEY REFERENCES users(id),state TEXT NOT NULL CHECK(state IN ('done','skipped')),updated TEXT NOT NULL);
    ''')
    if db.execute("SELECT 1 FROM workspace_migrations WHERE key='flat-directories-v2'").fetchone():
        return
    old = {r['id']: dict(r) for r in db.execute('SELECT * FROM directories ORDER BY id')}
    root = next((r for r in old.values() if r['name']=='标准文件' and r['parent_id'] is None), None)
    categories = {}
    for name in TAXONOMY:
        existing = root if name==TAXONOMY[0] and root else next((r for r in old.values() if r['name']==name), None)
        cid = existing['id'] if existing else db.execute('INSERT INTO directories(name) VALUES(?)', (name,)).lastrowid
        db.execute('UPDATE directories SET name=?,parent_id=NULL WHERE id=?', (name, cid))
        categories[name] = cid
    mapping = {}
    for original in old:
        current, seen, category = original, set(), TAXONOMY[0]
        while current in old and current not in seen:
            seen.add(current)
            if old[current]['name'] in categories:
                category = old[current]['name']; break
            current = old[current]['parent_id']
        mapping[original] = categories[category]
    directory_grants = {}
    for row in db.execute("SELECT * FROM grants WHERE scope='directory'"):
        directory_grants.setdefault(row['target_id'], []).append(dict(row))
    documents = db.execute('SELECT id,owner_id,directory_id FROM documents').fetchall()
    for doc in documents:
        current, seen, restrictions = doc['directory_id'], set(), []
        while current in old and current not in seen:
            seen.add(current)
            if current in directory_grants:
                restrictions.append({g['user_id'] for g in directory_grants[current] if g['access']=='edit' and g['user_id'] is not None})
            current = old[current]['parent_id']
        if restrictions:
            # Preserve the effective edit ACL before merging directory scopes. Read access is now shared.
            allowed = set.intersection(*restrictions)
            explicit = db.execute("SELECT user_id,access FROM grants WHERE scope='document' AND target_id=?", (doc['id'],)).fetchall()
            if explicit:
                allowed &= {g['user_id'] for g in explicit if g['access']=='edit'}
            db.execute("UPDATE grants SET access='read' WHERE scope='document' AND target_id=?", (doc['id'],))
            for uid in sorted(allowed):
                db.execute("INSERT INTO grants(scope,target_id,user_id,access) VALUES('document',?,?,'edit') ON CONFLICT(scope,target_id,user_id) DO UPDATE SET access='edit'", (doc['id'], uid))
            if not explicit and not allowed:
                db.execute("INSERT OR IGNORE INTO grants(scope,target_id,user_id,access) VALUES('document',?,?,'read')", (doc['id'], doc['owner_id']))
        db.execute('UPDATE documents SET directory_id=? WHERE id=?', (mapping.get(doc['directory_id'], categories[TAXONOMY[0]]), doc['id']))
    db.execute("DELETE FROM grants WHERE scope='directory'")
    db.execute('UPDATE directories SET parent_id=NULL')
    db.execute('DELETE FROM directories WHERE id NOT IN ('+','.join('?' for _ in categories)+')', tuple(categories.values()))
    db.execute("INSERT OR IGNORE INTO workspace_migrations VALUES('taxonomy-v1')")
    db.execute("INSERT INTO workspace_migrations VALUES('flat-directories-v2')")
    core.audit(db, {'username':'system'}, '简化标准体系目录', detail=json.dumps({'categories':list(TAXONOMY),'documents':len(documents),'previous':{str(i):{'name':r['name'],'parent_id':r['parent_id'],'category_id':mapping[i]} for i,r in old.items()}}, ensure_ascii=False))


def favorite(core, db, user, doc, data):
    if type(data.get('favorite')) is not bool:
        raise core.Error('收藏参数须为布尔值')
    if data['favorite']:
        db.execute('INSERT OR IGNORE INTO favorites VALUES(?,?,?)', (user['id'], doc['id'], core.now()))
    else:
        db.execute('DELETE FROM favorites WHERE user_id=? AND doc_id=?', (user['id'], doc['id']))
    return {'favorite': data['favorite']}


def directory_scope(db, directory_id):
    return {r['id'] for r in db.execute('WITH RECURSIVE subtree(id) AS (SELECT id FROM directories WHERE id=? UNION SELECT d.id FROM directories d JOIN subtree p ON d.parent_id=p.id) SELECT id FROM subtree', (int(directory_id),))}


def onboarding_state(db, uid):
    row = db.execute('SELECT state FROM user_onboarding WHERE user_id=?', (uid,)).fetchone()
    return row['state'] if row else None


def finish_onboarding(core, db, user, data):
    if data.get('state') not in ('done', 'skipped'):
        raise core.Error('教程状态无效')
    db.execute("INSERT INTO user_onboarding VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET state=CASE WHEN user_onboarding.state='done' THEN 'done' ELSE excluded.state END,updated=excluded.updated", (user['id'], data['state'], core.now()))
    return {'state': onboarding_state(db, user['id'])}
