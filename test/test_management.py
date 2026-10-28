from test.test_workspace import WorkspaceTest
import server
import workspace


class ManagementTest(WorkspaceTest):
    # Reuse the isolated database fixture without repeating its test cases.
    test_taxonomy_is_idempotent_and_new_document_defaults_to_blank = None
    test_favorites_are_personal_idempotent_and_never_bypass_access = None
    test_http_consent_gate_and_public_drafts = None

    def test_administrator_requires_existing_verified_user(self):
        self.call(self.admin,'users',dict(username='candidate',phone='13900000022',password='candidate-password',role='reader'))
        user=dict(self.db.execute("SELECT * FROM users WHERE username='candidate'").fetchone())
        self.assertIsNone(self.call(self.admin,'admin-users/search',{'phone':'13900000023'})['user'])
        found=self.call(self.admin,'admin-users/search',{'phone':user['phone']})['user']
        self.assertEqual(set(found),{'id','username','phone','phone_verified','role'})
        data={'phone':user['phone'],'user_id':user['id']}
        for actor,payload,status in ((user,data,403),(self.admin,{**data,'user_id':999},409),(self.admin,data,409)):
            with self.assertRaises(server.Error) as error:self.call(actor,'admin-users',payload)
            self.assertEqual(error.exception.status,status)
        self.db.execute('UPDATE users SET phone_verified=1 WHERE id=?',(user['id'],))
        self.call(self.admin,'admin-users',data)
        self.call(self.admin,'admin-users',data)
        after=dict(self.db.execute('SELECT * FROM users WHERE id=?',(user['id'],)).fetchone())
        self.assertEqual(after['role'],'admin')
        self.assertEqual({k:v for k,v in after.items() if k not in ('role','phone_verified')},{k:v for k,v in user.items() if k not in ('role','phone_verified')})
        self.assertEqual(self.db.execute('SELECT count(*) FROM users').fetchone()[0],2)

    def test_flat_migration_preserves_content_and_edit_restrictions(self):
        self.call(self.admin,'users',dict(username='editor',phone='13900000024',password='candidate-password',role='editor'))
        editor=dict(self.db.execute("SELECT * FROM users WHERE username='editor'").fetchone())
        parent=self.db.execute("SELECT id FROM directories WHERE name='机器人标准'").fetchone()[0]
        child=self.db.execute("INSERT INTO directories(name,parent_id) VALUES('控制与通信',?)",(parent,)).lastrowid
        did=self.call(self.admin,'documents',dict(number='MIGRATE',title='Retained',directory_id=child,label='V0.1',note='initial',body='原正文'))['id']
        self.db.execute("INSERT INTO grants(scope,target_id,user_id,access) VALUES('directory',?,?,'read')",(parent,self.admin['id']))
        before=[dict(r) for r in self.db.execute('SELECT * FROM versions')]
        self.db.execute("DELETE FROM workspace_migrations WHERE key='flat-directories-v2'")
        workspace.init(server,self.db)
        self.assertEqual([dict(r) for r in self.db.execute('SELECT * FROM versions')],before)
        self.assertEqual(self.db.execute('SELECT directory_id FROM documents WHERE id=?',(did,)).fetchone()[0],parent)
        self.assertEqual(self.db.execute('SELECT count(*) FROM directories WHERE parent_id IS NOT NULL').fetchone()[0],0)
        self.assertEqual(len(self.call(editor,'directories')),7)
        self.assertFalse(self.call(editor,'documents/'+str(did))['can_edit'])
        with self.assertRaises(server.Error):server.document(self.db,editor,did,True)
        with self.assertRaises(server.Error) as error:self.call(self.admin,'directories',{'name':'extra'})
        self.assertEqual(error.exception.status,410)
