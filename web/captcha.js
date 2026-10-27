'use strict';
const params=new URLSearchParams(location.search),appId=params.get('app_id'),nonce=params.get('nonce');
let completed=false,renderTimer;
function finish(result){if(completed)return;completed=true;clearTimeout(renderTimer);parent.postMessage({type:'phone-captcha',nonce,result},location.origin);}
document.querySelector('#cancel').onclick=()=>finish({ret:2});
if(!/^[0-9]{5,20}$/.test(appId||'')||!/^[a-f0-9]{32}$/.test(nonce||''))finish({message:'安全验证请求无效'});
else{
 const script=document.createElement('script');script.src='https://turing.captcha.qcloud.com/TJCaptcha.js';
 script.onerror=()=>finish({message:'无法加载腾讯云图形验证，请检查网络'});
 script.onload=()=>{try{const captcha=new window.TencentCaptcha(appId,result=>finish(result),{userLanguage:'zh-cn',showFn:()=>{clearTimeout(renderTimer);document.querySelector('#loading').hidden=true;parent.postMessage({type:'phone-captcha-ready',nonce},location.origin);}});captcha.show();}catch{finish({message:'图形验证启动失败，请重试'});}};
 renderTimer=setTimeout(()=>finish({message:'图形验证加载超时，请检查网络'}),30000);document.head.append(script);
}
