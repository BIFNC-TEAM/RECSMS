const projectRoot=require('node:path').resolve(__dirname,'..');
process.chdir(projectRoot);
// Reads existing versions against the actual configured ONLYOFFICE server.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const {spawnSync}=require('node:child_process');
const assert=require('node:assert/strict'),path=require('node:path');
(async()=>{
 const setup=spawnSync(process.env.PYTHON_EXECUTABLE||'python',['-c',`import server,secrets,time,json
with server.connection() as db:
 user=db.execute("SELECT id FROM users WHERE role='admin' ORDER BY id LIMIT 1").fetchone()
 token=secrets.token_urlsafe(32)
 db.execute('INSERT INTO sessions(token,user_id,expires) VALUES(?,?,?)',(token,user['id'],time.time()+600))
 doc=db.execute('SELECT doc_id FROM versions WHERE id NOT IN (SELECT version_id FROM deleted_versions) AND doc_id NOT IN (SELECT doc_id FROM deleted_documents) GROUP BY doc_id HAVING count(*)>1 ORDER BY doc_id LIMIT 1').fetchone()
 print(json.dumps({'token':token,'id':doc['doc_id']}))`],{cwd:projectRoot,encoding:'utf8'});
 assert.equal(setup.status,0,setup.stderr);const {token,id}=JSON.parse(setup.stdout);
 const browser=await chromium.launch({headless:true,channel:process.env.BROWSER_CHANNEL||'msedge'});
 try{
  const ctx=await browser.newContext({viewport:{width:1600,height:1000}});
  await ctx.addCookies([{name:'session',value:token,url:'http://127.0.0.1:8765',httpOnly:true,sameSite:'Strict'}]);
  const page=await ctx.newPage();await page.goto('http://127.0.0.1:8765');
  await page.waitForFunction(()=>typeof StandardsOffice!=='undefined'&&typeof api==='function');
  const before=await page.evaluate(async id=>api('documents/'+id),id);
  await page.evaluate(async id=>detail(id),id);
  await page.getByRole('button',{name:'版本管理',exact:true}).click();
  await page.getByRole('button',{name:'对比两个版本',exact:true}).click();
  await page.locator('select[name="a"]').selectOption(String(before.versions.at(-1).id));
  await page.locator('select[name="b"]').selectOption(String(before.current_version));
  await page.getByRole('button',{name:'确认',exact:true}).click();
  await page.waitForFunction(()=>/已启动|错误|无法/.test(document.querySelector('#office-status')?.textContent||''),null,{timeout:90000});
  assert.match(await page.locator('#office-status').innerText(),/已启动/);
  await page.waitForTimeout(10000);
  const frame=page.frames().find(f=>f.url().includes('documenteditor'));
  assert(frame,'Real editor iframe missing');
  if(await frame.getByText('您想要继续吗？',{exact:false}).count()){
   await frame.getByText('是',{exact:true}).click();
   await page.waitForTimeout(12000);
  }
  const text=await frame.locator('body').innerText();
  assert(!/下载失败|下载错误|令牌无效|文档安全令牌|Download failed/i.test(text));
  await page.screenshot({path:path.join(projectRoot,'data/live-compare.png'),fullPage:true});
  await page.locator('#office-close').click();
  const after=await page.evaluate(async id=>api('documents/'+id),id);
  assert.equal(after.current_version,before.current_version);assert.equal(after.versions.length,before.versions.length);
  console.log('PASS: actual ONLYOFFICE comparison loaded; source versions unchanged.');
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
