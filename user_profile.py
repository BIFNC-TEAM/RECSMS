"""Optional personal profile; avatars use the existing private COS object store."""
import base64
import object_store


def init(db):
    db.execute("CREATE TABLE IF NOT EXISTS user_profiles(user_id INTEGER PRIMARY KEY REFERENCES users(id),nickname TEXT NOT NULL DEFAULT '',identity TEXT NOT NULL DEFAULT '',organization TEXT NOT NULL DEFAULT '',avatar BLOB,avatar_type TEXT NOT NULL DEFAULT '')")


def get(db, uid):
    row=db.execute('SELECT * FROM user_profiles WHERE user_id=?',(uid,)).fetchone()
    if not row:return {}
    result=dict(row);result.pop('user_id')
    result['avatar']=base64.b64encode(object_store.get(result['avatar'])).decode() if result['avatar'] else ''
    return result


def route(core,db,user,method,data):
    if method=='GET':return get(db,user['id'])
    nickname=core.required(data,'nickname',40)
    identity=data.get('identity','');organization=data.get('organization','')
    if identity not in ('','student','employed','seeking') or not isinstance(organization,str) or len(organization)>120:
        raise core.Error('身份或学校/公司格式无效')
    avatar=None;mime=''
    if 'avatar' in data:
        try:avatar=base64.b64decode(data['avatar'],validate=True)
        except (ValueError,TypeError):raise core.Error('头像格式无效')
        if not avatar or len(avatar)>2*1024*1024:raise core.Error('头像不能超过2MB')
        if avatar.startswith(b'\x89PNG\r\n\x1a\n'):mime='image/png'
        elif avatar.startswith(b'\xff\xd8\xff'):mime='image/jpeg'
        else:raise core.Error('仅支持PNG/JPEG头像')
    db.execute('INSERT INTO user_profiles(user_id,nickname,identity,organization) VALUES(?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET nickname=excluded.nickname,identity=excluded.identity,organization=excluded.organization',(user['id'],nickname,identity,organization))
    if avatar is not None:db.execute('UPDATE user_profiles SET avatar=?,avatar_type=? WHERE user_id=?',(object_store.put(avatar),mime,user['id']))
    core.audit(db,user,'更新个人资料')
    return {'ok':True}
