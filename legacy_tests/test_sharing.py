import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import onlyoffice
import server


class SharingTest(unittest.TestCase):
    def test_private_default_share_scope_revocation_and_creator_submission(self):
        folder = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(server, 'DB', Path(folder) / 'platform.db'))
        self.enterContext(patch.dict(os.environ, {'STANDARD_ADMIN_PASSWORD': 'sharing-test-password'}))
        server.init()
        with server.connection() as db:
            for name, role in [('creator', 'editor'), ('collaborator', 'reader')]:
                db.execute('INSERT INTO users(username,password,role) VALUES(?,?,?)', (name, server.password_hash('temporary-password'), role))
            users = {r['username']: dict(r) for r in db.execute('SELECT id,username,role FROM users')}
            owner, member, admin = (users[k] for k in ('creator', 'collaborator', 'admin'))
            def call(user, path, data=None):
                return server.route(db, user, 'POST' if data is not None else 'GET', '/api/' + path, {}, data or {})
            def create(number):
                return call(owner, 'documents', {'number': number, 'title': number, 'directory_id': 1, 'label': 'W0.1', 'note': '新建独立副本', 'body': '初始内容'})['id']
            first, second = create('PRIVATE-1'), create('PRIVATE-2')
            self.assertIsNotNone(call(owner, f'documents/{first}')['work_copy'])
            self.assertEqual(call(member, 'documents'), [])
            with self.assertRaises(server.Error):
                call(member, f'documents/{first}')
            link = call(owner, f'documents/{first}/shares', {})
            self.assertEqual(call(owner, f'documents/{first}/shares', {}), link)
            self.assertNotEqual(link['token'], db.execute('SELECT token_hash FROM share_links WHERE id=?', (link['id'],)).fetchone()['token_hash'])
            self.assertEqual(db.execute('SELECT token_hash FROM share_links WHERE id=?', (link['id'],)).fetchone()['token_hash'], hashlib.sha256(link['token'].encode()).hexdigest())
            call(member, 'shares/join', {'token': link['token']})
            self.assertTrue(call(member, f'documents/{first}')['can_edit'])
            self.assertEqual([r['id'] for r in call(member, 'documents')], [first])
            with self.assertRaises(server.Error):
                call(member, f'documents/{second}')
            for action in ('shares', 'share-revoke'):
                for actor in (member, admin):
                    with self.assertRaises(server.Error) as denied:
                        call(actor, f'documents/{first}/{action}', {})
                    self.assertEqual(denied.exception.status, 403)
            # Sharing authorizes edits even for an otherwise read-only account, scoped to this copy.
            call(member, f'documents/{first}/save', {'label': 'W0.2', 'note': '协作者修改', 'body': '协作成果', 'expected_version': call(member, f'documents/{first}')['current_version']})
            for actor in (member, admin):
                with self.assertRaises(server.Error) as denied:
                    call(actor, f'documents/{first}/transition', {'target': 'review', 'note': '非创建者禁止提交'})
                self.assertEqual(denied.exception.status, 403)
            call(owner, f'documents/{first}/transition', {'target': 'review', 'note': '由创建者提交共同成果'})
            self.assertEqual(call(member, f'documents/{first}')['state'], 'review')
            call(owner, f'documents/{first}/share-revoke', {'id': link['id']})
            with self.assertRaises(server.Error):
                call(member, 'shares/join', {'token': link['token']})
            with self.assertRaises(server.Error):
                call(member, f'documents/{first}')
            self.assertNotIn(link['token'], str([dict(r) for r in db.execute('SELECT detail FROM audit')]))


if __name__ == '__main__':
    unittest.main()
