import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import server
import user_profile


class ProfileTest(unittest.TestCase):
    def test_optional_profile_and_validation(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(server,'DB',Path(folder)/'test.db'), patch.dict(os.environ,{'STANDARD_ADMIN_PASSWORD':'profile-test-password'}):
            server.init()
            with server.connection() as db:
                user=dict(db.execute('SELECT * FROM users').fetchone())
                user_profile.route(server,db,user,'POST',{'nickname':'小明','identity':'student','organization':'测试学校'})
                profile=user_profile.get(db,user['id'])
                self.assertEqual(profile['nickname'],'小明')
                self.assertEqual(profile['identity'],'student')
                with self.assertRaises(server.Error):user_profile.route(server,db,user,'POST',{'nickname':'小明','identity':'admin'})
                with self.assertRaises(server.Error):user_profile.route(server,db,user,'POST',{'nickname':'小明','avatar':'not-base64'})
                self.assertEqual(db.execute('SELECT username FROM users WHERE id=?',(user['id'],)).fetchone()['username'],user['username'])
