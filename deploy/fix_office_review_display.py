"""Run inside ONLYOFFICE Docs 10.0 to fix viewer review-mode enum conversion.

Upstream report: https://github.com/ONLYOFFICE/DocumentServer/issues/3853
Keeps backups outside the served web directory; does not modify documents.
"""
import gzip
from pathlib import Path
import shutil
import subprocess
import time


def main():
    path=Path('/var/www/onlyoffice/documentserver/web-apps/apps/common/main/lib/controller/ReviewChanges.js')
    source=path.read_text()
    marker='// RSMS: normalize viewer review mode before passing it to the SDK.'
    guard='config.canViewReview = (config.isEdit || me.api.asc_HaveRevisionsChanges(true));'
    fixed_guard="config.canViewReview = (config.isEdit || me.api.asc_HaveRevisionsChanges(true) || (me.appConfig.customization && me.appConfig.customization.review && me.appConfig.customization.review.reviewDisplay === 'final'));"
    if marker in source and fixed_guard in source:
        print('Review display fix already installed')
        return
    start=source.index('} else if (config.canViewReview) {')
    old='''                        val && me.turnDisplayMode(val);
                        val && me.view.turnDisplayMode(val);'''
    index=source.find(old,start)
    replacement='''                        // RSMS: normalize viewer review mode before passing it to the SDK.
                        var modes = {markup: Asc.c_oAscDisplayModeInReview.Edit,
                            simple: Asc.c_oAscDisplayModeInReview.Simple,
                            final: Asc.c_oAscDisplayModeInReview.Final,
                            original: Asc.c_oAscDisplayModeInReview.Original};
                        if (typeof val === 'string') {
                            val = Object.prototype.hasOwnProperty.call(modes, val) ? modes[val] :
                                (/^\\d+$/.test(val) ? Number(val) : undefined);
                        }
                        if (typeof val === 'number' && Object.keys(modes).some(function(k) { return modes[k] === val; })) {
                            me.turnDisplayMode(val);
                            me.view.turnDisplayMode(val);
                        }'''
    if marker in source:
        patched=source
    else:
        if index < 0:raise RuntimeError('Unsupported ONLYOFFICE review controller; no files changed')
        patched=source[:index]+replacement+source[index+len(old):]
    if guard not in patched:raise RuntimeError('Unexpected viewer guard; no files changed')
    patched=patched.replace(guard,fixed_guard)
    backup=Path('/var/lib/onlyoffice/rsms-review-display-backups')/str(time.time_ns())
    backup.mkdir(parents=True,mode=0o700)
    compressed=Path(str(path)+'.gz')
    shutil.copy2(path,backup/path.name)
    if compressed.exists():shutil.copy2(compressed,backup/compressed.name)
    candidate=backup/'candidate.js';candidate.write_text(patched)
    if shutil.which('node'):
        subprocess.run(['node','--check',str(candidate)],check=True)
    path.write_text(patched)
    if compressed.exists():compressed.write_bytes(gzip.compress(patched.encode()))
    print('Installed review display fix; original JS and gzip backed up')


if __name__=='__main__':main()
