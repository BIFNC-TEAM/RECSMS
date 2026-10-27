# 测试目录

从项目根目录执行；测试临时数据库和短信模拟配置均独立于正式环境。

```powershell
..\.venv\Scripts\python.exe -m unittest discover -s test -t . -v
node test/test_onboarding_ui.cjs
node test/test_workspace_ui.cjs
node test/test_phone_login_ui.cjs
node test/test_lifecycle_ui.cjs
node test/test_accounts_ui.cjs
powershell.exe -NoProfile -ExecutionPolicy Bypass -File test/test_setup_onlyoffice.ps1
```

浏览器脚本可通过 `PLAYWRIGHT_MODULE` 指定 Playwright 安装，通过 `PYTHON_EXECUTABLE` 指定 Python；默认使用 Edge。
新手教程与工作空间测试在临时库验证新用户首次显示、上一步/下一步、跳过、重看、账号间隔离、跨浏览器保留和移动端定位，不创建正式测试文档。

PostgreSQL 和 Redis 集成分别需要 `RSMS_TEST_DATABASE_URL`、`RSMS_TEST_REDIS_URL`，未提供时跳过，不自动使用正式实例。PostgreSQL 测试创建并清理独立随机 schema，须使用专门测试库。

`test_live_*.cjs` 连接已运行的本地服务，其中实际 Office 编辑验证可能创建明确标识的测试文档和账号；只在开发测试环境使用。真实验证码显示测试不解题、不发送短信。
`legacy/` 是已经退役的分支工作流历史夹具，不进入当前 Python 默认发现，也不代表当前界面的验收要求。
