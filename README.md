# 机器人与国产操作系统标准文件协作编辑及版本管理平台

Robot Evaluation Center Standards Management System

## 品牌、新手教程和测试目录

平台登录页、页头、浏览器图标和教程使用用户提供的原始 Logo，资源为 `web/logo.png`。
账号首次进入时展示7步教程，可上一步、下一步或跳过；完成/跳过状态保存在账号下，更换浏览器也不重复弹出。
“使用说明”可重新查看教程。教程不会创建示例文档、修改文档或扩大权限。
全部测试脚本集中在 `test/`，历史分支夹具位于 `test/legacy/`；运行方法见 [test/README.md](test/README.md)。测试不打包进生产镜像。

## 工作空间与登录预览更新

手机号/原密码使用标签切换，登录须主动勾选两份法律文本，服务端验证版本并记录草案确认。
服务协议及隐私政策按用户要求暂为预览草案，运营主体等事实待补，不作为已完成法定告知的正式政策；详见 [LEGAL_REVIEW.md](LEGAL_REVIEW.md)。
默认建议目录共33项（根目录、7类及25个子目录），首次迁移添加，不覆盖已有文档或目录；父目录检索包含子目录。
列表与详情页支持个人收藏，工作空间新增“我的收藏”；收藏不会增加文档权限。
“使用说明”包含状态流转、统一修订、自动保存、评审闭环、历史附件、权限和检索，公开入口 `/help`。
新建文档默认空白，在 ONLYOFFICE 编辑或从 Word 导入，不再填写正文输入框。

## RSMS 云端部署

正式环境部署目录 `/home/ubuntu/rsms`，Docker Swarm 复用现有 Traefik 和 ONLYOFFICE。
配置 PostgreSQL（`RSMS_dev`）、Redis 和私有 COS（`rsms-1325708495`）后运行 `sh deploy/deploy.sh`。
操作步骤及云端备份说明见 [deploy/README.md](deploy/README.md)。本机 SQLite 仅用于本地测试，现有数据不会导入正式环境。

## 账号、手机号邀请与评论

已改为手机号短信验证码登录，获取短信前必须完成腾讯云图形安全验证。手机号唯一；旧账号保留账号密码登录入口，可在右上角账号处通过短信验证绑定，不改变原文档归属。

文档“权限管理”按手机号邀请“可编辑/可管理”，支持待加入、调整与移除；受邀手机号验证登录后自动匹配。评审意见、文档评价与评论已统一到“评审意见”事项讨论区，支持标题、回复、关闭、重新打开及待处理/已关闭筛选。旧内容迁移留存，文档管理者可暂停新建与回复。

腾讯云配置已只读参考正式透明数据空间服务，保存于忽略目录 `data/tencent-auth.json`，正式服务未修改。配置与限流说明见 [SMS_SETUP.md](SMS_SETUP.md)。微信登录和小程序接入已移除。

## 当前流程

平台只使用编辑、公开、发布三个业务状态。一份文档只有一个在编稿，所有已发布版本永久只读留存；保存产生的内部快照只用于审计，不是独立分支。

- 编辑：拥有文档编辑权限的用户共同进入同一个 ONLYOFFICE 会话，以修订权限编辑，编辑器持续同步，平台每5秒请求回写并持久保存，窗口显示最近成功保存时间；无需退出或分享后才能保存和编辑。
- 公开：仅管理员继续修改正文；授权评审人可提出意见，管理员处理意见。可返回编辑。通过“查看原文”查看文档内修订。
- 发布：只能由公开进入。管理员先批准当前稿件，全部评审意见闭环、最终文件保存完成后，才可发布。正文或附件修改、新增评审意见会使批准失效。
- 下一轮：管理员从最新发布版本开启新的唯一在编稿，完整复制正文和附件，旧发布版本保持不变。

文档管理者通过标签栏右侧日历图标设置编辑截止时间、公开截止时间，均为北京时间，留空表示手动切换。公开截止时间须晚于编辑截止时间。公开到期时，若尚未批准、评审事项未关闭或有人正在编辑，保留公开状态并通知管理者；条件满足后自动发布。新一轮需要重新设置日期。

旧的独立分支、合并、分支归档及多状态操作接口已停用。迁移前资料仍在数据库中，历史版本下“迁移前留存资料（只读）”可查看原内容。原审核中、审核通过、计划发布状态迁移为公开，旧定时发布计划取消，需按新流程重新设置。迁移不自动批准发布。

## 启动

```powershell
cd E:\program\touming\standard-platform
npm ci
npm run build
..\.venv\Scripts\python.exe -m pip install -r requirements-auth.txt
..\.venv\Scripts\python.exe -u server.py
```

访问 http://127.0.0.1:8765 。首次初始化凭据输出到终端，可预设至少12位的 STANDARD_ADMIN_PASSWORD。STANDARD_DB可指定测试数据库，默认 data/platform.db。

## ONLYOFFICE 与修订比较

编辑入口使用 ONLYOFFICE 文档编辑器；协作者共享同一房间，使用 review 权限与强制修订配置，普通编辑者不能切换为无痕编辑或接受他人的修订。管理员可以审阅处理修订。

版本管理中的“对比两个版本”使用 ONLYOFFICE 原生 Compare，以只读修订标记显示差异，不覆盖原文件。若文档已有修订，编辑器会询问是否按接受已有修订后的内容比较，确认仅影响本次比较视图。纯文本版本转换为基础 DOCX，不包含原版式。

### 当前远端部署（2026-10-23）

ONLYOFFICE Docs 10.0 已部署到 `81.70.105.92`，容器名 `standards-office-docs`，部署目录 `/home/ubuntu/standards-office`。容器限制 2 CPU、3GB 内存，设置自动重启及容器日志轮转。回调桥接由 `standards-office-callback.service` 管理。未改变该主机已有 Swarm 业务服务。

已用真实编辑器验证：两个独立账号打开同一文档并编辑，关闭后收到最终保存回调，两人的新增内容均在返回的 DOCX 中，并成功形成平台版本。验证脚本为 `test/test_live_onlyoffice.cjs`，它创建明确标识的测试文档和测试编辑账号；截图在 `data/onlyoffice-live-collaboration.png`。这项验证覆盖实际编辑及保存，不等同于生产负载或全部 Word 格式兼容性测试。

当前本地平台与远端编辑器通过 SSH 连接，不新增公网端口：

| 路径 | 当前地址 |
|---|---|
| 用户访问平台 | `http://127.0.0.1:8765` |
| 用户访问远端编辑器（本地 SSH 转发） | `http://127.0.0.1:8080` → 远端 `127.0.0.1:18080` |
| 编辑器访问平台 | 容器 `host.docker.internal:18766` → 回调桥接 → SSH 反向转发 `127.0.0.1:18765` → 本地 `127.0.0.1:8765` |

密钥存于服务器 `office.env`（600 权限）及本地 `data/remote-onlyoffice.env`，平台数据库已配置相同 JWT。不保存 SSH 登录密码。回调桥接仅转发 `/api/office/file` 与 `/api/office/callback` 两个签名端点，不暴露平台登录、管理及其他 API。

电脑或 SSH 连接重启后，先启动本地平台，再运行以下脚本，并在 SSH 提示中输入服务器密码；保持该窗口运行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "E:\program\touming\standard-platform\connect_remote_office.ps1"
```

无需在本机继续安装 WSL 或 Docker。若提示 8080 已占用，先检查是否已有同一 SSH 转发运行，避免重复开启。远端重启后容器和回调桥接会自动恢复，但仍需恢复本机 SSH 连接。SSH 连接中断期间不要把未收到最终回调的文件视为已归档。

部署源码为 `deploy_onlyoffice_remote.sh` 和 `office_callback_proxy.py`。前一台 `43.129.235.38` 因磁盘余量不足未部署服务，未清理其日志、缓存或数据。

### 连接已有 Document Server

用管理员进入“管理与审计 → ONLYOFFICE 配置”，填写三个地址和共享密钥：

| 配置 | 用途 | 同机示例 / Docker 网络示例 |
|---|---|---|
| 浏览器访问 Document Server 地址 | 加载 api.js 和编辑器 iframe，必须能从用户浏览器访问 | `http://127.0.0.1:8080` |
| 平台访问 Document Server 地址 | 健康检查、下载保存回调文件 | `http://documentserver`（同一 Compose 网络） |
| Document Server 访问本平台地址 | 读取签名文件、发送保存回调；不能指向容器自己的 localhost | `http://platform:8765`（同一 Compose 网络） |
| JWT 共享密钥 | 与 Document Server 的 `JWT_SECRET` 完全一致 | 随机生成至少 32 字符，界面不会回显 |

设置也可通过 `ONLYOFFICE_PUBLIC_URL`、`ONLYOFFICE_INTERNAL_URL`、`ONLYOFFICE_PLATFORM_URL`、`ONLYOFFICE_JWT_SECRET` 环境变量提供；环境变量存在时界面不允许覆盖。保存界面配置后页面自动刷新，应用新的 CSP。健康检查通过仅表示网络可达，不替代 JWT、文件互通及真实编辑验证。

如果 Document Server 在另一台机器，本平台需要使用可从该机器访问的监听地址启动，例如 `python server.py --host 0.0.0.0`，并将第三个地址设为本平台实际地址。正式部署请使用 HTTPS；HTTPS 平台不能嵌入 HTTP 编辑器。

### Linux / Docker 部署

本机现有平台可使用 `setup_onlyoffice.ps1` 与 `compose.onlyoffice.local.yaml`，只新增 Document Server 容器，继续使用当前数据库。连接地址和随机 JWT 由 `configure_local_office.py` 写入平台及 `data/onlyoffice.env`，配置前自动备份。首次安装 WSL 需在管理员 PowerShell 执行：

```powershell
& "E:\program\touming\standard-platform\setup_onlyoffice.ps1"
```

脚本检查并安装 WSL/Docker，遇到 Windows 功能需要重启时会停止，**不会自动重启**；人工重启后运行同一脚本继续。它会启动 Document Server、将现有平台监听到 `0.0.0.0:8765` 以便 Docker 网关回调，并检查服务健康。Office 端口仅映射到本机 `127.0.0.1:8080`。`-CheckOnly` 仅检查环境，不安装组件。JWT 不在终端显示；重复配置保留同一密钥。

已提供 `Dockerfile`、`compose.yaml`、`.env.example`。在有 Docker Compose 的 Linux 主机运行：

```sh
cp .env.example .env
# 编辑 .env：设置管理员密码、随机 JWT 密钥及用户浏览器可访问的 Document Server URL。
# 可使用 python3 -c "import secrets; print(secrets.token_urlsafe(48))" 生成密钥。
docker compose up -d --build
```

默认平台端口 8765，Document Server 端口 8080；编辑器服务初始化可能需数分钟。Compose 为平台和 Document Server 提供独立持久卷；这是新的部署数据，不会自动带入当前 Windows 数据库。需要迁移时先导出备份，在新平台停服状态恢复，再启动服务；JWT 与服务地址按新环境重新配置。不要用 `docker compose down -v` 清除数据卷。

Document Server 官方 Docker 文档列出至少 4GB RAM 和 40GB 可用空间等环境要求，请按目标规模预留。[官方安装指南](https://helpcenter.onlyoffice.com/docs/installation/docs-community-install-docker.aspx)。配置中允许私网地址是为两个容器互相取文件，不应直接用于不受信任的多租户环境。


## 数据与备份

用户、文档、正文、附件、评审意见、通知、评价及审计保存在 SQLite。迁移前备份保存在 data/pre-three-phase-*.db。旧分支资料未删除。

通过服务的备份命令或管理接口生成一致性数据库快照；恢复时先停止服务，保留当前数据库副本后恢复备份。JWT密钥及 data 下配置不要公开。

## 当前验证

```powershell
..\.venv\Scripts\python.exe -m unittest discover -s test -t . -v
node test/test_lifecycle_ui.cjs
```

浏览器测试可设置 PLAYWRIGHT_MODULE、PYTHON_EXECUTABLE，默认使用 Edge。覆盖三阶段权限、无需分享共同编辑、公开退回、意见闭环、批准失效、发布冻结、下一轮、定时切换阻塞、迁移留存、签名 Office 回调以及桌面/移动端界面。旧分支工作流测试保存在 test/legacy/，仅作历史资料。

## 部署边界

当前 Python 内置HTTP服务和SQLite适合本机/单实例试用。生产部署需配置HTTPS、可靠的文档引擎连接、身份认证、备份、监控及负载验证。打印使用浏览器打印，可手动另存PDF。国产操作系统及CPU适配仍需在目标环境验收。

## 编辑期间自动保存

ONLYOFFICE 使用快速共同编辑模式，平台每5秒请求 forcesave，将状态6回调的完整DOCX写入数据库。编辑窗口保持打开时，下载及只读预览即可使用最近已保存稿件；最终关闭仅完成会话归档，不是首次保存。未发布期间的自动保存不生成公开历史版本。

编辑窗口显示上次成功保存时间，网络断开或保存请求失败时显示提示并自动重试。重新连接、文档引擎丢失缓存后，文件请求会从平台最近保存的稿件恢复。尚未从浏览器发送出去的修改，不能保证在断电断网时保留。

`test/test_live_autosave.cjs` 创建一份标记清楚的测试文档，使用真实编辑器验证未退出时回写、工作稿下载、离线提示、重连及最终归档；`test/test_lifecycle.py` 覆盖签名回调、落库、源文件恢复及失败重试。
