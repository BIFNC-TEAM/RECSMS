import base64
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import onlyoffice
import server


class WorkCopiesTest(unittest.TestCase):
    def test_independent_submissions_and_stale_base_protection(self):
        folder = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(server, 'DB', Path(folder) / 'platform.db'))
        self.enterContext(patch.dict(os.environ, {'STANDARD_ADMIN_PASSWORD': 'work-copy-test-password'}))
        server.init()
        with server.connection() as db:
            for name in ('alice', 'bob'):
                db.execute('INSERT INTO users(username,password,role) VALUES(?,?,?)', (name, server.password_hash('temporary-test-password'), 'editor'))
            users = {r['username']: dict(r) for r in db.execute('SELECT id,username,role FROM users')}
            admin, alice, bob = (users[k] for k in ('admin', 'alice', 'bob'))
            def call(user, path, data=None):
                return server.route(db, user, 'POST' if data is not None else 'GET', '/api/' + path, {}, data or {})
            root_id = call(alice, 'documents', {'number': 'COPY-001', 'title': '并行编制测试', 'directory_id': 1, 'label': 'V1.0', 'note': '基础版本', 'body': '第一条原文\n第二条原文'})['id']
            root = call(alice, f'documents/{root_id}')
            invitation = call(alice, f'documents/{root_id}/shares', {})
            call(bob, 'shares/join', {'token': invitation['token']})
            original = onlyoffice.make_docx(root['versions'][0]['body'])
            onlyoffice.store_file(db, root['current_version'], original)
            a = call(alice, f'documents/{root_id}/fork', {'source_id': root['current_version']})['id']
            b = call(bob, f'documents/{root_id}/fork', {'source_id': root['current_version']})['id']
            adoc, bdoc = call(alice, f'documents/{a}'), call(bob, f'documents/{b}')
            self.assertEqual(adoc['work_copy']['base_version_id'], bdoc['work_copy']['base_version_id'])
            self.assertEqual(db.execute('SELECT data FROM office_files WHERE version_id=?', (adoc['current_version'],)).fetchone()['data'], original)
            with self.assertRaises(server.Error) as denied:
                call(bob, f'documents/{a}')
            self.assertEqual(denied.exception.status, 403)
            db.execute("INSERT INTO office_sessions(key,doc_id,version_id,user_id,state,source,created,updated) VALUES('bob-active',?,?,?,'editing',?,?,?)", (b, bdoc['current_version'], bob['id'], original, server.now(), server.now()))
            def upload(user, doc_id, body):
                return call(user, f'documents/{doc_id}/office-upload', {'name': 'proposal.docx', 'data': base64.b64encode(onlyoffice.make_docx(body)).decode(), 'label': 'W1.0', 'note': '本人的独立修改'})
            upload(alice, a, '第一条 Alice 修改\n第二条原文')
            # Bob's active Office room cannot block Alice's own version or submission.
            call(alice, f'documents/{a}/transition', {'target': 'review', 'note': '提交个人版本'})
            call(admin, f'documents/{a}/transition', {'target': 'approved', 'note': '批准个人修改'})
            merged = call(admin, f'documents/{a}/merge', {'label': 'V1.1', 'note': '合入 Alice 修改'})
            self.assertEqual(merged['id'], root_id)
            graph = call(admin, f'documents/{root_id}')['version_graph']
            a_current = call(alice, f'documents/{a}')['current_version']
            self.assertIn({'child_id': merged['version_id'], 'parent_id': a_current, 'kind': 'merge'}, graph['edges'])
            self.assertIn({'child_id': adoc['current_version'], 'parent_id': root['current_version'], 'kind': 'fork'}, graph['edges'])
            call(admin, f'documents/{b}/office-abandon', {'key': 'bob-active', 'note': '结束测试会话'})
            upload(bob, b, '第一条原文\n第二条 Bob 修改')
            call(bob, f'documents/{b}/transition', {'target': 'review', 'note': '提交另一份个人版本'})
            call(admin, f'documents/{b}/transition', {'target': 'approved', 'note': '批准个人修改'})
            with self.assertRaises(server.Error) as conflict:
                call(admin, f'documents/{b}/merge', {'label': 'V1.2', 'note': '不能覆盖已经合入的修改'})
            self.assertEqual(conflict.exception.status, 409)
            preview = call(admin, f'documents/{b}/merge-preview')
            self.assertTrue(preview['main_changed'])
            self.assertIn('Alice', preview['main']['body'])
            self.assertIn('Bob', preview['proposal']['body'])
            self.assertNotIn('Bob', preview['main']['body'])
            self.assertEqual(call(alice, f'documents/{root_id}')['current_version'], merged['version_id'])
            bob_graph = call(bob, f'documents/{root_id}')['version_graph']
            self.assertNotIn(a, {n['doc_id'] for n in bob_graph['nodes']})
            self.assertEqual(db.execute('SELECT data FROM office_files WHERE version_id=?', (root['current_version'],)).fetchone()['data'], original)
            grouped = server.route(db, admin, 'GET', '/api/documents', {'grouped': '1'}, {})
            self.assertEqual(len(grouped), 1)
            self.assertEqual(grouped[0]['id'], root_id)
            self.assertEqual(grouped[0]['branch_count'], 3)
            self.assertEqual({d['id'] for d in call(admin, f'documents/{root_id}')['file_branches']}, {root_id, a, b})
            alice_group = server.route(db, alice, 'GET', '/api/documents', {'grouped': '1'}, {})
            self.assertEqual(alice_group[0]['branch_count'], 2)
            matching = server.route(db, admin, 'GET', '/api/documents', {'grouped': '1', 'q': 'Bob'}, {})
            self.assertEqual(matching[0]['id'], root_id)
            self.assertEqual(matching[0]['matched_branch_ids'], [b])
            self.assertEqual(server.route(db, alice, 'GET', '/api/documents', {'grouped': '1', 'q': 'Bob'}, {}), [])
            call(alice, 'documents', {'number': 'UNRELATED', 'title': '并行编制测试', 'directory_id': 1, 'label': 'V1.0', 'note': '同名独立文件', 'body': '另一份文件'})
            self.assertEqual(len(server.route(db, admin, 'GET', '/api/documents', {'grouped': '1'}, {})), 2)


if __name__ == '__main__':
    unittest.main()
