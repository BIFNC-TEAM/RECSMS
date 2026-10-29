// Read-only live regression: verify a saved Original preference cannot hide the preview.
// STANDARD_TEST_URL, STANDARD_TEST_SESSION and STANDARD_TEST_DOCUMENT_ID identify a test account/document.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict');
(async()=>{
 const base=process.env.STANDARD_TEST_URL||'http://127.0.0.1:8765';
 const id=process.env.STANDARD_TEST_DOCUMENT_ID,token=process.env.STANDARD_TEST_SESSION;
 assert(/^\d+$/.test(id||'')&&token,'Set STANDARD_TEST_DOCUMENT_ID and STANDARD_TEST_SESSION');
 const browser=await chromium.launch({headless:true,channel:'msedge'});
 try{
  const context=await browser.newContext();
  await context.addCookies([{name:'session',value:token,url:base,httpOnly:true,secure:base.startsWith('https:'),sameSite:'Strict'}]);
  await context.addInitScript(()=>{
   localStorage.setItem('de-review-mode','2');
   window.__reviewModes=[];
   const timer=setInterval(()=>{
    const api=window.Asc?.editor;
    if(api?.asc_SetDisplayModeInReview&&!api.__previewCheck){
     api.__previewCheck=true;const original=api.asc_SetDisplayModeInReview;
     api.asc_SetDisplayModeInReview=function(mode){window.__reviewModes.push(mode);return original.apply(this,arguments)};
    }
   },10);
   setTimeout(()=>clearInterval(timer),60000);
  });
  const page=await context.newPage();await page.goto(base);
  await page.locator(`[data-doc="${id}"]`).first().click();
  let frame;
  for(let i=0;i<60;i++){
   await page.waitForTimeout(1000);
   frame=page.frames().find(f=>f.url().includes('rsmsreview3')&&f.url().includes('documenteditor'));
   if(frame&&await frame.evaluate(()=>window.__reviewModes?.includes(1)))break;
  }
  assert(frame,'Updated viewer namespace must load');
  assert(await frame.evaluate(()=>window.__reviewModes.includes(1)),'Final mode must apply');
  await page.waitForTimeout(30000);
  const modes=await frame.evaluate(()=>window.__reviewModes);
  assert(!modes.includes(2),'Viewer must not revert to Original');
  console.log('PASS: Final preview remains active after 30 seconds with a stored Original preference');
 }finally{await browser.close()}
})().catch(e=>{console.error(e.message);process.exitCode=1});
