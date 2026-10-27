"""Standard-system directory seed and private document bookmarks."""

TAXONOMY = {
    '基础通用标准': ['术语与定义', '标准体系与编制规则', '分类编码与元数据'],
    '机器人标准': ['体系架构与接口', '控制与通信', '功能安全与信息安全', '性能与可靠性'],
    '国产操作系统标准': ['系统基础与运行环境', '驱动与硬件支持', '安全配置与权限', '软件安装与部署'],
    '适配与互操作标准': ['处理器架构适配', '机器人与操作系统适配', '接口与协议互操作', '应用迁移与兼容性'],
    '测试与评价标准': ['测试方法与测试用例', '性能与稳定性测试', '安全测试与评价', '兼容性验证与验收'],
    '编制评审与发布材料': ['编制说明与征求意见', '评审记录与意见处理', '发布与废止替代材料'],
    '参考与配套资料': ['引用标准与参考文献', '图表与样例数据', '操作指南与模板'],
}


def init(core, db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS workspace_migrations(key TEXT PRIMARY KEY);
    CREATE TABLE IF NOT EXISTS favorites(user_id INTEGER REFERENCES users(id),doc_id INTEGER REFERENCES documents(id),created TEXT NOT NULL,PRIMARY KEY(user_id,doc_id));
    CREATE TABLE IF NOT EXISTS user_onboarding(user_id INTEGER PRIMARY KEY REFERENCES users(id),state TEXT NOT NULL CHECK(state IN ('done','skipped')),updated TEXT NOT NULL);
    ''')
    if db.execute("SELECT 1 FROM workspace_migrations WHERE key='taxonomy-v1'").fetchone():
        return
    root = db.execute("SELECT id FROM directories WHERE name='标准文件' AND parent_id IS NULL ORDER BY id LIMIT 1").fetchone()
    root_id = root['id'] if root else db.execute('INSERT INTO directories(name) VALUES(?)', ('标准文件',)).lastrowid
    for name, children in TAXONOMY.items():
        parent = db.execute('SELECT id FROM directories WHERE parent_id=? AND name=? ORDER BY id LIMIT 1', (root_id, name)).fetchone()
        parent_id = parent['id'] if parent else db.execute('INSERT INTO directories(name,parent_id) VALUES(?,?)', (name, root_id)).lastrowid
        for child in children:
            if not db.execute('SELECT 1 FROM directories WHERE parent_id=? AND name=?', (parent_id, child)).fetchone():
                db.execute('INSERT INTO directories(name,parent_id) VALUES(?,?)', (child, parent_id))
    db.execute("INSERT INTO workspace_migrations VALUES('taxonomy-v1')")


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
