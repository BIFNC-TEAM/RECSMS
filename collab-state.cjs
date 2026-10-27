// Yjs is the authority for decoding and merging updates; Python never trusts client text.
const Y = require('yjs');
let input='';
process.stdin.setEncoding('utf8');
process.stdin.on('data', chunk=>input+=chunk);
process.stdin.on('end',()=>{
  try {
    const value=JSON.parse(input), doc=new Y.Doc(), text=doc.getText('body');
    if(value.state)Y.applyUpdate(doc,Buffer.from(value.state,'base64'));
    else if(value.body)text.insert(0,value.body);
    if(value.update)Y.applyUpdate(doc,Buffer.from(value.update,'base64'));
    const body=text.toString();
    if(body.length>2_000_000)throw Error('正文过大');
    const state=Y.encodeStateAsUpdate(doc);
    if(state.length>8*1024*1024)throw Error('协作文档状态过大，请保存版本后重新进入');
    process.stdout.write(JSON.stringify({body,state:Buffer.from(state).toString('base64')}));
  }catch(e){process.stderr.write(String(e.message));process.exitCode=1}
});
