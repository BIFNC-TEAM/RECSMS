const projectRoot=require('node:path').resolve(__dirname,'..');
process.chdir(projectRoot);
// Isolated browser acceptance: no production data or real SMS requests.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const {spawn}=require('node:child_process');
const fs=require('node:fs'),os=require('node:os'),path=require('node:path'),assert=require('node:assert/strict');
(async()=>{
 const tmp=fs.mkdtempSync(path.join(os.tmpdir(),'rsms-workspace-')),base='http://127.0.0.1:18778';
 const child=spawn(process.env.PYTHON_EXECUTABLE||'python',['-u','server.py','--port','18778'],{cwd:projectRoot,env:{...process.env,STANDARD_DB:path.join(tmp,'test.db'),STANDARD_ADMIN_PASSWORD:'workspace-ui-password'},stdio:'ignore',windowsHide:true});let browser;
 try{
  for(let i=0;i<100;i++){try{if((await fetch(base)).ok)break}catch{}await new Promise(r=>setTimeout(r,100))}
  browser=await chromium.launch({headless:true,channel:'msedge'});const context=await browser.newContext({viewport:{width:1440,height:1050}});const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));await page.goto(base);
  const consent=page.locator('#login-consent'),submit=page.locator('#login-submit');
  await consent.waitFor();assert(!await consent.isChecked());assert(await submit.isDisabled());assert(await page.locator('[data-sms-send]').isDisabled());assert.equal(await page.locator('.legacy-login').count(),0);
  assert.equal(await page.getByText('获取短信前需完成腾讯云图形安全验证，验证码5分钟有效。',{exact:true}).count(),0);
  const policy=await (await context.request.get(base+'/api/policies')).json();
  const blocked=await context.request.post(base+'/api/login',{data:{username:'admin',password:'workspace-ui-password'},headers:{'X-Requested-With':'StandardsPlatform'}});assert.equal(blocked.status(),400);
  const opened=context.waitForEvent('page');await page.getByRole('link',{name:'隐私政策',exact:true}).click();const privacy=await opened;await privacy.waitForLoadState();await privacy.getByRole('heading',{name:/隐私政策/,level:1}).waitFor();assert((await privacy.locator('body').innerText()).includes('预览草案'));await privacy.close();assert(!await consent.isChecked());
  await page.screenshot({path:path.join(projectRoot,'data/workspace-login-preview.png'),fullPage:true});
  await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:path.join(projectRoot,'data/workspace-login-mobile.png'),fullPage:true});await page.setViewportSize({width:1440,height:1050});
  await page.getByRole('tab',{name:'密码登录',exact:true}).click();assert(await page.locator('#phone-login').isHidden());assert(await page.locator('#login').isVisible());assert(await submit.isDisabled());
  await page.locator('#login [name="username"]').fill('admin');await page.locator('#login [name="password"]').fill('workspace-ui-password');await consent.check();await submit.click();await page.getByRole('heading',{name:'标准文档库',exact:true}).waitFor();await page.getByRole('button',{name:'跳过教程',exact:true}).click();await page.locator('.onboarding-dialog').waitFor({state:'hidden'});
  const dirs=await (await context.request.get(base+'/api/directories')).json();assert.equal(dirs.length,7);assert.equal(await page.getByRole('button',{name:/组件与实施范围/}).count(),0);
  await page.getByRole('button',{name:'＋ 新建文档',exact:true}).click();assert.equal(await page.locator('#modal textarea[name="body"]').count(),0);await page.getByLabel('标准编号',{exact:true}).fill('WORKSPACE-UI');await page.getByLabel('文档名称',{exact:true}).fill('收藏功能验收文档');await page.getByRole('button',{name:'确认',exact:true}).click();await page.locator('dialog').waitFor({state:'hidden'});
  await page.getByRole('button',{name:'收藏文档：收藏功能验收文档',exact:true}).click();await page.getByRole('button',{name:'取消收藏：收藏功能验收文档',exact:true}).waitFor();await page.getByRole('button',{name:/我的收藏/}).click();await page.getByRole('heading',{name:'我的收藏',exact:true}).waitFor();await page.getByRole('link',{name:'收藏功能验收文档',exact:true}).waitFor();
  await page.reload();await page.getByRole('button',{name:/我的收藏/}).click();await page.getByRole('heading',{name:'我的收藏',exact:true}).waitFor();await page.screenshot({path:path.join(projectRoot,'data/workspace-favorites.png'),fullPage:true});await page.getByRole('button',{name:'取消收藏：收藏功能验收文档',exact:true}).click();await page.getByText('暂无收藏，可在文档列表或详情页点击星标收藏。',{exact:true}).waitFor();
  await page.getByRole('button',{name:/使用说明/}).click();await page.getByRole('heading',{name:'机器人与国产操作系统标准文件协作编辑及版本管理平台使用说明',exact:true}).waitFor();await page.getByRole('heading',{name:'二、状态流转设计',exact:true}).waitFor();await page.screenshot({path:path.join(projectRoot,'data/workspace-guide.png'),fullPage:true});
  const docs=await (await context.request.get(base+'/api/documents')).json();const detail=await (await context.request.get(base+'/api/documents/'+docs[0].id)).json();assert.equal(detail.versions[0].body,'');assert.deepEqual(errors,[]);
  await page.getByRole('button',{name:/管理与审计/}).click();await page.getByRole('button',{name:'新增管理员',exact:true}).waitFor();
  for(const name of ['新增用户','新增目录','配置权限','ONLYOFFICE 配置'])assert.equal(await page.getByRole('button',{name,exact:true}).count(),0);
  assert.equal(await page.getByRole('heading',{name:'目录',exact:true}).count(),0);
  await page.getByRole('button',{name:'新增管理员',exact:true}).click();assert.equal(await page.locator('#modal input').count(),1);assert.equal(await page.locator('#modal input[type=password]').count(),0);
  await page.getByLabel('用户手机号',{exact:true}).fill('13900000099');await page.getByRole('button',{name:'搜索用户',exact:true}).click();await page.getByText('未找到该手机号对应的已注册用户',{exact:true}).waitFor();assert(await page.getByRole('button',{name:'设为平台管理员',exact:true}).isDisabled());await page.locator('#cancel').click();
  await page.getByRole('button',{name:'退出',exact:true}).click();await page.locator('#login-consent').waitFor();assert(!await page.locator('#login-consent').isChecked());
  console.log('PASS: login tabs, consent gate, public legal drafts, blank creation, 7 directories, persistent private favorites, help, and mobile layout.');
 }finally{if(browser)await browser.close();const exited=new Promise(r=>child.once('exit',r));child.kill();await exited;if(path.dirname(tmp)===os.tmpdir())fs.rmSync(tmp,{recursive:true,force:true});}
})().catch(e=>{console.error(e);process.exitCode=1});
