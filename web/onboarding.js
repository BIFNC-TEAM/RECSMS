/* Account-scoped, optional first-visit walkthrough. No document changes or demo data. */
window.StandardsOnboarding=(()=>{
 let current=null;
 const steps=[
  {title:'浏览标准文档',text:'在“标准文档库”中查看文档及当前状态。打开文档后，可查看正文、附件、历史版本和评审意见。'},
  {title:'检索文档',text:'输入标准编号、名称或正文关键词，点击“检索”。可结合状态、标签、负责人和日期缩小检索范围。',target:'#search'},
  {title:'收藏文档',text:'点击文档列表或详情页的星标，将文档加入“我的收藏”。再次点击星标可取消收藏。',target:'[data-page="favorites"]'},
  {title:'协作编辑',text:'在编辑阶段，拥有权限的用户可通过“编辑文档”共同修订同一份稿件。系统保留修订记录并自动保存。'},
  {title:'评审与发布',text:'文档按“编辑—公开—发布”流转。公开阶段完成评审意见处理和稿件批准后，方可发布。发布版本只读保留。',flow:true},
  {title:'查看待办',text:'在“我的待办”查看评审、退回和发布通知。评审问题在文档的“评审意见”中提交、回复和关闭。',target:'[data-page="todos"]'},
  {title:'使用说明',text:'在“使用说明”中查阅状态流转、协作编制和版本管理流程。需要时，可重新查看新手教程。',target:'[data-page="guide"]'}
 ];
 function close(){if(!current)return;window.removeEventListener('resize',place);window.removeEventListener('scroll',place,true);current.dialog.close();current.dialog.remove();current.focus.remove();current=null;}
 function place(){
  if(!current)return;
  const {dialog,focus,index}=current,step=steps[index],target=step.target&&document.querySelector(step.target),r=target?.getBoundingClientRect(),visible=r&&r.width&&r.height&&r.bottom>0&&r.top<innerHeight;
  const width=Math.min(400,innerWidth-28);dialog.style.width=width+'px';
  let x=(innerWidth-width)/2,y=(innerHeight-dialog.offsetHeight)/2;
  focus.classList.toggle('is-centered',!visible);
  if(visible){
   const left=Math.max(5,r.left-5),top=Math.max(5,r.top-5),right=Math.min(innerWidth-5,r.right+5),bottom=Math.min(innerHeight-5,r.bottom+5);
   Object.assign(focus.style,{left:left+'px',top:top+'px',width:(right-left)+'px',height:(bottom-top)+'px'});
   if(right+18+width<=innerWidth){x=right+18;y=top;}
   else if(bottom+18+dialog.offsetHeight<innerHeight){x=Math.max(14,Math.min(left,innerWidth-width-14));y=bottom+18;}
   else if(top-dialog.offsetHeight-18>14){x=Math.max(14,Math.min(left,innerWidth-width-14));y=top-dialog.offsetHeight-18;}
  }else Object.assign(focus.style,{left:'0',top:'0',width:'100%',height:'100%'});
  Object.assign(dialog.style,{left:Math.max(14,x)+'px',top:Math.max(14,Math.min(y,innerHeight-dialog.offsetHeight-14))+'px'});
 }
 function render(){
  const {dialog,index}=current,step=steps[index];
  dialog.innerHTML=`<div class="tour-heading"><img src="/logo.png" alt="平台标志" width="44" height="44"><span>新手教程 · ${index+1} / ${steps.length}</span><button type="button" data-tour-skip>跳过教程</button></div><div class="tour-progress" role="progressbar" aria-label="教程进度" aria-valuemin="0" aria-valuemax="${steps.length}" aria-valuenow="${index+1}"><span style="width:${(index+1)/steps.length*100}%"></span></div><h2 id="tour-title">${step.title}</h2><p id="tour-description">${step.text}</p>${step.flow?'<div class="tour-flow"><span>编辑</span><b>→</b><span>公开</span><b>→</b><span>发布</span></div>':''}<p class="tour-error" role="status"></p><footer><button type="button" data-tour-back ${index?'':'hidden'}>上一步</button><button type="button" class="primary" data-tour-next>${index===steps.length-1?'开始使用':'下一步'}</button></footer>`;
  dialog.querySelector('[data-tour-skip]').onclick=()=>finish('skipped');
  dialog.querySelector('[data-tour-back]').onclick=()=>{current.index--;render();};
  dialog.querySelector('[data-tour-next]').onclick=()=>{if(current.index===steps.length-1)finish('done');else{current.index++;render();}};
  place();dialog.querySelector('[data-tour-next]').focus({preventScroll:true});
 }
 async function finish(state){
  if(!current||current.saving)return;current.saving=true;
  const entry=current;for(const button of entry.dialog.querySelectorAll('button'))button.disabled=true;
  try{const result=await entry.api('onboarding',{state});entry.onFinish(result.state);close();}
  catch{if(current===entry){entry.dialog.querySelector('.tour-error').textContent='教程进度保存失败，请重试。';entry.saving=false;for(const button of entry.dialog.querySelectorAll('button'))button.disabled=false;place();}}
 }
 function open({api,onFinish}){
  if(current)return;
  const dialog=document.createElement('dialog'),focus=document.createElement('div');
  dialog.className='onboarding-dialog';dialog.setAttribute('aria-labelledby','tour-title');dialog.setAttribute('aria-describedby','tour-description');focus.className='tour-focus';focus.setAttribute('aria-hidden','true');document.body.append(focus,dialog);
  current={dialog,focus,index:0,api,onFinish,saving:false};dialog.addEventListener('cancel',e=>{e.preventDefault();finish('skipped');});render();dialog.showModal();place();dialog.querySelector('[data-tour-next]').focus();window.addEventListener('resize',place);window.addEventListener('scroll',place,true);
 }
 return {open};
})();
