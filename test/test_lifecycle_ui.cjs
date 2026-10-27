const projectRoot=require('node:path').resolve(__dirname,'..');
process.chdir(projectRoot);
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const {spawn}=require('node:child_process');
const fs=require('node:fs'),os=require('node:os'),path=require('node:path'),assert=require('node:assert/strict');
(async()=>{
 const tmp=fs.mkdtempSync(path.join(os.tmpdir(),'cycle-ui-')),base='http://127.0.0.1:18768';
 const child=spawn(process.env.PYTHON_EXECUTABLE||'python',['-u','server.py','--port','18768'],{cwd:projectRoot,env:{...process.env,STANDARD_DB:path.join(tmp,'db.sqlite'),STANDARD_ADMIN_PASSWORD:'cycle-ui-password'},stdio:'ignore',windowsHide:true});
 let browser;
 try{
  for(let i=0;i<100;i++){try{if((await fetch(base)).ok)break}catch{}await new Promise(r=>setTimeout(r,100))}
  browser=await chromium.launch({headless:true,channel:process.env.BROWSER_CHANNEL||'msedge'});
  const admin=await browser.newContext({viewport:{width:1600,height:1100}}),alice=await browser.newContext(),bob=await browser.newContext();
  async function api(ctx,url,data){if(url==='login')data={...data,...await(await ctx.request.get(base+'/api/policies')).json(),accepted:true};const r=data?await ctx.request.post(base+'/api/'+url,{data,headers:{'X-Requested-With':'StandardsPlatform'}}):await ctx.request.get(base+'/api/'+url);assert(r.ok(),await r.text());if(url==='login')await ctx.request.post(base+'/api/onboarding',{data:{state:'skipped'},headers:{'X-Requested-With':'StandardsPlatform'}});return r.json()}
  await api(admin,'login',{username:'admin',password:'cycle-ui-password'});
  for(const username of ['alice','bob'])await api(admin,'users',{username,phone:username==='alice'?'13900000001':'13900000002',role:'editor',password:'cycle-user-password'});
  await api(alice,'login',{username:'alice',password:'cycle-user-password'});await api(bob,'login',{username:'bob',password:'cycle-user-password'});
  const id=(await api(alice,'documents',{number:'UI-CYCLE',title:'Cycle UI document',directory_id:1,label:'V0.1',note:'Initial',body:'Original text'})).id;
  const page=await admin.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base);await page.getByRole('link',{name:'Cycle UI document',exact:true}).click();await page.locator('.document-tools').waitFor();
  assert.equal(await page.getByRole('button',{name:'分支管理',exact:true}).count(),0);
  assert.deepEqual(await page.locator('.tab-list>button').allTextContents(),['文档正文','附件材料','历史版本','评审意见','流转记录']);
  assert.deepEqual(await page.locator('.document-tool-icons>button').allTextContents(),['','','','']);
  assert.equal(await page.locator('.document-controls [data-action="members"]').count(),0);
  assert.equal(await page.locator('.document-tool-icons svg').count(),4);
  const bp=await bob.newPage();await bp.goto(base);await bp.getByRole('link',{name:'Cycle UI document',exact:true}).click();assert(await bp.getByRole('button',{name:'编辑文档',exact:true}).isEnabled());
  async function phase(target){await page.locator(`[data-transition="${target}"]`).click();await page.locator('input[name="note"]').fill('Lifecycle UI verification');await page.getByRole('button',{name:'确认',exact:true}).click();await page.locator('#modal').waitFor({state:'hidden'});}
  await phase('public');assert(await page.getByRole('button',{name:'编辑文档',exact:true}).isEnabled());
  await bp.reload();await bp.getByRole('link',{name:'Cycle UI document',exact:true}).click();assert(await bp.getByRole('button',{name:'编辑文档',exact:true}).isDisabled());
  await page.locator('[data-action="cycle-settings"]').click();assert.equal(await page.locator('input[name="edit_until"]').getAttribute('type'),'datetime-local');assert.equal(await page.locator('input[name="public_until"]').getAttribute('type'),'datetime-local');await page.locator('input[name="edit_until"]').fill('2099-01-01T09:00');await page.locator('input[name="public_until"]').fill('2099-01-02T09:00');await page.getByRole('button',{name:'确认',exact:true}).click();await page.locator('#modal').waitFor({state:'hidden'});
  await phase('approved');await phase('published');assert(await page.getByRole('button',{name:'编辑文档',exact:true}).isDisabled());
  const published=await api(admin,'documents/'+id),release=published.release_version;
  await page.locator('[data-action="next-cycle"]').click();await page.locator('input[name="label"]').fill('V1.1-working');await page.locator('input[name="note"]').fill('Next round');await page.getByRole('button',{name:'确认',exact:true}).click();await page.locator('#modal').waitFor({state:'hidden'});
  const next=await api(admin,'documents/'+id);assert.equal(next.state,'draft');assert.equal(next.release_version,release);assert.notEqual(next.current_version,release);
  await page.locator('[data-tab="versions"]').click();await page.locator('[data-tab="versions"].active').waitFor();assert.equal(await page.locator('[data-version-row]').count(),1);
  assert.equal(await page.locator('[data-derive]').count(),0);assert.equal(await page.locator('[data-action="merge"]').count(),0);
  await page.locator(`[data-view-version="${release}"]`).first().click();await page.locator('[data-viewed-version="'+release+'"]').waitFor();assert(await page.getByRole('button',{name:'编辑文档',exact:true}).isDisabled());
  await page.screenshot({path:path.join(projectRoot,'data/three-phase-ui.png'),fullPage:true});
  await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));assert.deepEqual(errors,[]);
  console.log('PASS: library navigation, three states, no branches, unshared editor access, public admin-only editing, timers, approval, immutable publication, next round, published history listing, mobile.');
 }finally{if(browser)await browser.close();const done=new Promise(r=>child.once('exit',r));child.kill();await done;if(path.dirname(tmp)===os.tmpdir())fs.rmSync(tmp,{recursive:true,force:true})}
})().catch(e=>{console.error(e);process.exitCode=1});
