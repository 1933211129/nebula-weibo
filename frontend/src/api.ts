export async function api<T>(
  path: string,
  method = "GET",
  body?: unknown,
): Promise<T> {
  const res = await fetch(import.meta.env.BASE_URL + "api" + path, {
    method,
    credentials: "same-origin",
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res
    .json()
    .catch(() => ({ detail: "服务响应异常，请稍后重试" }));
  if (!res.ok)
    throw new Error(
      typeof data.detail === "string"
        ? data.detail
        : "请检查输入格式及必填字段",
    );
  return data;
}
export type Task = {
  id: string;
  name: string;
  status: string;
  start_at: string;
  end_at: string;
  query_ids: string[];
  cookie_ids: string[];
  request_limit: number | null;
  search_mode: "hourly" | "adaptive";
  compacted_jobs: number;
  request_count: number;
  posts_count: number;
  comments_count: number;
  replies_count: number;
  pending_jobs: number;
  running_jobs: number;
  failed_jobs: number;
  limited_windows: number;
  repairable_windows: number;
  date_progress: number;
  total_query_hours: number;
  covered_query_hours: number;
  unqueued_query_hours: number;
  active_seconds: number;
  eta_seconds: number | null;
  covered_query_days: number;
  total_query_days: number;
  last_error: string | null;
};
export type CookieAccount = {
  id: string;
  name: string;
  status: string;
  request_interval: number;
  request_interval_min: number;
  request_interval_max: number;
  waiting_interval: boolean;
  failure_limit: number;
  consecutive_failures: number;
  request_count: number;
  last_checked_at: string | null;
  last_error: string | null;
  busy: boolean;
  current_job: { task_id: string; kind: string } | null;
};
export type Coverage = {
  month: string;
  total: number;
  done: number;
  limited: number;
  running: number;
  progress: number;
};
export type TaskDetailData = Partial<Task> & {
  keyword_verification?: Record<string, number>;
  keyword_checks?: {
    post_id: string;
    query: string;
    status: string;
    missing_terms: string[] | null;
  }[];
  coverage?: Coverage[];
  audits?: {
    post_id: string;
    displayed_comments_count: number | null;
    retrieved_comments_count: number;
    retrieved_reply_count: number;
    comments_complete: boolean;
    incomplete_reason: string[] | null;
  }[];
};
export type Log = {
  id: number;
  event: string;
  message: string;
  level: string;
  created_at: string;
  task_id: string | null;
  cookie_id: string | null;
  details: Record<string, unknown>;
};
export type Overview = {
  task_id: string | null;
  counts: Record<string, number>;
  tasks: Task[];
  active_cookies: number;
  running_workers: number;
  coverage: Coverage[];
  recent_logs: Log[];
  query_count: number;
};
export type Query = {
  query_id: string;
  category: string;
  query: string;
  terms: string[];
};
export type Requirements = {
  sections: { title: string; lines: string[] }[];
  queries: Query[];
  schema: Record<string, string[]>;
};
export const states: Record<string, string> = {
  pending: "待处理",
  done: "已遍历",
  limited: "达到页数限制",
  failed: "失败待重试",
  blocked: "中断待恢复",
  success: "成功",
  draft: "待启动",
  running: "采集中",
  paused: "已暂停",
  quota_paused: "额度暂停",
  needs_attention: "需要处理",
  completed: "已完成",
  partial: "完成 · 有缺口",
  cancelled: "已取消",
  active: "可用",
  unverified: "待验证",
  expired: "已失效",
  restricted: "访问受限",
  error_paused: "连续异常 · 已暂停",
  split: "已拆为小时",
  disabled: "已停用",
  normal: "正常",
  incomplete: "待补全",
  not_fetched: "待获取",
  outside_time_range: "范围外",
};
export const number = (value: number | undefined) =>
  new Intl.NumberFormat("zh-CN").format(value || 0);
export const duration = (seconds: number | null) =>
  seconds === null
    ? "样本不足"
    : seconds < 60
      ? "不足 1 分钟"
      : seconds < 3600
        ? `${Math.ceil(seconds / 60)} 分钟`
        : seconds < 86400
          ? `${(seconds / 3600).toFixed(1)} 小时`
          : `${(seconds / 86400).toFixed(1)} 天`;
