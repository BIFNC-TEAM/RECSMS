// Explicit live integration check. Creates a clearly labelled test document and test editor.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const {spawnSync}=require('node:child_process');
const fs=require('node:fs'),path=require('node:path');
const assert=require('node:assert/strict');
const python=process.env.PYTHON_EXECUTABLE||'python';
const base='http://127.0.0.1:8765';
(async()=>{
  const setup=spawnSync(python,['-c',`import server,secrets,json,time
with server.connection() as db:
 admin=db.execute("SELECT id,username,role FROM users WHERE role='admin' ORDER BY id LIMIT 1").fetchone()
 username='office_validation_editor'
 editor=db.execute('SELECT id,username,role FROM users WHERE username=?',(username,)).fetchone()
 if not editor:
  db.execute('INSERT INTO users(username,password,role) VALUES(?,?,?)',(username,server.password_hash(secrets.token_urlsafe(32)),'editor'))
  editor=db.execute('SELECT id,username,role FROM users WHERE username=?',(username,)).fetchone()
 collaborator=db.execute("SELECT id,username,role FROM users WHERE username='office_validation_collaborator'").fetchone()
 if not collaborator:
  db.execute('INSERT INTO users(username,password,role) VALUES(?,?,?)',('office_validation_collaborator',server.password_hash(secrets.token_urlsafe(32)),'reader'))
  collaborator=db.execute("SELECT id,username,role FROM users WHERE username='office_validation_collaborator'").fetchone()
 tokens={}
 for name,user in [('admin',admin),('editor',editor),('collaborator',collaborator)]:
  token=secrets.token_urlsafe(32);tokens[name]=token
  db.execute('INSERT INTO sessions VALUES(?,?,?)',(token,user['id'],time.time()+1800))
 server.audit(db,admin,'ONLYOFFICE 真实联调会话')
 print(json.dumps(tokens))`],{cwd:__dirname,encoding:'utf8'});
  if(setup.status)throw Error(setup.stderr);
  const tokens=JSON.parse(setup.stdout),statePath=path.join(__dirname,'data/live-office-test.json');
  const browser=await chromium.launch({headless:true,...(process.env.BROWSER_CHANNEL?{channel:process.env.BROWSER_CHANNEL}:{})});
  try{
    const ctx=await browser.newContext({viewport:{width:1440,height:1000}});
    await ctx.addCookies([{name:'session',value:tokens.editor,url:base,httpOnly:true,sameSite:'Strict'}]);
    const request=async(route,data)=>{const r=data?await ctx.request.post(base+'/api/'+route,{data,headers:{'X-Requested-With':'StandardsPlatform'}}):await ctx.request.get(base+'/api/'+route);if(!r.ok())throw Error(await r.text());return r.json()};
    let id=fs.existsSync(statePath)?JSON.parse(fs.readFileSync(statePath)).id:null;
    if(!id){id=(await request('documents',{number:'OO-LIVE-'+Date.now(),title:'ONLYOFFICE 接入验证（测试文件）',directory_id:1,label:'V0.1',note:'验证真实 Word 编辑器与保存回调',body:'ONLYOFFICE 实际编辑联调。\n本文件仅用于验证协作与版本管理。'})).id;fs.writeFileSync(statePath,JSON.stringify({id}))}
    const doc=await request('documents/'+id);
    if(doc.session?.state==='ready')throw Error('等待旧会话自动归档后重试');
    const page=await ctx.newPage();
    await page.goto(base);await page.getByRole('heading',{name:'标准文档库',exact:true}).waitFor();
    await page.getByRole('link',{name:'ONLYOFFICE 接入验证（测试文件）',exact:true}).click();
    await page.getByRole('button',{name:'编辑文档',exact:true}).click();
    await page.waitForFunction(()=>{const s=document.querySelector('#office-status');return s&&(/已连接|错误/.test(s.textContent))},{},{timeout:90000});
    console.log('Editor status:',await page.locator('#office-status').innerText());
    console.log('Frames:',page.frames().map(f=>f.url().split('?')[0]));
    const frame=page.frames().find(f=>f.url().includes('/documenteditor/'));
    if(frame)console.log('Text input IDs:',await frame.locator('textarea,[contenteditable]').evaluateAll(els=>els.map(e=>({tag:e.tagName,id:e.id,cls:e.className}))));
    await page.screenshot({path:path.join(__dirname,'data/onlyoffice-live.png'),fullPage:true});
    if(process.env.OFFICE_LIVE_INSPECT==='1')return;
    if(!frame)throw Error('Real document editor iframe missing');
    const invitation=await request(`documents/${id}/shares`,{});
    const adminCtx=await browser.newContext({viewport:{width:1440,height:1000}});
    await adminCtx.addCookies([{name:'session',value:tokens.collaborator,url:base,httpOnly:true,sameSite:'Strict'}]);
    const adminPage=await adminCtx.newPage();
    await adminPage.goto(base+'/#share='+invitation.token);
    await adminPage.getByRole('heading',{name:'ONLYOFFICE 接入验证（测试文件）',exact:true}).waitFor();
    assert(await adminPage.getByRole('button',{name:'提交文档',exact:true}).isDisabled());
    await adminPage.getByRole('button',{name:'编辑文档',exact:true}).click();
    await adminPage.getByText('Word 实时协作已连接',{exact:true}).waitFor({timeout:90000});
    const adminFrame=adminPage.frames().find(f=>f.url().includes('/documenteditor/'));
    const marker=Date.now(),writerText='Writer live validation '+marker,adminText='Shared collaborator validation '+marker;
    console.log('Typing real document through the editor keyboard input.');
    await page.bringToFront();await frame.locator('#area_id').focus();await page.waitForTimeout(500);await page.keyboard.press('Control+End');await page.waitForTimeout(200);await page.keyboard.press('Enter');await page.waitForTimeout(400);await page.keyboard.insertText(writerText);
    await page.waitForTimeout(1500);
    await adminPage.bringToFront();await adminFrame.locator('#area_id').focus();await adminPage.waitForTimeout(500);await adminPage.keyboard.press('Control+End');await adminPage.waitForTimeout(200);await adminPage.keyboard.press('Enter');await adminPage.waitForTimeout(400);await adminPage.keyboard.insertText(adminText);
    await adminPage.waitForTimeout(1500);
    let periodicallySaved=false;for(let i=0;i<35;i++){const working=await request('documents/'+id);if(working.working_body?.includes(writerText)&&working.working_body?.includes(adminText)){periodicallySaved=true;break}await page.waitForTimeout(1500)}assert(periodicallySaved,'periodic autosave did not return both users edits while the editors stayed open');console.log('Periodic autosave received while both editors remain open.');
    await page.screenshot({path:path.join(__dirname,'data/onlyoffice-live-collaboration.png'),fullPage:true});
    await page.getByRole('button',{name:'关闭编辑并等待保存',exact:true}).click();
    await adminPage.getByRole('button',{name:'关闭编辑并等待保存',exact:true}).click();
    await page.locator('.office-dialog').waitFor({state:'detached',timeout:90000});
    await adminPage.locator('.office-dialog').waitFor({state:'detached',timeout:90000});
    const info=(await request(`documents/${id}/office-status`)).session;
    assert.equal(info,null);
    console.log('Final save callback received.');
    assert.equal(await page.getByRole('button',{name:'保存 Office 新版本',exact:true}).count(),0);
    assert.equal(await page.locator('.editing-status').innerText(),'已完成');
    const verified=await request('documents/'+id),body=verified.versions[0].body;
    assert(body.includes(writerText),'writer text missing from returned DOCX');assert(body.includes(adminText),'second user text missing from returned DOCX');
    const saved=await ctx.request.get(base+`/api/download?doc_id=${id}&version_id=${verified.current_version}`);
    assert(saved.ok());assert(saved.headers()['content-disposition'].includes('.docx'));
    const bytes=await saved.body();assert(bytes[0]===0x50&&bytes[1]===0x4b);
    const revisions=spawnSync(python,['-c',`import io,sys,zipfile,xml.etree.ElementTree as ET,json
with zipfile.ZipFile(io.BytesIO(sys.stdin.buffer.read())) as z:
 root=ET.fromstring(z.read('word/document.xml'))
 ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
 authors={e.get('{'+ns['w']+'}author') for e in root.findall('.//w:ins',ns)}
 assert 'office_validation_editor' in authors, 'Shared editor modifications must be tracked suggestions'
 print('Tracked insertion authors verified')`],{cwd:__dirname,input:bytes});
    assert.equal(revisions.status,0,revisions.stderr.toString());
    console.log(revisions.stdout.toString().trim());
    console.log('PASS: real ONLYOFFICE owner + link-invited collaborator editing, creator-only submission UI, automatic final archival, preserved DOCX. Document ID:',id);
  }finally{await browser.close();spawnSync(python,['-c',`import server,sys
with server.connection() as db:
 for token in sys.argv[1:]:db.execute('DELETE FROM sessions WHERE token=?',(token,))`,tokens.admin,tokens.editor,tokens.collaborator],{cwd:__dirname})}
})().catch(e=>{console.error(e);process.exitCode=1});
