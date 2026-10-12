import { ArrowDownToLine, ArrowRight, Search } from "lucide-react";
import { useEffect, useState } from "react";
import { api, number, Task } from "../api";

import { Badge, Empty } from "../components/Common";

export function DataPage({ tasks }: { tasks: Task[] }) {
  const [table, setTable] = useState("posts"),
    [task, setTask] = useState(""),
    [search, setSearch] = useState(""),
    [page, setPage] = useState(1),
    [data, setData] = useState<{ total: number; rows: Record<string, any>[] }>({
      total: 0,
      rows: [],
    }),
    [error, setError] = useState(""),
    [loading, setLoading] = useState(false);
  useEffect(() => {
    setLoading(true);
    let gone = false;
    const t = setTimeout(
      () =>
        api<any>(
          `/records/${table}?page=${page}&task_id=${task}&q=${encodeURIComponent(search)}`,
        )
          .then((d) => {
            if (!gone) {
              setData(d);
              setError("");
            }
          })
          .catch((e) => !gone && setError(e.message))
          .finally(() => !gone && setLoading(false)),
      200,
    );
    return () => {
      gone = true;
      clearTimeout(t);
    };
  }, [table, task, search, page]);
  const labels: Record<string, string> = {
    posts: "微博帖子",
    comments: "评论与回复",
    users: "公开账号",
    post_query_matches: "关键词命中",
  };
  return (
    <>
      <div className="tabs">
        {Object.entries(labels).map(([key, label]) => (
          <button
            key={key}
            className={table === key ? "active" : ""}
            onClick={() => {
              setTable(key);
              setPage(1);
            }}
          >
            {label}
          </button>
        ))}
      </div>
      <div className="toolbar">
        <div className="filter-tools">
          <div className="search-field">
            <Search size={16} />
            <input
              aria-label="搜索数据"
              placeholder="搜索正文或名称"
              value={search}
              onChange={(e) => {
                setSearch(e.target.value);
                setPage(1);
              }}
            />
          </div>
          <select
            aria-label="按任务筛选数据"
            value={task}
            onChange={(e) => {
              setTask(e.target.value);
              setPage(1);
            }}
          >
            <option value="">全部任务</option>
            {tasks.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
          </select>
        </div>
        <a
          className="secondary button-link"
          href={`${import.meta.env.BASE_URL}api/export/${table}?${new URLSearchParams({ task_id: task, q: search })}`}
        >
          <ArrowDownToLine size={16} />
          导出筛选结果
        </a>
      </div>
      <p className="helper">
        导出格式为
        JSONL，包含当前筛选下的全部页。浏览列表保留待补全及范围外记录；总览的帖子数仅统计完整正文。
      </p>
      {error && <p className="error">{error}</p>}
      <section className="panel">
        <div className="section-title">
          <h2>{labels[table]}</h2>
          <span className="subtle">
            {loading ? "加载中…" : `${number(data.total)} 条记录`}
          </span>
        </div>
        {data.rows.length ? (
          <div className="data-list">
            {data.rows.map((r, i) => (
              <article className="data-record" key={i}>
                <div className="record-heading">
                  <span>{r.comment_id || r.post_id || r.user_id}</span>
                  {r.retrieval_status && <Badge status={r.retrieval_status} />}
                  <time>{r.created_at?.replace("T", " ").slice(0, 19)}</time>
                </div>
                <p>
                  {r.post_text ||
                    r.comment_text ||
                    r.screen_name ||
                    r.matched_keyword ||
                    "尚未取得正文"}
                </p>
                <div className="record-meta">
                  {r.query_id && (
                    <span>
                      检索任务：
                      {Array.isArray(r.query_id)
                        ? r.query_id.length
                        : r.query_id}
                    </span>
                  )}
                  {r.comment_level && (
                    <span>
                      {r.comment_level === 1 ? "一级评论" : "评论回复"}
                    </span>
                  )}
                  {r.comments_count !== undefined && (
                    <span>
                      评论 {r.comments_count ?? "未公开"} · 点赞{" "}
                      {r.likes_count ?? "未公开"}
                    </span>
                  )}
                  {r.post_url && (
                    <a href={r.post_url} target="_blank" rel="noreferrer">
                      查看原帖
                      <ArrowRight size={13} />
                    </a>
                  )}
                </div>
                <details>
                  <summary>查看完整字段</summary>
                  <pre>{JSON.stringify(r, null, 2)}</pre>
                </details>
              </article>
            ))}
          </div>
        ) : (
          <Empty title={loading ? "正在读取数据" : "还没有符合条件的数据"}>
            任务运行后，真实采集记录会出现在这里。
          </Empty>
        )}
        <div className="pagination">
          <span>
            第 {page} / {Math.max(1, Math.ceil(data.total / 30))} 页
          </span>
          <button
            className="small-button"
            disabled={page === 1}
            onClick={() => setPage(page - 1)}
          >
            上一页
          </button>
          <button
            className="small-button"
            disabled={page * 30 >= data.total}
            onClick={() => setPage(page + 1)}
          >
            下一页
          </button>
        </div>
      </section>
    </>
  );
}
