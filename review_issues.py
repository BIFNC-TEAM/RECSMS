"""One review discussion system, with preserved legacy feedback."""


def init(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS review_issues(id INTEGER PRIMARY KEY,doc_id INTEGER REFERENCES documents(id),version_id INTEGER REFERENCES versions(id),title TEXT NOT NULL,body TEXT NOT NULL,author TEXT NOT NULL,user_id INTEGER REFERENCES users(id),state TEXT NOT NULL DEFAULT 'open',resolution TEXT NOT NULL DEFAULT '',blocking INTEGER NOT NULL DEFAULT 1,created TEXT NOT NULL,updated TEXT NOT NULL,legacy_source TEXT,legacy_id INTEGER,UNIQUE(legacy_source,legacy_id));
    CREATE TABLE IF NOT EXISTS issue_events(id INTEGER PRIMARY KEY,issue_id INTEGER REFERENCES review_issues(id),author TEXT NOT NULL,user_id INTEGER REFERENCES users(id),kind TEXT NOT NULL,body TEXT NOT NULL,created TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS review_issues_doc ON review_issues(doc_id,state);
    ''')
    # Import once per original record; original tables remain untouched for audit/backup.
    for row in db.execute('SELECT * FROM opinions').fetchall():
        uid=db.execute('SELECT id FROM users WHERE username=?',(row['author'],)).fetchone()
        db.execute("INSERT OR IGNORE INTO review_issues(doc_id,version_id,title,body,author,user_id,state,resolution,blocking,created,updated,legacy_source,legacy_id) VALUES(?,?,?,?,?,?,?,?,1,?,?,?,?)",(row['doc_id'],row['version_id'],row['body'][:100] or '历史评审意见',row['body'],row['author'],uid['id'] if uid else None,'open' if row['status']=='pending' else 'closed',row['resolution'],row['created'],row['created'],'opinion',row['id']))
    for source,table in [('comment','document_comments'),('evaluation','evaluations')]:
        for row in db.execute('SELECT x.*,u.username FROM '+table+' x JOIN users u ON u.id=x.user_id'+(' WHERE x.deleted=0' if source=='comment' else '')).fetchall():
            prefix='历史评论' if source=='comment' else '历史评价（'+str(row['score'])+'分）'
            db.execute("INSERT OR IGNORE INTO review_issues(doc_id,version_id,title,body,author,user_id,state,resolution,blocking,created,updated,legacy_source,legacy_id) VALUES(?,?,?,?,?,?,'closed','历史反馈留存',0,?,?,?,?)",(row['doc_id'],row['version_id'],prefix+'：'+row['body'][:80],row['body'],row['username'],row['user_id'],row['created'],row['created'],source,row['id']))


def listing(db,doc_id):
    rows=[dict(r) for r in db.execute('SELECT * FROM review_issues WHERE doc_id=? ORDER BY id DESC',(doc_id,))]
    for row in rows:
        row['events']=[dict(r) for r in db.execute('SELECT * FROM issue_events WHERE issue_id=? ORDER BY id',(row['id'],))]
    return rows


def route(core,db,user,doc,action,method,data):
    user=dict(user)
    if action=='issues' and method=='GET':
        return listing(db,doc['id'])
    if method!='POST':
        raise core.Error('需要POST请求',405)
    if action in ('issues','issue-reply') and not core.accounts.discussion(db,doc['id']):
        raise core.Error('评审讨论已暂停',409)
    if action=='issues':
        vid=int(data.get('version_id') or doc['current_version'])
        if not db.execute('SELECT 1 FROM versions WHERE id=? AND doc_id=? AND id NOT IN (SELECT version_id FROM deleted_versions)',(vid,doc['id'])).fetchone():
            raise core.Error('关联版本不存在',404)
        title=core.required(data,'title',200);body=core.required(data,'body',10000)
        issue=db.execute('INSERT INTO review_issues(doc_id,version_id,title,body,author,user_id,created,updated) VALUES(?,?,?,?,?,?,?,?)',(doc['id'],vid,title,body,user['username'],user['id'],core.now(),core.now())).lastrowid
        event='新建评审事项'
    else:
        row=db.execute('SELECT * FROM review_issues WHERE id=? AND doc_id=?',(data.get('issue_id'),doc['id'])).fetchone()
        if not row:
            raise core.Error('评审事项不存在',404)
        issue=row['id']
        if action=='issue-reply':
            body=core.required(data,'body',10000);event='回复评审事项'
            kind='comment'
        elif action=='issue-state':
            if user['id']!=row['user_id'] and not core.accounts.manages(db,user,doc):
                raise core.Error('只有发起人或文档管理者可以关闭或重新打开事项',403)
            state=data.get('state')
            if state not in ('open','closed') or state==row['state']:
                raise core.Error('事项状态无效或已变化',409)
            body=core.required(data,'body',3000)
            kind='reopened' if state=='open' else 'closed'
            db.execute('UPDATE review_issues SET state=?,resolution=?,blocking=1 WHERE id=?',(state,'' if state=='open' else body,issue))
            event='重新打开评审事项' if state=='open' else '关闭评审事项'
        else:
            raise core.Error('接口不存在',404)
        db.execute('INSERT INTO issue_events(issue_id,author,user_id,kind,body,created) VALUES(?,?,?,?,?,?)',(issue,user['username'],user['id'],kind,body,core.now()))
        db.execute('UPDATE review_issues SET updated=? WHERE id=?',(core.now(),issue))
    db.execute('UPDATE document_cycles SET approved_version=NULL WHERE doc_id=?',(doc['id'],))
    core.audit(db,user,event,doc['id'],'issue='+str(issue))
    recipients={doc['owner_id']}
    for r in db.execute("SELECT DISTINCT user_id FROM issue_events WHERE issue_id=? UNION SELECT user_id FROM review_issues WHERE id=?",(issue,issue)):
        if r['user_id']:
            recipients.add(r['user_id'])
    recipients.update(r['id'] for r in db.execute("SELECT id FROM users WHERE role='admin'"))
    for uid in recipients-{user['id']}:
        account=db.execute('SELECT id,username,role FROM users WHERE id=?',(uid,)).fetchone()
        try:
            core.document(db,dict(account),doc['id'])
        except core.Error:
            continue
        db.execute('INSERT INTO todos(user_id,doc_id,message,created) VALUES(?,?,?,?)',(uid,doc['id'],event+' #'+str(issue)+' · '+doc['title'],core.now()))
    return {'ok':True,'id':issue}
