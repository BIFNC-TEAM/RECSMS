"""Add clearly labelled workflow fixtures through the platform's real routes."""
import base64
from datetime import datetime, timedelta, timezone
import json
import sqlite3
from pathlib import Path
import server
import onlyoffice


def main():
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    with sqlite3.connect(server.DB) as source, sqlite3.connect(server.DB.parent / ('pre-demo-' + stamp + '.db')) as backup:
        source.backup(backup)
    with server.connection() as db:
        admin = dict(db.execute("SELECT id,username,role FROM users WHERE role='admin' ORDER BY id LIMIT 1").fetchone())
        def api(user, path, data=None):
            return server.route(db, user, 'GET' if data is None else 'POST', '/api/' + path, {}, data or {})
        accounts = [('demo_writer', 'editor'), ('demo_reviewer', 'reviewer'), ('demo_reader', 'reader')]
        for name, role in accounts:
            if not db.execute('SELECT 1 FROM users WHERE username=?', (name,)).fetchone():
                api(admin, 'users', {'username': name, 'password': 'DemoTest@2026!', 'role': role})
        writer, reviewer, reader = [dict(db.execute('SELECT id,username,role FROM users WHERE username=?', (name,)).fetchone()) for name, _ in accounts]
        directory = db.execute("SELECT id FROM directories WHERE name='测试演示（流程验收）'").fetchone()
        if not directory:
            api(admin, 'directories', {'name': '测试演示（流程验收）'})
            directory = db.execute("SELECT id FROM directories WHERE name='测试演示（流程验收）'").fetchone()
        if db.execute("SELECT 1 FROM documents WHERE number='DEMO-FLOW-001'").fetchone():
            print('Demo scenarios already exist; no duplicate data added.')
            return
        rows = []
        def get(doc):
            return api(admin, f'documents/{doc}')
        def grant_reader(doc):
            api(admin, 'grants', {'scope': 'document', 'target_id': doc, 'user_id': reader['id'], 'access': 'read'})
        def create(n, title, purpose):
            doc = api(writer, 'documents', {'number': f'DEMO-FLOW-{n:03}', 'title': '【测试演示】' + title, 'directory_id': directory['id'], 'label': 'V0.1', 'tags': '测试演示,流程验收,机器人,国产操作系统', 'extra': {'数据用途': '人工功能测试', '测试场景': purpose}, 'note': '创建测试草稿：' + purpose, 'body': f'{title}\n\n1 范围\n本文件适用于机器人与国产操作系统兼容性验证。仅为平台功能测试材料。\n\n2 技术要求\n2.1 连续运行不少于24小时。\n2.2 接口响应时间不大于100毫秒。\n\n3 测试方法\n记录操作系统版本、CPU架构、测试环境和实际结果。\n\n4 验收与追溯\n评审意见完成闭环后，由管理员批准并发布。\n\n测试场景：{purpose}'})['id']
            grant_reader(doc)
            api(writer, f'documents/{doc}/attachment', {'name': '测试记录样例.csv', 'data': base64.b64encode('测试项,预期结果,实测结果,结论\n启动,小于60秒,42秒,通过\n连续运行,24小时无异常,24小时无异常,通过\n'.encode('utf-8-sig')).decode(), 'label': 'V0.2', 'note': '补充测试记录附件'})
            api(writer, f'documents/{doc}/attachment', {'name': '评审材料说明.docx', 'data': base64.b64encode(onlyoffice.make_docx(title + '\n测试演示附件\n包括测试环境、验收口径和评审依据。\n本材料仅用于功能测试，不作为实际标准发布。')).decode(), 'label': 'V0.3', 'note': '补充评审材料附件'})
            rows.append({'id': doc, 'title': title, 'purpose': purpose})
            return doc
        def move(doc, target, note):
            api(writer if target == 'review' else admin, f'documents/{doc}/transition', {'target': target, 'note': note})
            if target == 'review':
                db.execute('INSERT INTO todos(user_id,doc_id,message,created) VALUES(?,?,?,?)', (admin['id'], doc, '【测试演示】请审核：' + get(doc)['title'], server.now()))
                server.audit(db, admin, '测试审核提醒', doc, '测试数据生成时添加管理员审核待办')
            else:
                db.execute("UPDATE todos SET done=1 WHERE doc_id=? AND user_id IN (SELECT id FROM users WHERE role IN ('admin','reviewer'))", (doc,))
        def opinion(doc, body, status=None, resolution=''):
            api(reviewer, f'documents/{doc}/opinion', {'body': body})
            oid = db.execute('SELECT max(id) FROM opinions WHERE doc_id=?', (doc,)).fetchone()[0]
            if status:
                api(writer, f'documents/{doc}/resolve', {'opinion_id': oid, 'status': status, 'resolution': resolution})
        def review(doc):
            move(doc, 'review', '起草人已完成编制，提交管理员审核；请审查技术指标和测试依据。')
        def approve(doc):
            move(doc, 'approved', '管理员审核通过：技术要求完整，评审意见已闭环。')
        def save(doc, note):
            current = get(doc)
            api(writer, f'documents/{doc}/save', {'expected_version': current['current_version'], 'label': 'V0.4', 'note': note, 'body': current['versions'][0]['body'].replace('不少于24小时', '不少于48小时') + '\n补充：响应时间按100次测试的P95值统计。'})

        draft = create(1, '机器人接口规范—起草与协作', '继续编辑、导入Word、上传附件、分享协作、提交文档')
        invitation = api(writer, f'documents/{draft}/shares', {})
        api(reader, 'shares/join', {'token': invitation['token']})
        pending = create(2, '国产系统兼容性规范—待审核', '管理员/评审人处理审核提醒，新增评审意见；起草人处理两条未闭环意见')
        review(pending)
        opinion(pending, '请明确支持的操作系统版本和CPU架构范围。')
        opinion(pending, '请补充接口响应时间的采样次数与统计口径。')
        returned = create(3, '机器人可靠性规范—已退回', '起草人修改正文、处理意见、重新提交审核')
        review(returned)
        opinion(returned, '连续运行24小时的覆盖不足，建议扩展到48小时并补充异常恢复步骤。')
        move(returned, 'draft', '退回修改：请补充连续运行指标和异常恢复验证材料后重新提交。')
        ready = create(4, '接口性能规范—意见已闭环待批准', '查看已处理/不采纳意见及说明，管理员可完成审核')
        review(ready)
        opinion(ready, '请提供接口响应时间的统计口径。', 'resolved', '接受意见：补充100次采样及P95响应时间口径，详见附件测试记录。')
        opinion(ready, '建议将所有硬件平台纳入本版验证。', 'rejected', '不采纳：本版仅覆盖任务指定的目标CPU架构，其他平台列入后续版本计划。')
        passed = create(5, '安全配置规范—已审核通过', '管理员立即发布或设置定时发布')
        review(passed)
        opinion(passed, '请明确默认关闭调试端口。', 'resolved', '已在安全配置说明中明确默认关闭调试端口，并提供核查记录。')
        approve(passed)
        scheduled = create(6, '日志与审计规范—定时发布', '查看计划发布、取消发布、重新设定发布时间')
        review(scheduled)
        approve(scheduled)
        publish_at = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(timespec='seconds')
        api(admin, f'documents/{scheduled}/schedule', {'publish_at': publish_at})
        published = create(7, '机器人兼容性测试方法—已发布与评价', '查看完整退回/重提/批准/发布记录、附件、评分与评价；可继续新增评价')
        review(published)
        opinion(published, '建议将连续运行时间提高到48小时。')
        move(published, 'draft', '退回：需要补充48小时稳定性指标及响应时间统计方法。')
        save(published, '根据退回意见补充48小时运行指标、P95统计口径')
        oid = get(published)['opinions'][0]['id']
        api(writer, f'documents/{published}/resolve', {'opinion_id': oid, 'status': 'resolved', 'resolution': '接受意见，已更新正文为48小时并补充统计方法。'})
        review(published)
        approve(published)
        move(published, 'published', '正式发布测试版本，作为后续测试和评价依据。')
        for user, score, body in [(writer, 5, '编制和审核流程完整，附件可下载，指标表述清楚。'), (reviewer, 4, '评审意见已闭环，建议下一版增加更多异常恢复样例。'), (reader, 4, '测试步骤可执行，建议补充ARM平台截图。'), (admin, 5, '管理员验收：发布冻结、历史版本、审核记录和评价功能可核查。')]:
            api(user, f'documents/{published}/evaluate', {'version_id': get(published)['release_version'], 'score': score, 'body': body})
        branching = create(8, '设备适配规范—分支合入与归档', '同一文件的独立草稿、已通过待合入分支、归档分支和版本树')
        for i, title in enumerate(['新增ARM平台适配（待合入）', '补充异常恢复（协作草稿）', '旧实验方案（已归档）']):
            branch = api(writer, f'documents/{branching}/fork', {'source_id': get(branching)['current_version']})['id']
            api(writer, f'documents/{branch}/metadata', {'title': '【测试演示】' + title, 'tags': '测试演示,独立分支', 'extra': {'测试场景': title}})
            grant_reader(branch)
            current = get(branch)
            api(writer, f'documents/{branch}/save', {'expected_version': current['current_version'], 'label': 'W0.2', 'note': title, 'body': current['versions'][0]['body'] + '\n分支补充内容：' + title})
            if i == 0:
                review(branch)
                opinion(branch, '请确认ARM平台采用相同验收口径。', 'resolved', '接受，ARM平台和现有平台统一采用相同验收阈值。')
                approve(branch)
            elif i == 2:
                api(admin, f'documents/{branch}/archive-branch', {'note': '旧实验方案保留供追溯，不再继续编制；可测试恢复归档。'})
            rows.append({'id': branch, 'title': title, 'purpose': '分支管理'})
        report = {'created': server.now(), 'directory': '测试演示（流程验收）', 'accounts': [name for name, _ in accounts], 'scheduled_publish_at': publish_at, 'documents': rows}
        Path('data/demo-scenarios.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
