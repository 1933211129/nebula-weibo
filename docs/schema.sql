-- Schema only. No credentials or collected records.
-- Prefer python -m server.cli init for initial setup and query seeding.

CREATE TABLE comments (
	comment_id VARCHAR(64) NOT NULL,
	post_id VARCHAR(64),
	parent_comment_id VARCHAR(64),
	root_comment_id VARCHAR(64),
	comment_level BIGINT,
	comment_text LONGTEXT,
	comment_user_id VARCHAR(64),
	reply_to_user_id VARCHAR(64),
	created_at VARCHAR(48),
	likes_count BIGINT,
	reply_count BIGINT,
	comment_source TEXT,
	ip_location TEXT,
	retrieved_at VARCHAR(48),
	retrieval_status VARCHAR(48),
	PRIMARY KEY (comment_id)
);
CREATE INDEX ix_comments_post ON comments (post_id);
CREATE INDEX ix_comments_root ON comments (root_comment_id);

CREATE TABLE cookie_accounts (
	id VARCHAR(36) NOT NULL,
	name VARCHAR(100) NOT NULL,
	encrypted_value TEXT NOT NULL,
	fingerprint VARCHAR(64) NOT NULL,
	status VARCHAR(24) NOT NULL,
	request_interval FLOAT NOT NULL,
	request_interval_min FLOAT NOT NULL DEFAULT '1',
	request_interval_max FLOAT NOT NULL DEFAULT '2.5',
	failure_limit INTEGER NOT NULL DEFAULT '5',
	consecutive_failures INTEGER NOT NULL DEFAULT '0',
	request_count BIGINT NOT NULL,
	last_checked_at DATETIME,
	last_error VARCHAR(200),
	lease_owner VARCHAR(64),
	lease_until DATETIME,
	next_request_at DATETIME(3),
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (fingerprint)
);


CREATE TABLE crawl_logs (
	id BIGINT NOT NULL AUTO_INCREMENT,
	task_id VARCHAR(36),
	cookie_id VARCHAR(36),
	level VARCHAR(16) NOT NULL,
	event VARCHAR(64) NOT NULL,
	message VARCHAR(240) NOT NULL,
	details JSON,
	created_at DATETIME NOT NULL,
	PRIMARY KEY (id)
);
CREATE INDEX ix_logs_task_time ON crawl_logs (task_id, created_at);

CREATE TABLE post_query_matches (
	post_id VARCHAR(64) NOT NULL,
	query_id VARCHAR(64) NOT NULL,
	matched_keyword TEXT,
	retrieved_at VARCHAR(48),
	PRIMARY KEY (post_id, query_id)
);


CREATE TABLE posts (
	post_id VARCHAR(64) NOT NULL,
	post_url TEXT,
	post_text LONGTEXT,
	user_id VARCHAR(64),
	created_at VARCHAR(48),
	`year_month` VARCHAR(48),
	is_original BIGINT,
	is_repost BIGINT,
	reposted_post_id VARCHAR(64),
	reposted_user_id VARCHAR(64),
	repost_text LONGTEXT,
	topics JSON,
	mentions JSON,
	urls JSON,
	media_type VARCHAR(48),
	image_count BIGINT,
	video_count BIGINT,
	post_source TEXT,
	likes_count BIGINT,
	comments_count BIGINT,
	reposts_count BIGINT,
	favorites_count BIGINT,
	ip_location TEXT,
	query_id JSON,
	matched_keyword JSON,
	retrieved_at VARCHAR(48),
	retrieval_status VARCHAR(48),
	PRIMARY KEY (post_id)
);
CREATE INDEX ix_posts_date ON posts (created_at);
CREATE INDEX ix_posts_month ON posts (`year_month`);

CREATE TABLE queries (
	query_id VARCHAR(64) NOT NULL,
	category TEXT,
	query TEXT,
	terms JSON,
	PRIMARY KEY (query_id)
);


CREATE TABLE tasks (
	id VARCHAR(36) NOT NULL,
	name VARCHAR(160) NOT NULL,
	status VARCHAR(24) NOT NULL,
	start_at DATETIME NOT NULL,
	end_at DATETIME NOT NULL,
	query_ids JSON NOT NULL,
	cookie_ids JSON NOT NULL,
	include_comments BOOL NOT NULL,
	request_limit BIGINT,
	search_mode VARCHAR(24) NOT NULL DEFAULT 'hourly',
	compacted_jobs BIGINT NOT NULL DEFAULT '0',
	request_count BIGINT NOT NULL,
	active_seconds FLOAT NOT NULL,
	active_since DATETIME,
	created_at DATETIME NOT NULL,
	finished_at DATETIME,
	last_error VARCHAR(200),
	PRIMARY KEY (id)
);


CREATE TABLE users (
	user_id VARCHAR(64) NOT NULL,
	screen_name TEXT,
	verified BIGINT,
	verified_type BIGINT,
	verified_reason TEXT,
	followers_count BIGINT,
	follow_count BIGINT,
	statuses_count BIGINT,
	user_description LONGTEXT,
	profile_retrieved_at VARCHAR(48),
	PRIMARY KEY (user_id)
);


CREATE TABLE comment_audits (
	task_id VARCHAR(36) NOT NULL,
	post_id VARCHAR(64) NOT NULL,
	displayed_comments_count BIGINT,
	retrieved_comments_count BIGINT,
	retrieved_first_level_count BIGINT,
	retrieved_reply_count BIGINT,
	first_level_exhausted BOOL,
	comments_complete BOOL,
	incomplete_reason JSON,
	updated_at DATETIME,
	PRIMARY KEY (task_id, post_id),
	FOREIGN KEY(task_id) REFERENCES tasks (id)
);


CREATE TABLE comment_threads (
	task_id VARCHAR(36) NOT NULL,
	root_id VARCHAR(64) NOT NULL,
	post_id VARCHAR(64) NOT NULL,
	expected_count BIGINT,
	exhausted BOOL,
	last_error VARCHAR(200),
	PRIMARY KEY (task_id, root_id),
	FOREIGN KEY(task_id) REFERENCES tasks (id)
);


CREATE TABLE job_receipts (
	task_id VARCHAR(36) NOT NULL,
	key_hash VARCHAR(64) NOT NULL,
	PRIMARY KEY (task_id, key_hash),
	FOREIGN KEY(task_id) REFERENCES tasks (id)
);


CREATE TABLE jobs (
	id VARCHAR(36) NOT NULL,
	task_id VARCHAR(36) NOT NULL,
	kind VARCHAR(24) NOT NULL,
	dedupe_key VARCHAR(240) NOT NULL,
	payload JSON NOT NULL,
	status VARCHAR(24) NOT NULL,
	priority INTEGER NOT NULL,
	attempts INTEGER NOT NULL,
	cookie_id VARCHAR(36),
	lease_owner VARCHAR(64),
	lease_until DATETIME,
	last_error VARCHAR(200),
	created_at DATETIME NOT NULL,
	finished_at DATETIME,
	PRIMARY KEY (id),
	CONSTRAINT uq_task_job UNIQUE (task_id, dedupe_key),
	FOREIGN KEY(task_id) REFERENCES tasks (id)
);
CREATE INDEX ix_jobs_lease ON jobs (lease_until);
CREATE INDEX ix_jobs_queue ON jobs (task_id, status, priority);

CREATE TABLE keyword_checks (
	task_id VARCHAR(36) NOT NULL,
	post_id VARCHAR(64) NOT NULL,
	query_id VARCHAR(64) NOT NULL,
	status VARCHAR(32) NOT NULL,
	missing_terms JSON,
	checked_at DATETIME,
	PRIMARY KEY (task_id, post_id, query_id),
	FOREIGN KEY(task_id) REFERENCES tasks (id)
);


CREATE TABLE search_windows (
	id VARCHAR(36) NOT NULL,
	task_id VARCHAR(36) NOT NULL,
	query_id VARCHAR(64) NOT NULL,
	parent_id VARCHAR(36),
	range_end DATETIME,
	start_at DATETIME NOT NULL,
	end_at DATETIME NOT NULL,
	status VARCHAR(24) NOT NULL,
	current_page INTEGER,
	updated_at DATETIME,
	PRIMARY KEY (id),
	FOREIGN KEY(task_id) REFERENCES tasks (id)
);
CREATE INDEX ix_windows_task ON search_windows (task_id, status);

CREATE TABLE task_comments (
	task_id VARCHAR(36) NOT NULL,
	comment_id VARCHAR(64) NOT NULL,
	created_at DATETIME,
	PRIMARY KEY (task_id, comment_id),
	FOREIGN KEY(task_id) REFERENCES tasks (id)
);


CREATE TABLE task_posts (
	task_id VARCHAR(36) NOT NULL,
	post_id VARCHAR(64) NOT NULL,
	created_at DATETIME,
	PRIMARY KEY (task_id, post_id),
	FOREIGN KEY(task_id) REFERENCES tasks (id)
);


CREATE TABLE job_attempts (
	id VARCHAR(36) NOT NULL,
	job_id VARCHAR(36) NOT NULL,
	task_id VARCHAR(36) NOT NULL,
	cookie_id VARCHAR(36) NOT NULL,
	checkpoint JSON NOT NULL,
	kind VARCHAR(24) NOT NULL,
	started_at DATETIME(6) NOT NULL,
	finished_at DATETIME(6),
	outcome VARCHAR(40) NOT NULL,
	http_status INTEGER,
	PRIMARY KEY (id),
	FOREIGN KEY(job_id) REFERENCES jobs (id)
);
CREATE INDEX ix_attempts_task ON job_attempts (task_id, started_at);
