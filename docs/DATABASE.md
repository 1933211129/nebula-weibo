# 数据库结构

平台使用 MySQL 8.0+。以下结构来自 `server/db.py` 的 SQLAlchemy 元数据；共 17 张表。`schema.sql` 是无数据、无凭据的建表参考，推荐用 `python -m server.cli init` 初始化并写入示例词库。

内容 ID 使用字符串，避免数值精度损失。内容表时间保存为北京时间 ISO 8601 字符串；任务范围为北京时间无时区 DATETIME，调度、租约和管理时间由 UTC 生成。请勿直接混合比较这两类时间。JSON 用于数组和断点。数值型布尔内容字段以 0/1 存储。

内容表全库去重，任务成员关系另存。多数逻辑关联未设置数据库外键；下表仅将实际声明的外键列出。用户表是最近一次可用字段快照，不是历史画像。空值表示未提供或未获取，不等于 0。

| 表 | 用途 | 主键 |
|---|---|---|
| `comments` | 一级评论和回复 | comment_id |
| `cookie_accounts` | 加密 Cookie 通道、节流与租约 | id |
| `crawl_logs` | 平台运行事件 | id |
| `post_query_matches` | 微博与检索词的多对多关联 | post_id, query_id |
| `posts` | 去重微博及公开元数据 | post_id |
| `queries` | 检索词及组成项 | query_id |
| `tasks` | 采集任务、范围和运行状态 | id |
| `users` | 帖子作者与评论者公开元数据 | user_id |
| `comment_audits` | 每个任务中帖子的评论完整性 | task_id, post_id |
| `comment_threads` | 根评论回复遍历状态 | task_id, root_id |
| `job_receipts` | 压缩后保留的作业去重凭据 | task_id, key_hash |
| `jobs` | 持久作业队列、断点与重试 | id |
| `keyword_checks` | 组合关键词正文核验状态 | task_id, post_id, query_id |
| `search_windows` | 检索时间窗口与分页进度 | id |
| `task_comments` | 任务与评论关联 | task_id, comment_id |
| `task_posts` | 任务与微博关联 | task_id, post_id |
| `job_attempts` | 请求尝试与结果记录 | id |

## comments

一级评论和回复。

| 字段 | MySQL 类型 | 允许 NULL | 默认值 | 说明 |
|---|---|---|---|---|
| `comment_id` | `VARCHAR(64)` | 否 | — | 评论 ID；主键 |
| `post_id` | `VARCHAR(64)` | 是 | — | 微博 ID |
| `parent_comment_id` | `VARCHAR(64)` | 是 | — | 直接父评论 ID（一级评论为空） |
| `root_comment_id` | `VARCHAR(64)` | 是 | — | 根评论 ID（一级评论为自身） |
| `comment_level` | `BIGINT` | 是 | — | 1 一级评论 / 2 回复 |
| `comment_text` | `LONGTEXT` | 是 | — | 评论正文 |
| `comment_user_id` | `VARCHAR(64)` | 是 | — | 评论者 ID |
| `reply_to_user_id` | `VARCHAR(64)` | 是 | — | 被回复者 ID（未知为空） |
| `created_at` | `VARCHAR(48)` | 是 | — | 发布时间 / 管理记录创建时间 |
| `likes_count` | `BIGINT` | 是 | — | 点赞数 |
| `reply_count` | `BIGINT` | 是 | — | 接口显示回复数 |
| `comment_source` | `TEXT` | 是 | — | 评论发布来源 |
| `ip_location` | `TEXT` | 是 | — | 公开 IP 属地文本，非 IP 地址 |
| `retrieved_at` | `VARCHAR(48)` | 是 | — | 采集时间 |
| `retrieval_status` | `VARCHAR(48)` | 是 | — | 记录获取状态 |

索引：

```sql
CREATE INDEX ix_comments_post ON comments (post_id);
CREATE INDEX ix_comments_root ON comments (root_comment_id);
```

## cookie_accounts

加密 Cookie 通道、节流与租约。

| 字段 | MySQL 类型 | 允许 NULL | 默认值 | 说明 |
|---|---|---|---|---|
| `id` | `VARCHAR(36)` | 否 | — | 管理字段，见字段名及下方说明；主键 |
| `name` | `VARCHAR(100)` | 否 | — | 管理字段，见字段名及下方说明 |
| `encrypted_value` | `TEXT` | 否 | — | 管理字段，见字段名及下方说明 |
| `fingerprint` | `VARCHAR(64)` | 否 | — | 管理字段，见字段名及下方说明 |
| `status` | `VARCHAR(24)` | 否 | unverified | 管理字段，见字段名及下方说明 |
| `request_interval` | `FLOAT` | 否 | 3 | 管理字段，见字段名及下方说明 |
| `request_interval_min` | `FLOAT` | 否 | 1 | 管理字段，见字段名及下方说明 |
| `request_interval_max` | `FLOAT` | 否 | 2.5 | 管理字段，见字段名及下方说明 |
| `failure_limit` | `INTEGER` | 否 | 5 | 管理字段，见字段名及下方说明 |
| `consecutive_failures` | `INTEGER` | 否 | 0 | 管理字段，见字段名及下方说明 |
| `request_count` | `BIGINT` | 否 | 0 | 管理字段，见字段名及下方说明 |
| `last_checked_at` | `DATETIME` | 是 | — | 管理字段，见字段名及下方说明 |
| `last_error` | `VARCHAR(200)` | 是 | — | 管理字段，见字段名及下方说明 |
| `lease_owner` | `VARCHAR(64)` | 是 | — | 管理字段，见字段名及下方说明 |
| `lease_until` | `DATETIME` | 是 | — | 管理字段，见字段名及下方说明 |
| `next_request_at` | `DATETIME(3)` | 是 | — | 管理字段，见字段名及下方说明 |
| `created_at` | `DATETIME` | 否 | 应用生成时间 | 发布时间 / 管理记录创建时间 |

唯一约束：fingerprint。

## crawl_logs

平台运行事件。

| 字段 | MySQL 类型 | 允许 NULL | 默认值 | 说明 |
|---|---|---|---|---|
| `id` | `BIGINT` | 否 | — | 管理字段，见字段名及下方说明；主键 |
| `task_id` | `VARCHAR(36)` | 是 | — | 管理字段，见字段名及下方说明 |
| `cookie_id` | `VARCHAR(36)` | 是 | — | 管理字段，见字段名及下方说明 |
| `level` | `VARCHAR(16)` | 否 | info | 管理字段，见字段名及下方说明 |
| `event` | `VARCHAR(64)` | 否 | — | 管理字段，见字段名及下方说明 |
| `message` | `VARCHAR(240)` | 否 | — | 管理字段，见字段名及下方说明 |
| `details` | `JSON` | 是 | — | 管理字段，见字段名及下方说明 |
| `created_at` | `DATETIME` | 否 | 应用生成时间 | 发布时间 / 管理记录创建时间 |

索引：

```sql
CREATE INDEX ix_logs_task_time ON crawl_logs (task_id, created_at);
```

## post_query_matches

微博与检索词的多对多关联。

| 字段 | MySQL 类型 | 允许 NULL | 默认值 | 说明 |
|---|---|---|---|---|
| `post_id` | `VARCHAR(64)` | 否 | — | 微博 ID；主键 |
| `query_id` | `VARCHAR(64)` | 否 | — | 检索词 ID；posts 中为数组；主键 |
| `matched_keyword` | `TEXT` | 是 | — | 命中的检索词；posts 中为数组 |
| `retrieved_at` | `VARCHAR(48)` | 是 | — | 采集时间 |

## posts

去重微博及公开元数据。

| 字段 | MySQL 类型 | 允许 NULL | 默认值 | 说明 |
|---|---|---|---|---|
| `post_id` | `VARCHAR(64)` | 否 | — | 微博 ID；主键 |
| `post_url` | `TEXT` | 是 | — | 微博链接 |
| `post_text` | `LONGTEXT` | 是 | — | 帖子正文（尽可能补全长文） |
| `user_id` | `VARCHAR(64)` | 是 | — | 用户 ID |
| `created_at` | `VARCHAR(48)` | 是 | — | 发布时间 / 管理记录创建时间 |
| `year_month` | `VARCHAR(48)` | 是 | — | 发帖年月 |
| `is_original` | `BIGINT` | 是 | — | 是否原创 |
| `is_repost` | `BIGINT` | 是 | — | 是否转发 |
| `reposted_post_id` | `VARCHAR(64)` | 是 | — | 被转发微博 ID |
| `reposted_user_id` | `VARCHAR(64)` | 是 | — | 被转发作者 ID |
| `repost_text` | `LONGTEXT` | 是 | — | 转发者正文（非被转发原文） |
| `topics` | `JSON` | 是 | — | 话题数组 |
| `mentions` | `JSON` | 是 | — | 正文提及的账号名称数组 |
| `urls` | `JSON` | 是 | — | 正文链接数组（不下载媒体） |
| `media_type` | `VARCHAR(48)` | 是 | — | none / image / video / image_video |
| `image_count` | `BIGINT` | 是 | — | 图片数量 |
| `video_count` | `BIGINT` | 是 | — | 视频数量 |
| `post_source` | `TEXT` | 是 | — | 发布来源 |
| `likes_count` | `BIGINT` | 是 | — | 点赞数 |
| `comments_count` | `BIGINT` | 是 | — | 接口显示评论数 |
| `reposts_count` | `BIGINT` | 是 | — | 转发数 |
| `favorites_count` | `BIGINT` | 是 | — | 收藏数（接口未提供时为空） |
| `ip_location` | `TEXT` | 是 | — | 公开 IP 属地文本，非 IP 地址 |
| `query_id` | `JSON` | 是 | — | 检索词 ID；posts 中为数组 |
| `matched_keyword` | `JSON` | 是 | — | 命中的检索词；posts 中为数组 |
| `retrieved_at` | `VARCHAR(48)` | 是 | — | 采集时间 |
| `retrieval_status` | `VARCHAR(48)` | 是 | — | 记录获取状态 |

索引：

```sql
CREATE INDEX ix_posts_date ON posts (created_at);
CREATE INDEX ix_posts_month ON posts (`year_month`);
```

## queries

检索词及组成项。

| 字段 | MySQL 类型 | 允许 NULL | 默认值 | 说明 |
|---|---|---|---|---|
| `query_id` | `VARCHAR(64)` | 否 | — | 检索词 ID；posts 中为数组；主键 |
| `category` | `TEXT` | 是 | — | 词库分类 |
| `query` | `TEXT` | 是 | — | 实际检索表达式 |
| `terms` | `JSON` | 是 | — | 组成检索项的词数组 |

## tasks

采集任务、范围和运行状态。

| 字段 | MySQL 类型 | 允许 NULL | 默认值 | 说明 |
|---|---|---|---|---|
| `id` | `VARCHAR(36)` | 否 | — | 管理字段，见字段名及下方说明；主键 |
| `name` | `VARCHAR(160)` | 否 | — | 管理字段，见字段名及下方说明 |
| `status` | `VARCHAR(24)` | 否 | draft | 管理字段，见字段名及下方说明 |
| `start_at` | `DATETIME` | 否 | — | 管理字段，见字段名及下方说明 |
| `end_at` | `DATETIME` | 否 | — | 管理字段，见字段名及下方说明 |
| `query_ids` | `JSON` | 否 | — | 管理字段，见字段名及下方说明 |
| `cookie_ids` | `JSON` | 否 | — | 管理字段，见字段名及下方说明 |
| `include_comments` | `BOOL` | 否 | True | 管理字段，见字段名及下方说明 |
| `request_limit` | `BIGINT` | 是 | — | 管理字段，见字段名及下方说明 |
| `search_mode` | `VARCHAR(24)` | 否 | hourly | 管理字段，见字段名及下方说明 |
| `compacted_jobs` | `BIGINT` | 否 | 0 | 管理字段，见字段名及下方说明 |
| `request_count` | `BIGINT` | 否 | 0 | 管理字段，见字段名及下方说明 |
| `active_seconds` | `FLOAT` | 否 | 0 | 管理字段，见字段名及下方说明 |
| `active_since` | `DATETIME` | 是 | — | 管理字段，见字段名及下方说明 |
| `created_at` | `DATETIME` | 否 | 应用生成时间 | 发布时间 / 管理记录创建时间 |
| `finished_at` | `DATETIME` | 是 | — | 管理字段，见字段名及下方说明 |
| `last_error` | `VARCHAR(200)` | 是 | — | 管理字段，见字段名及下方说明 |

## users

帖子作者与评论者公开元数据。

| 字段 | MySQL 类型 | 允许 NULL | 默认值 | 说明 |
|---|---|---|---|---|
| `user_id` | `VARCHAR(64)` | 否 | — | 用户 ID；主键 |
| `screen_name` | `TEXT` | 是 | — | 昵称 |
| `verified` | `BIGINT` | 是 | — | 是否认证 |
| `verified_type` | `BIGINT` | 是 | — | 认证类型 |
| `verified_reason` | `TEXT` | 是 | — | 认证说明 |
| `followers_count` | `BIGINT` | 是 | — | 粉丝数 |
| `follow_count` | `BIGINT` | 是 | — | 关注数 |
| `statuses_count` | `BIGINT` | 是 | — | 微博数 |
| `user_description` | `LONGTEXT` | 是 | — | 个人简介 |
| `profile_retrieved_at` | `VARCHAR(48)` | 是 | — | 用户元数据采集时间 |

## comment_audits

每个任务中帖子的评论完整性。

| 字段 | MySQL 类型 | 允许 NULL | 默认值 | 说明 |
|---|---|---|---|---|
| `task_id` | `VARCHAR(36)` | 否 | — | 管理字段，见字段名及下方说明；主键；外键 tasks.id |
| `post_id` | `VARCHAR(64)` | 否 | — | 微博 ID；主键 |
| `displayed_comments_count` | `BIGINT` | 是 | — | 管理字段，见字段名及下方说明 |
| `retrieved_comments_count` | `BIGINT` | 是 | 0 | 管理字段，见字段名及下方说明 |
| `retrieved_first_level_count` | `BIGINT` | 是 | 0 | 管理字段，见字段名及下方说明 |
| `retrieved_reply_count` | `BIGINT` | 是 | 0 | 管理字段，见字段名及下方说明 |
| `first_level_exhausted` | `BOOL` | 是 | False | 管理字段，见字段名及下方说明 |
| `comments_complete` | `BOOL` | 是 | False | 管理字段，见字段名及下方说明 |
| `incomplete_reason` | `JSON` | 是 | — | 管理字段，见字段名及下方说明 |
| `updated_at` | `DATETIME` | 是 | 应用生成时间 | 管理字段，见字段名及下方说明 |

## comment_threads

根评论回复遍历状态。

| 字段 | MySQL 类型 | 允许 NULL | 默认值 | 说明 |
|---|---|---|---|---|
| `task_id` | `VARCHAR(36)` | 否 | — | 管理字段，见字段名及下方说明；主键；外键 tasks.id |
| `root_id` | `VARCHAR(64)` | 否 | — | 管理字段，见字段名及下方说明；主键 |
| `post_id` | `VARCHAR(64)` | 否 | — | 微博 ID |
| `expected_count` | `BIGINT` | 是 | — | 管理字段，见字段名及下方说明 |
| `exhausted` | `BOOL` | 是 | False | 管理字段，见字段名及下方说明 |
| `last_error` | `VARCHAR(200)` | 是 | — | 管理字段，见字段名及下方说明 |

## job_receipts

压缩后保留的作业去重凭据。

| 字段 | MySQL 类型 | 允许 NULL | 默认值 | 说明 |
|---|---|---|---|---|
| `task_id` | `VARCHAR(36)` | 否 | — | 管理字段，见字段名及下方说明；主键；外键 tasks.id |
| `key_hash` | `VARCHAR(64)` | 否 | — | 管理字段，见字段名及下方说明；主键 |

## jobs

持久作业队列、断点与重试。

| 字段 | MySQL 类型 | 允许 NULL | 默认值 | 说明 |
|---|---|---|---|---|
| `id` | `VARCHAR(36)` | 否 | — | 管理字段，见字段名及下方说明；主键 |
| `task_id` | `VARCHAR(36)` | 否 | — | 管理字段，见字段名及下方说明；外键 tasks.id |
| `kind` | `VARCHAR(24)` | 否 | — | 管理字段，见字段名及下方说明 |
| `dedupe_key` | `VARCHAR(240)` | 否 | — | 管理字段，见字段名及下方说明 |
| `payload` | `JSON` | 否 | — | 管理字段，见字段名及下方说明 |
| `status` | `VARCHAR(24)` | 否 | pending | 管理字段，见字段名及下方说明 |
| `priority` | `INTEGER` | 否 | 0 | 管理字段，见字段名及下方说明 |
| `attempts` | `INTEGER` | 否 | 0 | 管理字段，见字段名及下方说明 |
| `cookie_id` | `VARCHAR(36)` | 是 | — | 管理字段，见字段名及下方说明 |
| `lease_owner` | `VARCHAR(64)` | 是 | — | 管理字段，见字段名及下方说明 |
| `lease_until` | `DATETIME` | 是 | — | 管理字段，见字段名及下方说明 |
| `last_error` | `VARCHAR(200)` | 是 | — | 管理字段，见字段名及下方说明 |
| `created_at` | `DATETIME` | 否 | 应用生成时间 | 发布时间 / 管理记录创建时间 |
| `finished_at` | `DATETIME` | 是 | — | 管理字段，见字段名及下方说明 |

索引：

```sql
CREATE INDEX ix_jobs_lease ON jobs (lease_until);
CREATE INDEX ix_jobs_queue ON jobs (task_id, status, priority);
```

唯一约束：task_id, dedupe_key。

## keyword_checks

组合关键词正文核验状态。

| 字段 | MySQL 类型 | 允许 NULL | 默认值 | 说明 |
|---|---|---|---|---|
| `task_id` | `VARCHAR(36)` | 否 | — | 管理字段，见字段名及下方说明；主键；外键 tasks.id |
| `post_id` | `VARCHAR(64)` | 否 | — | 微博 ID；主键 |
| `query_id` | `VARCHAR(64)` | 否 | — | 检索词 ID；posts 中为数组；主键 |
| `status` | `VARCHAR(32)` | 否 | pending | 管理字段，见字段名及下方说明 |
| `missing_terms` | `JSON` | 是 | — | 管理字段，见字段名及下方说明 |
| `checked_at` | `DATETIME` | 是 | — | 管理字段，见字段名及下方说明 |

## search_windows

检索时间窗口与分页进度。

| 字段 | MySQL 类型 | 允许 NULL | 默认值 | 说明 |
|---|---|---|---|---|
| `id` | `VARCHAR(36)` | 否 | — | 管理字段，见字段名及下方说明；主键 |
| `task_id` | `VARCHAR(36)` | 否 | — | 管理字段，见字段名及下方说明；外键 tasks.id |
| `query_id` | `VARCHAR(64)` | 否 | — | 检索词 ID；posts 中为数组 |
| `parent_id` | `VARCHAR(36)` | 是 | — | 管理字段，见字段名及下方说明 |
| `range_end` | `DATETIME` | 是 | — | 管理字段，见字段名及下方说明 |
| `start_at` | `DATETIME` | 否 | — | 管理字段，见字段名及下方说明 |
| `end_at` | `DATETIME` | 否 | — | 管理字段，见字段名及下方说明 |
| `status` | `VARCHAR(24)` | 否 | pending | 管理字段，见字段名及下方说明 |
| `current_page` | `INTEGER` | 是 | 0 | 管理字段，见字段名及下方说明 |
| `updated_at` | `DATETIME` | 是 | 应用生成时间 | 管理字段，见字段名及下方说明 |

索引：

```sql
CREATE INDEX ix_windows_task ON search_windows (task_id, status);
```

## task_comments

任务与评论关联。

| 字段 | MySQL 类型 | 允许 NULL | 默认值 | 说明 |
|---|---|---|---|---|
| `task_id` | `VARCHAR(36)` | 否 | — | 管理字段，见字段名及下方说明；主键；外键 tasks.id |
| `comment_id` | `VARCHAR(64)` | 否 | — | 评论 ID；主键 |
| `created_at` | `DATETIME` | 是 | 应用生成时间 | 发布时间 / 管理记录创建时间 |

## task_posts

任务与微博关联。

| 字段 | MySQL 类型 | 允许 NULL | 默认值 | 说明 |
|---|---|---|---|---|
| `task_id` | `VARCHAR(36)` | 否 | — | 管理字段，见字段名及下方说明；主键；外键 tasks.id |
| `post_id` | `VARCHAR(64)` | 否 | — | 微博 ID；主键 |
| `created_at` | `DATETIME` | 是 | 应用生成时间 | 发布时间 / 管理记录创建时间 |

## job_attempts

请求尝试与结果记录。

| 字段 | MySQL 类型 | 允许 NULL | 默认值 | 说明 |
|---|---|---|---|---|
| `id` | `VARCHAR(36)` | 否 | — | 管理字段，见字段名及下方说明；主键 |
| `job_id` | `VARCHAR(36)` | 否 | — | 管理字段，见字段名及下方说明；外键 jobs.id |
| `task_id` | `VARCHAR(36)` | 否 | — | 管理字段，见字段名及下方说明 |
| `cookie_id` | `VARCHAR(36)` | 否 | — | 管理字段，见字段名及下方说明 |
| `checkpoint` | `JSON` | 否 | — | 管理字段，见字段名及下方说明 |
| `kind` | `VARCHAR(24)` | 否 | — | 管理字段，见字段名及下方说明 |
| `started_at` | `DATETIME(6)` | 否 | — | 管理字段，见字段名及下方说明 |
| `finished_at` | `DATETIME(6)` | 是 | — | 管理字段，见字段名及下方说明 |
| `outcome` | `VARCHAR(40)` | 否 | running | 管理字段，见字段名及下方说明 |
| `http_status` | `INTEGER` | 是 | — | 管理字段，见字段名及下方说明 |

索引：

```sql
CREATE INDEX ix_attempts_task ON job_attempts (task_id, started_at);
```

## 调度与完整性字段说明

`cookie_accounts.encrypted_value` 为 Fernet 密文，`fingerprint` 为 SUB 值的 SHA-256 摘要；二者都不应对外发布。`request_interval_min/max` 控制请求随机间隔，`failure_limit/consecutive_failures` 控制连续失败保护，`lease_owner/lease_until` 保证同一通道请求互斥，`next_request_at` 为下次允许请求时间。

`tasks.query_ids/cookie_ids` 保存任务分配，`include_comments` 控制评论采集，`search_mode` 为 hourly/adaptive，`request_count` 为请求尝试数。`request_limit` 为兼容 CLI 额度字段，界面任务没有总请求上限。`active_seconds/active_since` 记录运行时长，`compacted_jobs` 记录已压缩作业数。

`search_windows.parent_id` 关联拆分窗口，`range_end` 保存后续范围终点，`current_page` 保存分页位置。`jobs.kind/payload` 保存作业类型及请求断点，`dedupe_key` 在任务内唯一；`priority` 用于调度，`attempts` 为尝试次数。`job_receipts.key_hash` 在已完成作业压缩后保留去重信息。`job_attempts.checkpoint` 保存当次断点，`outcome/http_status` 保存结果与响应状态。

`comment_audits` 对比接口显示数量与实际入库的一级评论、回复数量，`first_level_exhausted` 标记一级分页结束，`comments_complete` 为程序的完整性判断，`incomplete_reason` 保存未完整原因。`comment_threads.expected_count/exhausted/last_error` 分别记录回复预期数、遍历结束与失败原因。`keyword_checks.status/missing_terms/checked_at` 记录组合词核验结果、缺失词和核验时间。

`status`、`last_error`、`finished_at` 等管理字段记录当前状态、最近错误和结束时间。`crawl_logs.details` 保存结构化事件信息。接口可见性、删除和访问限制会影响结果；分页结束不能证明平台历史内容已全部召回。

## 初始化与维护

先由数据库管理员创建数据库并设置 utf8mb4，再配置连接串。初始化账户需具备建表、修改表结构与普通读写权限；初始化结束后可按部署要求收紧运行账户权限。首次初始化使用 `python -m server.cli init`，启动 Web 或 worker 不会隐式建表。升级前备份数据库和 Cookie 加密密钥，初始化命令含结构补齐逻辑，不是完整版本化迁移框架。

`schema.sql` 中 DEFAULT 仅包含数据库端默认值；其余默认值由应用插入时生成。不要用数据库导出文件替代这里的纯结构文件发布。
