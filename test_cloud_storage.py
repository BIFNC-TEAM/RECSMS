import io
import os
import unittest
import tempfile
import base64
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from unittest.mock import patch
import object_store
import database


class FakeCos:
    def __init__(self):
        self.objects = {}

    def put_object(self, Bucket, Key, Body, **kwargs):
        assert kwargs['ACL'] == 'private'
        self.objects[Key] = Body

    def get_object(self, Bucket, Key):
        body = io.BytesIO(self.objects[Key])
        body.get_raw_stream = lambda: body
        return {'Body': body}


class CloudStorageTest(unittest.TestCase):
    def test_cos_private_roundtrip_and_integrity(self):
        cos = FakeCos()
        with patch.dict(os.environ, {'COS_BUCKET': 'test', 'COS_PREFIX': 'test/objects'}), patch.object(object_store, 'client', return_value=cos):
            original = b'original docx bytes'
            a = object_store.put(original)
            b = object_store.put(b'new version')
            self.assertNotEqual(a, b)
            self.assertEqual(object_store.get(a), original)
            self.assertEqual(object_store.size(a), len(original))
            self.assertEqual(object_store.get(b'local bytes'), b'local bytes')
            cos.objects[object_store.key(object_store.reference(a)[2].decode())] = b'corrupted'
            with self.assertRaises(RuntimeError):
                object_store.get(a)

    def test_postgres_dialect_preserves_literals_and_precise_timestamps(self):
        self.assertEqual(database.postgres_sql("INSERT OR IGNORE INTO t VALUES(?,'what? 10%')"), "INSERT INTO t VALUES(%s,'what? 10%%') ON CONFLICT DO NOTHING")
        self.assertIn('DOUBLE PRECISION', database.postgres_sql('created REAL'))
        self.assertEqual(database.postgres_sql('CREATE TABLE t(id INTEGER PRIMARY KEY,data BLOB)'), 'CREATE TABLE t(id SERIAL PRIMARY KEY,data BYTEA)')

    def test_office_live_save_attachment_copy_and_frozen_release_use_cos_references(self):
        import server
        import onlyoffice
        cos = FakeCos()
        config = dict(public_url='http://office', internal_url='http://office', platform_url='http://platform', secret='test-secret-longer-than-thirty-two-characters')
        with tempfile.TemporaryDirectory() as folder, patch.object(server, 'DB', Path(folder)/'test.db'), patch.dict(os.environ, {'COS_BUCKET':'test', 'STANDARD_ADMIN_PASSWORD':'cloud-test-password'}), patch.object(object_store, 'client', return_value=cos):
            server.init()
            with server.connection() as db:
                user = dict(db.execute('SELECT * FROM users').fetchone())
                def call(path, data=None):
                    return server.route(db,user,'POST' if data is not None else 'GET','/api/'+path,{},data or {})
                doc_id = call('documents',dict(number='COS-CHECK',title='COS',directory_id=1,label='V0.1',body='original',note='test'))['id']
                path = 'documents/'+str(doc_id)
                attachment = b'private attachment'
                call(path+'/attachment',dict(name='test.txt',data=base64.b64encode(attachment).decode(),label='V0.2',note='attachment'))
                self.assertEqual(call(path)['attachments'][-1]['size'],len(attachment))
                raw = db.execute('SELECT data FROM attachments ORDER BY id DESC LIMIT 1').fetchone()[0]
                self.assertTrue(raw.startswith(object_store.MAGIC))
                self.assertEqual(object_store.get(raw),attachment)
                with patch.object(onlyoffice,'ready',return_value=config), patch.object(onlyoffice,'download',return_value=b'true'):
                    opened = call(path+'/office-open',{})
                db.commit()
                content = onlyoffice.make_docx('COS autosave content')
                payload = dict(key=opened['key'], status=6, url='http://office/result.docx')
                def callback():
                    token = onlyoffice.sign({'payload':payload,'operation':'callback'},config['secret'])
                    return onlyoffice.callback(server,db,payload,'Bearer '+token)
                with patch.object(onlyoffice,'ready',return_value=config), patch.object(onlyoffice,'download',return_value=content):
                    callback()
                    db.commit()
                    ref = db.execute('SELECT saved FROM office_sessions WHERE key=?',(opened['key'],)).fetchone()[0]
                    self.assertTrue(ref.startswith(object_store.MAGIC))
                    query={k:v[0] for k,v in parse_qs(urlparse(opened['config']['document']['url']).query).items()}
                    self.assertEqual(onlyoffice.file(server,db,query),content)
                    with patch.object(cos,'put_object',side_effect=OSError('fixture upload failure')), patch.object(onlyoffice,'download',return_value=onlyoffice.make_docx('failed save')):
                        with self.assertRaises(OSError):
                            callback()
                    db.rollback()
                    self.assertEqual(db.execute('SELECT saved FROM office_sessions WHERE key=?',(opened['key'],)).fetchone()[0],ref)
                    payload['status']=2
                    callback()
                for target in ('public','approved','published'):
                    call(path+'/transition',dict(target=target,note='test'))
                released=call(path)['release_version']
                self.assertEqual(object_store.get(db.execute('SELECT data FROM office_files WHERE version_id=?',(released,)).fetchone()[0]),content)
                call(path+'/next-cycle',dict(label='V2-working',note='next'))
                self.assertEqual(object_store.get(db.execute('SELECT data FROM office_files WHERE version_id=?',(call(path)['current_version'],)).fetchone()[0]),content)
