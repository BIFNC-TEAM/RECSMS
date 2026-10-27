# 腾讯云手机号登录

微信登录及小程序代码已移除。登录流程为：手机号 → 腾讯云图形安全验证 → 腾讯云短信验证码 → 登录/注册。旧账号可通过“账号密码登录（旧账号）”进入，再点击右上角账号 → 短信验证绑定手机号，保留原账号ID、文档和权限。

## 配置来源与存放

已通过SSH只读参考 `81.70.105.92` 上透明数据空间 `touming_backend` 服务的短信和验证码配置，复制所需字段到本地 `data/tencent-auth.json`，该文件位于Git忽略目录，不进入前端或数据库备份。正式服务器配置未修改。

默认读取 `data/tencent-auth.json`，可用 `STANDARD_TENCENT_AUTH_CONFIG` 指定其他路径；同名环境变量优先覆盖文件值：

| 配置 | 用途 |
|---|---|
| `TENCENTCLOUD_SECRET_ID` / `TENCENTCLOUD_SECRET_KEY` | 腾讯云图形验证服务端API凭据 |
| `TENCENT_CAPTCHA_APP_ID` | Web验证码应用ID，唯一允许返回前端的配置 |
| `TENCENT_CAPTCHA_SECRET_KEY` | 图形票据服务端核验密钥 |
| `TENCENT_SMS_SECRET_ID` / `TENCENT_SMS_SECRET_KEY` | 短信API凭据，留空时使用通用腾讯云凭据 |
| `TENCENT_SMS_SDK_APP_ID` | 短信应用ID |
| `TENCENT_SMS_SIGN_NAME` / `TENCENT_SMS_TEMPLATE_ID` | 已审核的签名和验证码模板 |
| `TENCENT_SMS_REGION` / `TENCENT_SMS_ENDPOINT` | 短信地域及API域名 |

短信模板保持正式服务一致，两个参数依次为6位验证码、有效分钟数（5）。不要将包含密钥的配置复制到web目录、聊天、日志或提交到版本库。

安装腾讯云官方SDK并启动：

```powershell
..\.venv\Scripts\python.exe -m pip install -r requirements-auth.txt
..\.venv\Scripts\python.exe -u server.py
```

浏览器访问的域名需在腾讯云验证码应用允许的Web域名范围内；若本机127.0.0.1无法加载挑战，检查腾讯云控制台的域名配置。正式HTTPS部署设置 `STANDARD_COOKIE_SECURE=1`。反向代理后需显式设置 `STANDARD_TRUSTED_PROXIES` 为可信代理IP/CIDR，让验证码核验使用真实客户端IP；默认不信任来访请求自带的 `X-Forwarded-For`。

## 保护措施

- 只在腾讯云服务端核验图形票据成功后发送短信，拒绝容灾跳过票据及重复票据。
- 手机号每60秒一次、每小时5次、每日10次；单IP每小时20次、每日60次。用途共用手机号限额，避免切换登录/绑定绕过。
- 验证码5分钟有效，最多尝试5次，成功后立即失效；重发成功会使同用途旧码失效。
- 数据库保存带服务端随机密钥的验证码HMAC，不保存或返回明文验证码。
- 手机号唯一；短信验证既有手机号时复用原账号。旧账号绑定时要求当前已登录且手机号未被其他账号占用。
- 腾讯云短信返回成功才建立有效验证码；失败不会进入模拟发送或返回开发验证码。
- 文档手机号邀请与统一评审讨论权限不变；受邀手机号通过短信验证后获得对应权限。

## 验证

```powershell
..\.venv\Scripts\python.exe -m unittest discover -s test -t . -v
node test/test_phone_login_ui.cjs
node test/test_accounts_ui.cjs
node test/test_lifecycle_ui.cjs
```

自动化发送测试使用明确的云接口夹具，不向真实号码发短信。已使用正式配置请求腾讯云图形票据核验API，确认可达并拒绝无效票据；实际短信接收和完整人工图形挑战需用用户本人手机号操作网页登录页验证。
