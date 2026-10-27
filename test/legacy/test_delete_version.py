import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import server


class DeleteVersionTest(unittest.TestCase):
    def test_delete_is_creator_scoped_soft_and_reference_safe(self):
        temp = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(server, 'DB', Path(temp) / 'db.sqlite'))
        self.enterContext(patch.dict(os.environ, {'STANDARD_ADMIN_PASSWORD': 'delete-test-password'}))
        server.init()
        with server.connection() as db:
            db.execute('INSERT INTO users(username,password,role) VALUES(?,?,?)', ('writer', server.password_hash('temporary-password'), 'editor'))
            users = {u['username']: dict(u) for u in db.execute('SELECT id,username,role FROM users')}
            admin, writer = users['admin'], users['writer']
            def call(user, path, data=None):
                return server.route(db, user, 'POST' if data is not None else 'GET', '/api/' + path, {}, data or {})
            doc_id = call(writer, 'documents', {'number': 'DELETE-1', 'title': '删除版本测试', 'directory_id': 1, 'label': 'V0.1', 'note': '初始', 'body': '原始正文'})['id']
            initial = call(writer, f'documents/{doc_id}')['current_version']
            call(writer, f'documents/{doc_id}/save', {'label': 'V0.2', 'note': '修改', 'body': '新正文', 'expected_version': initial})
            target = call(writer, f'documents/{doc_id}')['current_version']
            with self.assertRaises(server.Error) as denied:
                call(admin, f'documents/{doc_id}/delete-version', {'version_id': target})
            self.assertEqual(denied.exception.status, 403)
            with self.assertRaises(server.Error) as stale:
                call(writer, f'documents/{doc_id}/delete-version', {'version_id': initial})
            self.assertEqual(stale.exception.status, 409)
            self.assertFalse(call(writer, f'documents/{doc_id}/delete-version', {'version_id': target})['deleted_document'])
            doc = call(writer, f'documents/{doc_id}')
            self.assertEqual(doc['current_version'], initial)
            self.assertEqual([v['id'] for v in doc['versions']], [initial])
            self.assertTrue(db.execute('SELECT 1 FROM versions WHERE id=?', (target,)).fetchone())
            derived = call(writer, f'documents/{doc_id}/fork', {'source_id': initial})['id']
            with self.assertRaises(server.Error) as referenced:
                call(writer, f'documents/{doc_id}/delete-version', {'version_id': initial})
            self.assertEqual(referenced.exception.status, 409)
            copy_head = call(writer, f'documents/{derived}')['current_version']
            link = call(writer, f'documents/{derived}/shares', {})
            self.assertTrue(call(writer, f'documents/{derived}/delete-version', {'version_id': copy_head})['deleted_document'])
            with self.assertRaises(server.Error):
                call(writer, 'shares/join', {'token': link['token']})
            with self.assertRaises(server.Error):
                call(writer, f'documents/{derived}')
            self.assertTrue(db.execute('SELECT 1 FROM documents WHERE id=?', (derived,)).fetchone())


if __name__ == '__main__':
    unittest.main()
