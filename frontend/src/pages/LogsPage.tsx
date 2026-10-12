import { Activity, Check } from "lucide-react";
import { useEffect, useState } from "react";
import { api, Log, Task } from "../api";

import { Empty } from "../components/Common";

export function LogList({ rows }: { rows: Log[] }) {
  return rows.length ? (
    <div className="log-list">
      {rows.map((l) => (
        <div key={l.id} className="log-row">
          <span
            className={`log-mark ${l.level === "warning" ? "warning" : ""}`}
          >
            {l.level === "warning" ? (
              <Activity size={14} />
            ) : (
              <Check size={14} />
            )}
          </span>
          <div>
            <p>{l.message}</p>
            <small>
              {l.event}
              {l.task_id && ` · 任务 ${l.task_id.slice(0, 8)}`}
            </small>
          </div>
          <time>
            {new Date(l.created_at + "Z").toLocaleString("zh-CN", {
              hour12: false,
              timeZone: "Asia/Shanghai",
            })}
          </time>
        </div>
      ))}
    </div>
  ) : (
    <Empty title="尚无运行记录">
      创建任务或验证 Cookie 后，这里会显示操作与采集事件。
    </Empty>
  );
}

export function LogsPage({ tasks }: { tasks: Task[] }) {
  const [rows, setRows] = useState<Log[]>([]),
    [task, setTask] = useState(""),
    [error, setError] = useState("");
  useEffect(() => {
    const load = () =>
      api<Log[]>("/logs" + (task ? "?task_id=" + task : ""))
        .then(setRows)
        .catch((e) => setError(e.message));
    load();
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, [task]);
  return (
    <section className="panel">
      <div className="section-title">
        <h2>最近 150 条事件</h2>
        <select
          value={task}
          aria-label="筛选日志任务"
          onChange={(e) => setTask(e.target.value)}
        >
          <option value="">所有任务与通道</option>
          {tasks.map((t) => (
            <option key={t.id} value={t.id}>
              {t.name}
            </option>
          ))}
        </select>
      </div>
      {error && <p className="error">{error}</p>}
      <LogList rows={rows} />
    </section>
  );
}
