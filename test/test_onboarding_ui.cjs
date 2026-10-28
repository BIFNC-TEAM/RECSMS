const projectRoot=require('node:path').resolve(__dirname,'..');
process.chdir(projectRoot);
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const {spawn}=require('node:child_process');
const fs=require('node:fs'),os=require('node:os'),path=require('node:path'),assert=require('node:assert/strict'),crypto=require('node:crypto');
(async()=>{
 const tmp=fs.mkdtempSync(path.join(os.tmpdir(),'rsms-onboarding-')),base='http://127.0.0.1:18779';
 const child=spawn(process.env.PYTHON_EXECUTABLE||'python',['-u','server.py','--port','18779'],{cwd:projectRoot,env:{...process.env,STANDARD_DB:path.join(tmp,'test.db'),STANDARD_ADMIN_PASSWORD:'onboarding-ui-password'},stdio:'ignore',windowsHide:true});let browser;
 try{
  for(let i=0;i<100;i++){try{if((await fetch(base)).ok)break}catch{}await new Promise(r=>setTimeout(r,100))}
  browser=await chromium.launch({headless:true,channel:'msedge'});const context=await browser.newContext({viewport:{width:1440,height:1000}});
  async function api(ctx,url,data){if(url==='login')data={...data,...await(await ctx.request.get(base+'/api/policies')).json(),accepted:true};const r=data?await ctx.request.post(base+'/api/'+url,{data,headers:{'X-Requested-With':'StandardsPlatform'}}):await ctx.request.get(base+'/api/'+url);assert(r.ok(),await r.text());return r.json();}
  await api(context,'login',{username:'admin',password:'onboarding-ui-password'});
  const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));await page.goto(base);const tour=page.locator('.onboarding-dialog');await tour.waitFor();
  await page.waitForFunction(()=>[...document.querySelectorAll('img[src="/logo.png"]')].every(i=>i.complete&&i.naturalWidth>0));
  assert.equal(await page.title(),'机器人与国产操作系统标准文件协作编辑及版本管理平台');assert((await page.locator('.brand').innerText()).includes('Robot Evaluation Center Standards Management System'));const logo=await context.request.get(base+'/logo.png');assert(logo.headers()['content-type'].startsWith('image/png'));assert.equal(crypto.createHash('sha256').update(await logo.body()).digest('hex'),crypto.createHash('sha256').update(fs.readFileSync(path.join(projectRoot,'web/logo.png'))).digest('hex'));
  assert.equal(await tour.locator('#tour-title').innerText(),'浏览标准文档');await page.screenshot({path:path.join(projectRoot,'data/onboarding-welcome.png'),fullPage:true});
  await tour.getByRole('button',{name:'下一步',exact:true}).click();assert.equal(await tour.locator('#tour-title').innerText(),'检索文档');await page.screenshot({path:path.join(projectRoot,'data/onboarding-search.png'),fullPage:true});
  await tour.getByRole('button',{name:'上一步',exact:true}).click();assert.equal(await tour.locator('#tour-title').innerText(),'浏览标准文档');
  for(let i=0;i<6;i++)await tour.getByRole('button',{name:'下一步',exact:true}).click();
  await tour.getByRole('button',{name:'开始使用',exact:true}).click();await tour.waitFor({state:'hidden'});assert.equal((await api(context,'me')).onboarding_state,'done');assert.equal((await api(context,'documents')).length,0);
  await page.reload();await page.getByRole('heading',{name:'标准文档库',exact:true}).waitFor();assert.equal(await page.locator('.onboarding-dialog').count(),0);
  await page.getByRole('button',{name:/使用说明/}).click();await page.getByRole('button',{name:'重新查看新手教程',exact:true}).click();await tour.waitFor();await page.keyboard.press('Escape');await tour.waitFor({state:'hidden'});assert.equal((await api(context,'me')).onboarding_state,'done');
  await api(context,'users',{username:'new-reader',phone:'13900000055',password:'reader-onboarding-password',role:'reader'});
  const reader=await browser.newContext({viewport:{width:390,height:844}});await api(reader,'login',{username:'new-reader',password:'reader-onboarding-password'});const mobile=await reader.newPage();await mobile.goto(base);await mobile.locator('.onboarding-dialog').waitFor();
  for(let i=0;i<7;i++){const r=await mobile.locator('.onboarding-dialog').boundingBox();assert(r.x>=0&&r.y>=0&&r.x+r.width<=391&&r.y+r.height<=845);if(i<6)await mobile.getByRole('button',{name:'下一步',exact:true}).click();}
  await mobile.screenshot({path:path.join(projectRoot,'data/onboarding-mobile.png'),fullPage:true});await mobile.getByRole('button',{name:'跳过教程',exact:true}).click();await mobile.locator('.onboarding-dialog').waitFor({state:'hidden'});assert.equal((await api(reader,'me')).onboarding_state,'skipped');assert.equal((await api(context,'me')).onboarding_state,'done');
  const another=await browser.newContext();await api(another,'login',{username:'new-reader',password:'reader-onboarding-password'});const again=await another.newPage();await again.goto(base);await again.getByRole('heading',{name:'标准文档库',exact:true}).waitFor();assert.equal(await again.locator('.onboarding-dialog').count(),0);assert.deepEqual(errors,[]);
  console.log('PASS: original logo, seven tutorial steps, back/next, completion, skip, replay, per-account persistence across browsers, no document creation, mobile positioning.');
 }finally{if(browser)await browser.close();const exited=new Promise(r=>child.once('exit',r));child.kill();await exited;if(path.dirname(tmp)===os.tmpdir())fs.rmSync(tmp,{recursive:true,force:true});}
})().catch(e=>{console.error(e);process.exitCode=1});
