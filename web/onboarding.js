/* Account-scoped, optional first-visit walkthrough. No document changes or demo data. */
window.StandardsOnboarding=(()=>{
 let current=null;
 const steps=[
  {title:'先认一下这个页面',text:'标准文件都在“标准文档库”里。我们花半分钟看几个常用的地方，以后找文件、改文档会顺手些。'},
  {title:'找文件，从这里开始',text:'可以搜标准编号、名称，也可以搜正文里的词。文件多时，再选状态、负责人或日期，就不用逐页翻了。',target:'#search'},
  {title:'常用的文件，点一下星标',text:'收藏后，文件会出现在“我的收藏”里。这里存的是入口，不会多存一份文件，权限也不会变。',target:'[data-page="favorites"]'},
  {title:'大家改的是同一份稿子',text:'打开文档后，点“编辑文档”。有编辑权限的人会进入同一份在编稿，修改有修订记录，也会自动保存，不用互相传文件。'},
  {title:'发布前，先过公开评审',text:'文档先共同编辑，再进入公开评审。意见处理完、当前稿件批准后才能发布。发布稿会保留下来，下次修改再开新一轮。',flow:true},
  {title:'需要你处理的事，在这里看',text:'评审、退回和发布的提醒会出现在“我的待办”。文档里的问题请写成一条评审意见，处理完再关闭，别人也能看懂改了什么。',target:'[data-page="todos"]'},
  {title:'想再看一遍，来这里',text:'“使用说明”里有完整流程，也能重新打开这份教程。现在可以开始找文件了。',target:'[data-page="guide"]'}
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
  catch{if(current===entry){entry.dialog.querySelector('.tour-error').textContent='刚才没保存成功，请再试一次。';entry.saving=false;for(const button of entry.dialog.querySelectorAll('button'))button.disabled=false;place();}}
 }
 function open({api,onFinish}){
  if(current)return;
  const dialog=document.createElement('dialog'),focus=document.createElement('div');
  dialog.className='onboarding-dialog';dialog.setAttribute('aria-labelledby','tour-title');dialog.setAttribute('aria-describedby','tour-description');focus.className='tour-focus';focus.setAttribute('aria-hidden','true');document.body.append(focus,dialog);
  current={dialog,focus,index:0,api,onFinish,saving:false};dialog.addEventListener('cancel',e=>{e.preventDefault();finish('skipped');});render();dialog.showModal();place();dialog.querySelector('[data-tour-next]').focus();window.addEventListener('resize',place);window.addEventListener('scroll',place,true);
 }
 return {open};
})();
