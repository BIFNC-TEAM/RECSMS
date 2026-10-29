# RSMS 正式环境

服务器：81.70.105.92。目录：`/home/ubuntu/rsms`。不导入本机任何文档或账号。
复用服务器 Docker Swarm、Traefik 和已有 `standards-office-docs`；不占用新公网端口。

## 填写配置并启动

```sh
cd /home/ubuntu/rsms
nano .env
chmod 600 .env
sh deploy/deploy.sh
docker service ls
docker service logs --tail 50 rsms_platform
```

`DATABASE_URL`：腾讯云 PostgreSQL 内网连接串，数据库必须预先创建为 **RSMS_dev**，账号有该库建表、建索引、读写权限。
`REDIS_URL`：腾讯云 Redis 内网连接串；按实例配置使用 `redis://` 或 `rediss://`，默认库0，用 `REDIS_PREFIX=rsms:production:` 隔离其他应用。
连接串密码中的特殊字符需要 URL 编码（例如 `@` → `%40`、`$` → `%24`）。`.env` 使用 Docker 原生 `KEY=value` 格式，不添加引号；管理员密码和 JWT 建议使用随机字母数字，避免各工具对引号和 `$` 的解释不同。
`COS_SECRET_ID/COS_SECRET_KEY`：独立最小权限账号，允许指定前缀下 PutObject、GetObject、DeleteObject；桶为 `rsms-1325708495`，地域 `ap-beijing`。
**将桶访问权限设置为私有**，不要设置公开读。正文和下载均先检查平台权限，平台服务端从 COS 取回文件，不提供永久公开链接。
`STANDARD_ADMIN_PASSWORD`：至少12位，初始化账号 admin；正式日志不会输出密码。仅首次空库初始化有效。
`ONLYOFFICE_JWT_SECRET`：须与已有容器一致，部署准备时已从该容器配置复制，不要单独修改。
腾讯云短信、验证码变量按 `.env.production.example` 填写，验证码控制台加入 `rsms.openzoo.net` 域名。

## 网络和域名

- `rsms.openzoo.net` 和 `office.rsms.openzoo.net` 均解析到81服务器。Traefik 自动申请/续期证书，HTTP 自动转 HTTPS；平台使用 `le` TLS 验证，Office 使用 `lehttp` HTTP 验证。
- 云数据库及 Redis 安全组须允许81服务器访问；若资源不在同一 VPC，需要配置网络互通。
- `rsms-office` 是新建的可附加 Docker overlay 网络；平台通过 `http://documentserver` 调用既有 ONLYOFFICE。
- ONLYOFFICE 回调 `http://rsms_platform:8765/api/office/callback`，不再依赖本机 SSH 回调隧道。
- `/healthz` 检查 PostgreSQL 和 Redis。启动前额外验证 COS 完整写入/读取/删除（随机检测对象，不碰已有文件）。

## 数据与备份

PostgreSQL：用户、权限、文档信息/检索文本、发布历史、评审事项、流程、审计及 COS 对象引用。
COS：Word 文件、附件、编辑中自动保存稿、变更包，以 SHA256 命名的不可变对象。
Redis：登录会话、接口限流。Redis 数据丢失会退出登录，不影响文档。
编辑仍每5秒持久化；已上传 COS 后才提交数据库引用，COS 失败会保留旧保存稿并让回调重试。
历史发布文件不会被覆盖；尚未入库的对象可能成为孤立文件，当前不自动清理，以避免误删引用文件。

正式备份请开启腾讯云 PostgreSQL 自动备份/PITR，并为 COS 开启版本控制；数据库备份和 COS 都须保留，单独备份一方无法完整恢复。
网页 SQLite 备份接口在 PostgreSQL 环境返回明确说明，不会生成不完整备份。
暂采用单个平台副本；写事务用 PostgreSQL advisory lock 保持原流程一致性，自动保存任务有跨实例锁。

## 验证与回滚

```sh
curl -fsS https://rsms.openzoo.net/healthz
curl -fsS https://office.rsms.openzoo.net/healthcheck
```

以 admin 登录，创建文档、上传附件、协同编辑，在编辑器仍打开时下载确认自动保存，再验证公开/批准/发布/下一轮流程。
发布前配置未齐时只部署 Office HTTPS 入口：`sh deploy/deploy.sh --office-only`。
更新只构建和更新 rsms 服务；请不要运行 `docker system prune` 或删除已有 Office 数据卷。
平台镜像使用内容编号标签，避免同一标签重建后 Swarm 未更新容器。`.env` 及所有 `.env.*` 文件都不进入镜像；环境备份放在应用目录以外。
更新使用 start-first：新副本健康后停止旧副本。验收通过后，只移除本平台已停止的旧容器及不再使用的 `rsms-platform` 旧镜像；不要执行全局镜像或数据卷清理。
现有其它 Docker 服务保持原配置。

## ONLYOFFICE 10.0 修订预览兼容补丁

针对上游问题 [#3853](https://github.com/ONLYOFFICE/DocumentServer/issues/3853)，查看器将字符串显示模式直接传给SDK，且部分含修订稿件在初始化时检测不到修订。补丁对显示模式做枚举转换，并在显式请求 final 时应用最终显示模式，不接受或删除原文修订。

```sh
docker exec -i standards-office-docs python3 - < deploy/fix_office_review_display.py
```

补丁同步更新 JS 与 gzip 文件，原文件备份在容器 `/var/lib/onlyoffice/rsms-review-display-backups/`。重复执行安全。容器普通重启保留补丁；重新创建或升级 ONLYOFFICE 容器后需重新核查并应用。未知代码结构会停止，勿在未经验证的新版本上强制替换。部署后须用含新增修订的实际文件确认只读预览显示正文，不能只检查接口返回成功。
