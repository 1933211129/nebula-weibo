# 🌌 Nebula Weibo · 微博研究采集工作台

面向学习与研究的微博公开内容采集平台，提供可视化任务管理、**小时级时间窗口、两组关键词交叉检索、评论与回复采集、关联用户元数据、断点恢复与 JSONL 导出**。采用 React + TypeScript、FastAPI 与 MySQL 持久队列。

> ⚠️ **免责声明：本项目仅用于学习研究，禁止大规模爬取和商业用途。使用者必须遵守微博网站 robots 协议、平台规则及适用法律法规；违规采集、使用或传播数据存在法律风险，相关责任由使用者承担。** 请在使用前核查 [微博 robots 协议](https://weibo.com/robots.txt) 和目标站点的适用规则，确认研究范围及授权。免责声明不构成采集授权，程序也不会自动完成合规审查。

本项目基于 [nghuyong/WeiboSpider](https://github.com/nghuyong/WeiboSpider) 修改完善，感谢原作者与贡献者提供的开源基础。保留上游 MIT 许可证及版权声明，详见 [LICENSE](LICENSE)。上述用途要求为本项目的使用声明；上游 MIT 许可证原文保持不变。

## ✨ 功能特点

| 能力 | 具体行为 |
|---|---|
| 小时级检索 | 将“检索词 × 北京时间一小时”作为独立检索窗口，保存分页位置；单个小时失败不要求所有小时从头重跑。 |
| 自适应拆分 | 默认按天探测：结果达到 500 条时拆为小时；数量不明确时结合分页判断，仍无法确定则拆小时。也可选择始终逐小时。 |
| 交叉关键词 | A 组和 B 组两两配对，自动去重；在补全后的帖子正文中核验全部组成词，记录缺失词与核验状态。 |
| 评论与回复 | 可选采集公开可见的一级评论及回复，保留直接父评论、根评论、回复对象和分页断点。 |
| 用户元数据 | 随帖子与评论响应保存作者公开昵称、认证信息、粉丝数、关注数、微博数与简介。 |
| 恢复与可追溯 | 暂停、继续、失败重试、针对性补抓、请求尝试记录、评论完整性检查与运行日志。 |
| Cookie 通道 | 支持浏览器导出 JSON，凭据加密保存；通道间可独立调度，同一 Cookie 请求通过租约互斥，异常时保留断点。 |
| 数据管理 | 跨任务按内容 ID 去重，任务关系独立保存；按任务浏览、筛选和导出 JSONL。 |
| 独立运行 | Web 工作台、采集执行器、数据库初始化分别启动；仅打开工作台不会自动开始采集。 |

时间拆分有助于细化检索范围，但无法消除微博搜索可见性和分页上限。**日期覆盖表示搜索范围已遍历，不代表历史微博或评论 100% 完整。** 接口未提供的字段保留为空。

## 🖥️ 界面预览

![采集工作台界面示例](docs/images/overview.png)

现有部署的界面示例，显示采集统计、日期覆盖和通道状态；截图数值不作为性能或采集完整性承诺，当前界面可能略有差异。

## 🚀 本地启动

推荐 Python 3.12、Node.js 22、MySQL 8.0+。MySQL 需自行准备，项目不会安装数据库服务。以下命令在项目根目录执行。

```sh
git clone https://github.com/YuanBoKong/nebula-weibo.git
cd nebula-weibo
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env.local
python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

把生成的密钥填入 `.env.local` 的 `COOKIE_ENCRYPTION_KEY`，并将 `DATABASE_URL` 改为自己的连接信息。示例仅含占位符，不能直接连接数据库。密码含 `@`、`:`、`/` 等字符时，须对连接串中的密码进行 URL 编码。

```dotenv
DATABASE_URL=mysql+pymysql://nebula_example_user:CHANGE_ME@127.0.0.1:3306/weibo_research?charset=utf8mb4
COOKIE_ENCRYPTION_KEY=GENERATE_WITH_FERNET
```

由数据库管理员预先创建数据库，并为应用账户配置相应权限：

```sql
CREATE DATABASE weibo_research CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

```sh
chmod 600 .env.local
# 首次安装：创建表、补齐结构并导入示例词库
python -m server.cli init
cd frontend
npm ci
npm run build
cd ..
python -m uvicorn server.app:app --host 127.0.0.1 --port 8877
```

打开 **http://127.0.0.1:8877**。交互式 API 文档位于 `/api/docs`，健康检查为 `/api/health`。已有数据库日常运行无需重复初始化。密钥用于解密已保存的 Cookie，须与数据库一起妥善备份；更换密钥后原有通道不能直接解密。

需要前端热更新时，另开终端进入 `frontend` 执行 `npm run dev`；开发服务将 `/api` 代理到本机 8877。后端可加 `--reload`，仍不会自动启动采集。

## 🍪 获取与导入 Cookie

1. 在 **Google Chrome 浏览器安装 Cookie-Editor 插件**，确认扩展来源可信。
2. 打开 [https://weibo.com/](https://weibo.com/)，登录自己的账号，保持当前页面为微博网站。
3. 打开 Cookie-Editor，选择 **Export → JSON**，复制完整 JSON 数组。
4. 在工作台进入 **Cookie 通道**，新增通道并粘贴 JSON，保存后验证可用性。**可直接使用导出内容，无需手工转换为 Cookie 请求头。**
5. 为任务分配该通道。失效时重新登录并导出，更新原通道后验证，再重试任务断点。

平台识别导出项的 `name`、`value` 与 `domain`，需要有效的 `SUB` 登录凭据及正确的微博域名。请粘贴数组全文，不要只复制某个 value，也不要粘贴其他网站的导出内容。兼容完整 Cookie 请求头字符串，但推荐 JSON，便于保留域名信息。验证操作会向微博发起请求。

Cookie 相当于登录凭据，请勿出现在截图、Issue、提交、日志或公开示例中。平台通过 `COOKIE_ENCRYPTION_KEY` 加密后入库；加密不替代访问控制。仓库不提供任何可用 Cookie。

## 🔎 创建小范围研究任务

在任务管理中创建任务，选择北京时间整点范围。开始包含、结束不包含，例如 `2026-01-01 09:00` 至 `2026-01-01 11:00` 对应两个小时。

普通词支持逗号、顿号、分号、空格和换行拆分；可选“保留词内空格”。交叉检索示例：A 组填写“城市公交、地铁”，B 组填写“通勤、拥挤”，生成四个组合。组合核验忽略英文大小写与全半角，不扩展同义词，不以评论、用户名或被转发原文替代当前帖子正文。最多 1000 个检索项，每项最多 200 字符；这是输入限制，不是建议采集规模。

勾选评论采集后，程序继续处理一级评论及其回复；取消时仍会保存帖子中可获得的用户元数据。用户元数据来自返回内容，不单独执行完整用户主页、粉丝或关注列表遍历。图片和视频仅记录类型与数量，不下载媒体文件。

创建任务后为草稿；点击启动将其标为可执行。另开终端启动独立执行器才会实际采集：

```sh
source .venv/bin/activate
# 限定任务，并在约 120 秒后停止领取新作业，等待在途请求收尾
python -m server.executor --task TASK_ID --seconds 120
# 持续处理已启动任务，Ctrl+C 停止执行器
python -m server.executor
```

执行器退出后保留断点。暂停任务应在界面操作或使用 `python -m server.cli pause TASK_ID`；恢复使用 `resume`，失败重试使用 `retry`，状态查看使用 `status`。界面任务没有总请求上限，请主动限定研究范围、运行时长与请求频率；遇到风控、验证码、权限限制应停止并检查，不应以多账号规避限制。

总览按所选任务统计，另有全库去重总量；两者口径不同，不能把多个任务的计数相加当作全库量。评论完整性同时记录接口显示数量、实际一级评论/回复数量、遍历状态和未完整原因。

## 📦 可采集字段与导出

数据浏览提供帖子、评论、用户与关键词关联的查询。JSONL 每行一条 JSON 记录，导出与页面使用相同任务和文本筛选条件。内容 ID 按字符串处理，避免电子表格将长 ID 四舍五入；时间、计数和资料为采集时可见值，不是长期历史快照。

以下列出当前平台保存的完整内容字段。`normal` 以外的记录状态可包括未获取、正文未补全或超出时间范围；分析前应结合状态筛选。字段存在不意味着每条记录都有值。

### `posts` · 去重微博及公开元数据

| 字段 | 含义 |
|---|---|
| `post_id` | 微博 ID |
| `post_url` | 微博链接 |
| `post_text` | 帖子正文（尽可能补全长文） |
| `user_id` | 用户 ID |
| `created_at` | 发布时间 / 管理记录创建时间 |
| `year_month` | 发帖年月 |
| `is_original` | 是否原创 |
| `is_repost` | 是否转发 |
| `reposted_post_id` | 被转发微博 ID |
| `reposted_user_id` | 被转发作者 ID |
| `repost_text` | 转发者正文（非被转发原文） |
| `topics` | 话题数组 |
| `mentions` | 正文提及的账号名称数组 |
| `urls` | 正文链接数组（不下载媒体） |
| `media_type` | none / image / video / image_video |
| `image_count` | 图片数量 |
| `video_count` | 视频数量 |
| `post_source` | 发布来源 |
| `likes_count` | 点赞数 |
| `comments_count` | 接口显示评论数 |
| `reposts_count` | 转发数 |
| `favorites_count` | 收藏数（接口未提供时为空） |
| `ip_location` | 公开 IP 属地文本，非 IP 地址 |
| `query_id` | 检索词 ID；posts 中为数组 |
| `matched_keyword` | 命中的检索词；posts 中为数组 |
| `retrieved_at` | 采集时间 |
| `retrieval_status` | 记录获取状态 |

### `comments` · 一级评论和回复

| 字段 | 含义 |
|---|---|
| `comment_id` | 评论 ID |
| `post_id` | 微博 ID |
| `parent_comment_id` | 直接父评论 ID（一级评论为空） |
| `root_comment_id` | 根评论 ID（一级评论为自身） |
| `comment_level` | 1 一级评论 / 2 回复 |
| `comment_text` | 评论正文 |
| `comment_user_id` | 评论者 ID |
| `reply_to_user_id` | 被回复者 ID（未知为空） |
| `created_at` | 发布时间 / 管理记录创建时间 |
| `likes_count` | 点赞数 |
| `reply_count` | 接口显示回复数 |
| `comment_source` | 评论发布来源 |
| `ip_location` | 公开 IP 属地文本，非 IP 地址 |
| `retrieved_at` | 采集时间 |
| `retrieval_status` | 记录获取状态 |

### `users` · 帖子作者与评论者公开元数据

| 字段 | 含义 |
|---|---|
| `user_id` | 用户 ID |
| `screen_name` | 昵称 |
| `verified` | 是否认证 |
| `verified_type` | 认证类型 |
| `verified_reason` | 认证说明 |
| `followers_count` | 粉丝数 |
| `follow_count` | 关注数 |
| `statuses_count` | 微博数 |
| `user_description` | 个人简介 |
| `profile_retrieved_at` | 用户元数据采集时间 |

### `post_query_matches` · 微博与检索词的多对多关联

| 字段 | 含义 |
|---|---|
| `post_id` | 微博 ID |
| `query_id` | 检索词 ID；posts 中为数组 |
| `matched_keyword` | 命中的检索词；posts 中为数组 |
| `retrieved_at` | 采集时间 |

### `queries` · 检索词及组成项

| 字段 | 含义 |
|---|---|
| `query_id` | 检索词 ID；posts 中为数组 |
| `category` | 词库分类 |
| `query` | 实际检索表达式 |
| `terms` | 组成检索项的词数组 |

## 🗄️ 数据库结构

平台使用 17 张 MySQL 表，完整字段类型、默认值、主键、索引、关联与时间语义见 **[数据库说明](docs/DATABASE.md)**；无数据建表参考见 **[schema.sql](docs/schema.sql)**。

| 分组 | 表 |
|---|---|
| 内容与检索词 | `posts`、`comments`、`users`、`queries`、`post_query_matches` |
| 任务与队列 | `tasks`、`search_windows`、`jobs`、`job_receipts`、`job_attempts` |
| 通道与日志 | `cookie_accounts`、`crawl_logs` |
| 任务成员关系 | `task_posts`、`task_comments` |
| 完整性与核验 | `comment_audits`、`comment_threads`、`keyword_checks` |

帖子、评论和用户跨任务去重，`task_posts` 与 `task_comments` 保留任务归属；帖子可关联多个检索词。内容字段时间为带北京时间偏移的字符串，任务范围为北京时间 DATETIME，调度租约时间为 UTC。请按数据库文档处理时区。

## 🐳 Docker 部署

镜像包含前后端，不包含 MySQL。配置 `.env`（与 `.env.local` 相同键名）后构建并启动：

```sh
cp .env.example .env
# 编辑 .env：配置容器能够访问的数据库地址和持久加密密钥
chmod 600 .env
docker build -t nebula-weibo:local .
# 仅首次建表 / 明确维护结构时运行
docker compose --profile maintenance run --rm db-init
docker compose up -d app
```

打开 **http://127.0.0.1:8878/weibo**。容器里的 `127.0.0.1` 指容器自身，数据库地址应使用实际可达主机；Docker Desktop 连接宿主机数据库时可按环境使用 `host.docker.internal`。

```sh
# 显式启用采集执行器
docker compose --profile collector up -d worker
# 停止采集，保持工作台在线
docker compose stop worker
# 查看状态与日志
docker compose ps
docker compose logs --tail 100 app worker
```

默认 `docker compose up -d` 只启动平台；已启动的 worker 需显式停止。默认镜像以 `/weibo` 为入口，修改入口时应同步调整构建参数 `APP_BASE_PATH` 与运行环境变量。镜像按当前构建架构生成，跨架构部署时使用对应 `--platform` 构建。

**平台没有登录与权限系统。** 默认发布配置仅绑定本机回环地址；所有能够访问平台的人都可管理任务、Cookie 通道和数据。远程部署应先设置受控网络、认证代理与 HTTPS，再按实际需求调整监听方式。

## 🧩 代码结构与验证

```text
server/                 FastAPI、数据库、任务生命周期、独立执行器
server/collector/       请求、解析、调度、限速、恢复与核验
weibospider/research*.py 共享解析器与历史研究样本入口
weibospider/spiders/    上游 Scrapy 采集器（保留来源代码）
frontend/src/           React 工作台
tests/unit/             离线解析与调度行为回归
tests/integration/      显式启用的 MySQL 集成测试
tests/live/             会访问微博的真实采集测试
docs/                   公开数据库说明、建表参考与界面图片
```

现代平台依赖安装在根目录，不需要 Scrapy。保留的历史 Scrapy 入口依赖其原有运行环境与本地 `cookie.txt`，不属于本 README 的平台启动流程，也不代表上述工作台提供粉丝/关注列表等全部历史功能。

```sh
pip install -r requirements-dev.txt
python -B -m unittest discover -s tests/unit -v
ruff check server tests
cd frontend
npm ci
npm run build
```

MySQL 集成测试需显式设置 `RUN_MYSQL_TESTS=1`，请只在独立测试库中运行；真实采集测试会消耗请求，不能作为默认 CI。仓库不包含采集结果、数据库导出、真实 Cookie、私有环境配置、参考项目副本或内部工作记录。

## 🤝 致谢与使用边界

感谢 [nghuyong/WeiboSpider](https://github.com/nghuyong/WeiboSpider) 的开源贡献。本项目在其基础上增加研究任务工作台、小时窗口与自适应调度、交叉关键词核验、关系化存储与恢复能力。上游版权及 MIT 许可证保留于 [LICENSE](LICENSE)。

请仅在合法、获授权的学习研究范围内使用，禁止大规模爬取和商用，遵守微博 robots 协议与法律法规。不要公开含个人信息的原始研究数据或可用登录凭据；研究结果发布前应完成必要的匿名化与伦理审查。
