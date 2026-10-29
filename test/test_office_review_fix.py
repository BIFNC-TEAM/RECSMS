import ast
import json
from pathlib import Path
import shutil
import subprocess
import unittest


class ReviewFixTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'),'Node.js required for viewer patch check')
    def test_actual_patch_normalizes_modes_including_zero(self):
        path=Path(__file__).resolve().parents[1]/'deploy/fix_office_review_display.py'
        tree=ast.parse(path.read_text(encoding='utf-8'))
        replacement=next(ast.literal_eval(n.value) for n in ast.walk(tree)
                         if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='replacement' for t in n.targets))
        script='''const vm=require('node:vm'),assert=require('node:assert/strict');
const patch=PATCH;
for(const [input,expected] of [['markup',0],['simple',3],['final',1],['original',2],['0',0],['1',1],[0,0],[1,1],['bad',undefined],['99',undefined]]){
 const calls=[];
 vm.runInNewContext(patch,{val:input,Asc:{c_oAscDisplayModeInReview:{Edit:0,Simple:3,Final:1,Original:2}},me:{turnDisplayMode:v=>calls.push(v),view:{turnDisplayMode:v=>calls.push(v)}}});
 assert.deepEqual(calls,expected===undefined?[]:[expected,expected]);
}
'''.replace('PATCH',json.dumps(replacement))
        subprocess.run(['node','-e',script],check=True,capture_output=True)
