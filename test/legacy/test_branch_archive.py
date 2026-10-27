import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import server


class BranchArchiveTest(unittest.TestCase):
    def test_archive_permissions_readonly_preservation_and_restore(self):
        folder = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(server, 'DB', Path(folder) / 'test.db'))
        self.enterContext(patch.dict(os.environ, {'STANDARD_ADMIN_PASSWORD': 'archive-test-password'}))
        server.init()
        with server.connection() as db:
            db.execute("INSERT INTO users(username,password,role) VALUES('writer',?,'editor')", (server.password_hash('test-password'),))
            users = {r['username']: dict(r) for r in db.execute('SELECT * FROM users')}
            admin, writer = users['admin'], users['writer']
            def call(user, path, data=None):
                return server.route(db, user, 'GET' if data is None else 'POST', '/api/' + path, {}, data or {})
            root = call(writer, 'documents', {'number': 'ARCHIVE', 'title': '归档测试', 'directory_id': 1, 'label': 'V1', 'note': '原文', 'body': '保留正文'})['id']
            original = call(writer, f'documents/{root}')
            with self.assertRaises(server.Error) as main_branch:
                call(admin, f'documents/{root}/archive-branch', {})
            self.assertEqual(main_branch.exception.status, 409)
            self.assertFalse(call(admin, f'documents/{root}')['is_archived'])
            branch = call(writer, f'documents/{root}/fork', {'source_id': original['current_version']})['id']
            before = call(writer, f'documents/{branch}')
            for action in ('archive-branch', 'restore-branch'):
                with self.assertRaises(server.Error) as denied:
                    call(writer, f'documents/{branch}/{action}', {})
                self.assertEqual(denied.exception.status, 403)
            call(admin, f'documents/{branch}/archive-branch', {'note': '暂时归档'})
            archived = call(writer, f'documents/{branch}')
            self.assertTrue(archived['is_archived'])
            self.assertFalse(archived['can_edit'])
            self.assertEqual(archived['versions'], before['versions'])
            self.assertEqual(archived['archive']['note'], '暂时归档')
            for user in (admin, writer):
                for action in ('versions', 'transition', 'delete-version', 'shares'):
                    with self.assertRaises(server.Error) as denied:
                        call(user, f'documents/{branch}/{action}', {'target': 'review'})
                    self.assertEqual(denied.exception.status, 409)
            self.assertEqual(len(call(admin, f'documents/{root}')['file_branches']), 2)
            self.assertEqual(db.execute('SELECT count(*) FROM deleted_documents').fetchone()[0], 0)
            call(admin, f'documents/{branch}/restore-branch', {})
            restored = call(writer, f'documents/{branch}')
            self.assertFalse(restored['is_archived'])
            self.assertTrue(restored['can_edit'])
            self.assertEqual(restored['current_version'], before['current_version'])
            db.execute('INSERT INTO collab(doc_id,version_id,state,body) VALUES(?,?,?,?)', (branch, before['current_version'], '', '未保存的修改'))
            with self.assertRaises(server.Error) as pending:
                call(admin, f'documents/{branch}/archive-branch', {})
            self.assertEqual(pending.exception.status, 409)
            self.assertFalse(call(writer, f'documents/{branch}')['is_archived'])
            db.execute('DELETE FROM collab WHERE doc_id=?', (branch,))
            db.execute("UPDATE documents SET state='scheduled' WHERE id=?", (branch,))
            schedule = db.execute('INSERT INTO schedules(doc_id,version_id,user_id,publish_at,created) VALUES(?,?,?,?,?)', (branch, before['current_version'], admin['id'], '2099-01-01T00:00:00', server.now())).lastrowid
            call(admin, f'documents/{branch}/archive-branch', {})
            self.assertEqual(call(admin, f'documents/{branch}')['state'], 'approved')
            self.assertEqual(db.execute('SELECT status FROM schedules WHERE id=?', (schedule,)).fetchone()[0], 'cancelled')
            call(admin, f'documents/{branch}/restore-branch', {})
            self.assertEqual(call(admin, f'documents/{branch}')['state'], 'approved')
            self.assertEqual(db.execute('SELECT status FROM schedules WHERE id=?', (schedule,)).fetchone()[0], 'cancelled')


if __name__ == '__main__':
    unittest.main()
