import type { TaskDetailData } from "../api";
import { ChevronRight, Pause, Play, Square, X } from "lucide-react";
import { useEffect, useState } from "react";
import { CookieAccount, duration, number, Task } from "../api";
import { Checkpoints } from "../components/Checkpoints";

import { Badge, Empty } from "../components/Common";
import { CoverageGrid } from "../components/CoverageGrid";

export function TaskTable({
  tasks,
  busy,
  act,
  inspect,
}: {
  tasks: Task[];
  busy: string;
  act: (path: string) => Promise<boolean>;
  inspect: (t: Task) => void;
}) {
  if (!tasks.length)
    return (
      <Empty title="还没有采集任务">
        创建任务，选择检索词、日期和 Cookie 后开始采集。
      </Empty>
    );
  return (
    <div className="table-scroll">
      <table className="task-table">
        <caption className="helper">继续采集只恢复未完成请求；补抓异常与缺口会重试失败断点，不重跑已完成数据。</caption>
        <thead>
          <tr>
            <th>任务 / 研究范围</th>
            <th>状态</th>
            <th>日期检索覆盖</th>
            <th className="num">帖子 / 评论</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          {tasks.map((t) => (
            <tr key={t.id}>
              <td>
                <button className="task-name" onClick={() => inspect(t)}>
                  {t.name}
                </button>
                <small>
                  {t.start_at.replace("T", " ").slice(0, 16)} —{" "}
                  {t.end_at.replace("T", " ").slice(0, 16)}
                  <span>（截止前） · {t.query_ids.length} 个词</span>
                </small>
              </td>
              <td>
                <Badge status={t.status} />
              </td>
              <td>
                <div className="progress-line">
                  <div className="progress-track">
                    <div style={{ width: `${t.date_progress}%` }} />
                  </div>
                  <span>{t.date_progress}%</span>
                </div>
                <small>
                  {number(t.pending_jobs)} 项待处理
                  {t.failed_jobs > 0 && ` · ${t.failed_jobs} 项异常`}
                </small>
              </td>
              <td className="num">
                <strong>{number(t.posts_count)}</strong>
                <span className="subtle"> / {number(t.comments_count)}</span>
              </td>
              <td>
                <div className="row-actions">
                  {!["completed", "cancelled", "partial"].includes(
                    t.status,
                  ) && (
                    <button
                      disabled={!!busy}
                      className="small-button"
                      onClick={() =>
                        act(
                          `/tasks/${t.id}/${t.status === "running" ? "pause" : t.status === "draft" ? "start" : "resume"}`,
                        )
                      }
                    >
                      {t.status === "running" ? (
                        <Pause size={13} />
                      ) : (
                        <Play size={13} />
                      )}{" "}
                      {t.status === "running"
                        ? "暂停"
                        : t.status === "draft"
                            ? "启动"
                            : "继续采集"}
                    </button>
                  )}
                  {!["running", "completed", "cancelled"].includes(t.status) && (t.failed_jobs > 0 || t.repairable_windows > 0) && (
                    <button
                      className="small-button"
                      disabled={!!busy}
                      onClick={() => act(`/tasks/${t.id}/retry`)}
                    >
                    补抓异常与缺口
                    </button>
                  )}
                  <button
                    className="icon-button"
                    aria-label={`查看 ${t.name}`}
                    onClick={() => inspect(t)}
                  >
                    <ChevronRight size={17} />
                  </button>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function TaskDetail({
  task,
  detail,
  cookies,
  busy,
  act,
  close,
}: {
  task: Task;
  detail: TaskDetailData;
  cookies: CookieAccount[];
  busy: string;
  act: (p: string, b?: unknown, m?: string) => Promise<boolean>;
  close: () => void;
}) {
  const [ids, setIds] = useState(task?.cookie_ids || []);
  const [searchMode, setSearchMode] = useState(task.search_mode);
  useEffect(() => {
    setIds(task?.cookie_ids || []);
    setSearchMode(task.search_mode);
  }, [task?.id]);
  useEffect(() => {
    document.getElementById("task-detail")?.scrollIntoView({ block: "start" });
  }, [task?.id]);
  if (!task) return null;
  return (
    <section className="panel detail-panel" id="task-detail" tabIndex={-1}>
      <div className="section-title">
        <div>
          <h2>{task.name}</h2>
          <p>搜索日期覆盖与内容处理分别核对。</p>
        </div>
        <button
          className="icon-button"
          aria-label="关闭任务详情"
          onClick={close}
        >
          <X size={18} />
        </button>
      </div>
      {task.last_error && (
        <div className="alert" role="status">
          中断原因：{task.last_error}
          。请检查异常记录和 Cookie 状态，修复后重试。
        </div>
      )}
      <p className="helper">
        已遍历 {number(task.covered_query_hours)} /{" "}
        {number(task.total_query_hours)} 个检索词小时，后续待生成{" "}
        {number(task.unqueued_query_hours)} 个小时窗口。
      </p>
      <div className="detail-stats">
        <span>
          累计请求<strong>{number(task.request_count)}</strong>
        </span>
        <span>
          运行时间<strong>{duration(task.active_seconds)}</strong>
        </span>
        <span>
          预计剩余（粗估）<strong>{duration(task.eta_seconds)}</strong>
        </span>
        <span>
          评论回复<strong>{number(task.replies_count)}</strong>
        </span>
      </div>
      <p className="helper">
        预估依据已遍历的日期窗口与有效运行时间；检索词热度不同、评论积压与访问限制会使估计变化。
      </p>
      {detail.coverage && <CoverageGrid cells={detail.coverage} />}
      <Checkpoints taskId={task.id} cookies={cookies} />
      <h3>组合命中核验</h3>
      <p className="helper">
        已通过 {detail.keyword_verification?.verified || 0} · 未命中{" "}
        {detail.keyword_verification?.rejected || 0} · 待核验{" "}
        {detail.keyword_verification?.pending || 0} · 时间范围外{" "}
        {detail.keyword_verification?.outside_time_range || 0}
        。按帖子与检索项计数；下方最多展示 100 条。
      </p>
      {!!detail.keyword_checks?.length && (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>帖子 ID</th>
                <th>组合</th>
                <th>核验结果</th>
                <th>缺少词</th>
              </tr>
            </thead>
            <tbody>
              {detail.keyword_checks.map((r) => (
                <tr key={`${r.post_id}-${r.query}`}>
                  <td>{r.post_id}</td>
                  <td>{r.query}</td>
                  <td>
                    {{
                      verified: "同时命中",
                      rejected: "未同时命中",
                      pending: "等待完整正文",
                      outside_time_range: "时间范围外",
                    }[r.status] || r.status}
                  </td>
                  <td>{r.missing_terms?.join("、") || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div className="task-settings">
        <p className="helper">已压缩 {number(task.compacted_jobs || 0)} 项完成作业。有效数据、去重凭据及失败断点保留；队列表仅展示尚未压缩的记录。</p>
        <h3>采集通道</h3>
        <label>检索策略
          <select value={searchMode} disabled={task.status === 'running' || task.search_mode === 'adaptive'} onChange={e => setSearchMode(e.target.value as 'hourly' | 'adaptive')}>
            <option value="hourly">始终逐小时检索</option>
            <option value="adaptive">按天探测，触顶拆小时</option>
          </select>
        </label>
        <p className="helper">切换到按天探测只重排尚未开始的窗口，保留数据和异常断点。当天不足 500 条按天采集，达到 500 条拆小时；缺少条数时参考分页，仍无法判断则拆小时。</p>
        <div className="checkbox-row">
          {cookies.map((c) => (
            <label key={c.id}>
              <input
                type="checkbox"
                checked={ids.includes(c.id)}
                disabled={task.status === "running"}
                onChange={() =>
                  setIds(
                    ids.includes(c.id)
                      ? ids.filter((x) => x !== c.id)
                      : [...ids, c.id],
                  )
                }
              />
              {c.name}
            </label>
          ))}
        </div>
        <div className="inline-controls">
          <button
            className="secondary"
            disabled={!!busy || task.status === "running"}
            onClick={() =>
              act(
                "/tasks/" + task.id,
                {
                  cookie_ids: ids,
                  search_mode: searchMode,
                },
                "PATCH",
              )
            }
          >
            保存配置
          </button>
          {!["completed", "cancelled"].includes(task.status) && (
            <button
              className="danger-button"
              disabled={!!busy}
              onClick={() => {
                if (confirm("取消此任务？已采集的数据会保留。"))
                  act("/tasks/" + task.id + "/cancel");
              }}
            >
              <Square size={13} />
              取消任务
            </button>
          )}
        </div>
        {task.status === "running" && (
          <p className="helper">暂停任务后可调整采集通道。</p>
        )}
      </div>
      {!!detail.audits?.length && (
        <div className="audit-section">
          <h3>评论完整性核对</h3>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>帖子 ID</th>
                  <th>显示总数</th>
                  <th>实采 / 回复</th>
                  <th>核对</th>
                </tr>
              </thead>
              <tbody>
                {detail.audits?.map((a) => (
                  <tr key={a.post_id}>
                    <td>{a.post_id}</td>
                    <td>{a.displayed_comments_count ?? "未公开"}</td>
                    <td>
                      {a.retrieved_comments_count} / {a.retrieved_reply_count}
                    </td>
                    <td>
                      {a.comments_complete
                        ? "数量一致"
                        : (a.incomplete_reason || [])
                            .map(
                              (x: string) =>
                                (
                                  ({
                                    first_level_pending: "一级评论未结束",
                                    replies_pending: "回复未结束",
                                    reply_count_mismatch: "回复数不一致",
                                    displayed_count_mismatch: "总数不一致",
                                    pending: "待处理",
                                  }) as Record<string, string>
                                )[x] || x,
                            )
                            .join("、")}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </section>
  );
}
