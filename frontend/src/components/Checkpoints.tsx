import { useEffect, useState } from "react";
import { api, CookieAccount, states } from "../api";

type Row = {
  id: string;
  status?: string;
  outcome?: string;
  query?: string;
  start_at?: string;
  end_at?: string;
  current_page?: number;
  kind?: string;
  attempts?: number;
  last_error?: string;
  cookie_id?: string;
  started_at?: string;
  http_status?: number;
  payload?: Record<string, unknown>;
  checkpoint?: Record<string, unknown>;
};
type Result = { rows: Row[]; total: number };
const kinds: Record<string, string> = {
  search: "关键词搜索",
  post: "帖子详情",
  longtext: "长文补全",
  longtext_check: "长文完整性复核",
  comments: "一级评论",
  replies: "楼中楼回复",
};
const failures: Record<string, string> = {
  login_expired: "登录失效",
  access_restricted: "访问受限",
  rate_limited: "频率限制",
  network_error: "网络异常",
  lease_expired: "执行超时",
  longtext_unconfirmed: "微博未提供可确认的完整正文",
  missing_comment_cursor: "评论分页缺少结束标记与游标",
  stalled_comment_cursor: "评论分页游标重复，无法继续",
  test_harness_interrupted: "测试中断",
};
function statusLabel(value: string) {
  return failures[value] || states[value] || value;
}

export function Checkpoints({
  taskId,
  cookies,
}: {
  taskId: string;
  cookies: CookieAccount[];
}) {
  const [kind, setKind] = useState("windows"),
    [status, setStatus] = useState(""),
    [page, setPage] = useState(1),
    [data, setData] = useState<Result>({ rows: [], total: 0 }),
    [error, setError] = useState(""),
    [loading, setLoading] = useState(true);
  useEffect(() => {
    setPage(1);
    setStatus("");
  }, [taskId, kind]);
  useEffect(() => {
    let gone = false;
    async function load() {
      try {
        const result = await api<Result>(
          `/tasks/${taskId}/checkpoints?kind=${kind}&status=${status}&page=${page}`,
        );
        if (!gone) {
          setData(result);
          setError("");
        }
      } catch (e) {
        if (!gone) setError((e as Error).message);
      } finally {
        if (!gone) setLoading(false);
      }
    }
    setLoading(true);
    load();
    const timer = setInterval(load, 5000);
    return () => {
      gone = true;
      clearInterval(timer);
    };
  }, [taskId, kind, status, page]);
  return (
    <div className="checkpoint-panel">
      <h3>小时任务与恢复断点</h3>
      <p className="helper">
        已保存的分页位置不会因暂停或失效而清空。更新并验证 Cookie
        后，在任务中选择“重试异常”或“继续”。未生成的后续小时会随着采集自动加入队列。
      </p>
      <div className="tabs">
        {Object.entries({
          windows: "检索时间窗口",
          jobs: "处理队列",
          attempts: "异常与在途请求",
        }).map(([key, label]) => (
          <button
            key={key}
            type="button"
            className={kind === key ? "active" : ""}
            onClick={() => setKind(key)}
          >
            {label}
          </button>
        ))}
      </div>
      <div className="toolbar">
        <span className="subtle">
          {loading ? "正在读取…" : `${data.total} 条记录`}
        </span>
        <select
          aria-label="筛选断点状态"
          value={status}
          onChange={(e) => {
            setStatus(e.target.value);
            setPage(1);
          }}
        >
          <option value="">全部状态</option>
          {(kind === "attempts"
            ? ["done", "login_expired", "running", "network_error"]
            : kind === "windows"
              ? ["pending", "running", "done", "limited"]
              : ["pending", "running", "done", "failed", "blocked"]
          ).map((s) => (
            <option key={s} value={s}>
              {statusLabel(s)}
            </option>
          ))}
        </select>
      </div>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              {kind === "windows" ? (
                <>
                  <th>检索词</th>
                  <th>北京时间范围（截止前）</th>
                  <th>已保存页码</th>
                  <th>状态</th>
                </>
              ) : (
                <>
                  <th>数据类型 / 状态</th>
                  <th>通道</th>
                  <th>
                    {kind === "attempts"
                      ? "发起时间 / HTTP 状态"
                      : "尝试次数 / 中断原因"}
                  </th>
                  <th>已保存断点</th>
                </>
              )}
            </tr>
          </thead>
          <tbody>
            {data.rows.map((r) => (
              <tr key={r.id}>
                {kind === "windows" ? (
                  <>
                    <td>{r.query}</td>
                    <td>
                      {r.start_at?.replace("T", " ").slice(0, 16)}
                      <small>
                        至 {r.end_at?.replace("T", " ").slice(0, 16)}
                      </small>
                    </td>
                    <td>{r.current_page || "尚未取得"}</td>
                    <td>
                      <span className={`badge state-${r.status}`}>
                        {statusLabel(r.status || "")}
                      </span>
                    </td>
                  </>
                ) : (
                  <>
                    <td>
                      {kinds[r.kind || ""] || r.kind}
                      <small>{statusLabel(r.outcome || r.status || "")}</small>
                    </td>
                    <td>
                      {cookies.find((c) => c.id === r.cookie_id)?.name ||
                        "待分配"}
                    </td>
                    <td>
                      {kind === "attempts" ? (
                        <>
                          {r.started_at
                            ? new Date(r.started_at + "Z").toLocaleString(
                                "zh-CN",
                                { hour12: false, timeZone: "Asia/Shanghai" },
                              )
                            : "—"}
                          <small>HTTP {r.http_status ?? "无响应记录"}</small>
                        </>
                      ) : (
                        <>
                          {r.attempts} 次
                          <small>
                            {r.last_error
                              ? statusLabel(r.last_error)
                              : "无异常"}
                          </small>
                        </>
                      )}
                    </td>
                    <td>
                      <details>
                        <summary>查看分页位置</summary>
                        <pre>
                          {JSON.stringify(r.checkpoint || r.payload, null, 2)}
                        </pre>
                      </details>
                    </td>
                  </>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!loading && !data.rows.length && (
        <p className="helper">
          没有符合筛选条件的记录。新任务启动后会生成小时窗口。
        </p>
      )}
      <div className="pagination">
        <span>
          第 {page} / {Math.max(1, Math.ceil(data.total / 30))} 页
        </span>
        <button
          type="button"
          className="small-button"
          disabled={page === 1 || loading}
          onClick={() => setPage(page - 1)}
        >
          上一页
        </button>
        <button
          type="button"
          className="small-button"
          disabled={page * 30 >= data.total || loading}
          onClick={() => setPage(page + 1)}
        >
          下一页
        </button>
      </div>
    </div>
  );
}
