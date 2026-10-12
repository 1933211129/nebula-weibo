import type { TaskDetailData } from "./api";
import {
  Activity,
  ArrowRight,
  CalendarDays,
  ChevronRight,
  ClipboardList,
  Database,
  KeyRound,
  Layers3,
  LayoutDashboard,
  Menu,
  Plus,
  ShieldCheck,
  X,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, CookieAccount, number, Overview, states } from "./api";
import { CoverageGrid } from "./components/CoverageGrid";
import { TaskDetail, TaskTable } from "./components/TaskViews";
import { CookiesPage } from "./pages/CookiesPage";
import { DataPage } from "./pages/DataPage";
import { LogList, LogsPage } from "./pages/LogsPage";
import { RequirementsPage } from "./pages/RequirementsPage";
import { TaskForm } from "./pages/TaskForm";

type Page =
  | "overview"
  | "tasks"
  | "cookies"
  | "requirements"
  | "data"
  | "logs"
  | "create";
const navs = [
  { id: "overview", label: "采集总览", icon: LayoutDashboard },
  { id: "tasks", label: "任务管理", icon: Layers3 },
  { id: "cookies", label: "Cookie 通道", icon: KeyRound },
  { id: "requirements", label: "采集说明与参考", icon: ClipboardList },
  { id: "data", label: "数据浏览", icon: Database },
  { id: "logs", label: "运行日志", icon: Activity },
] as const;
const emptyOverview: Overview = {
  task_id: null,
  counts: {},
  tasks: [],
  active_cookies: 0,
  running_workers: 0,
  coverage: [],
  recent_logs: [],
  query_count: 0,
};

export default function App() {
  const [page, setPage] = useState<Page>("overview"),
    [mobile, setMobile] = useState(false);
  const [narrow, setNarrow] = useState(
    () => window.matchMedia("(max-width: 760px)").matches,
  );
  const sidebarRef = useRef<HTMLElement>(null);
  const menuRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const media = window.matchMedia("(max-width: 760px)");
    const change = () => {
      setNarrow(media.matches);
      if (!media.matches) setMobile(false);
    };
    media.addEventListener("change", change);
    return () => media.removeEventListener("change", change);
  }, []);
  useEffect(() => {
    if (!narrow || !mobile) return;
    const drawer = sidebarRef.current;
    const controls = () =>
      Array.from(
        drawer?.querySelectorAll<HTMLElement>(
          "a[href], button:not([disabled])",
        ) || [],
      );
    controls()[0]?.focus();
    const oldOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const keydown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        setMobile(false);
        return;
      }
      if (event.key !== "Tab") return;
      const items = controls(),
        first = items[0],
        last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last?.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first?.focus();
      }
    };
    document.addEventListener("keydown", keydown);
    return () => {
      document.body.style.overflow = oldOverflow;
      document.removeEventListener("keydown", keydown);
      menuRef.current?.focus();
    };
  }, [narrow, mobile]);
  const [overview, setOverview] = useState<Overview>(emptyOverview),
    [cookies, setCookies] = useState<CookieAccount[]>([]),
    [loading, setLoading] = useState(true),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [busy, setBusy] = useState("");
  const [selected, setSelected] = useState(""),
    [detail, setDetail] = useState<TaskDetailData | null>(null);
  const [overviewChoice, setOverviewChoice] = useState("");
  const [totals, setTotals] = useState<Record<string, number>>({});
  const refreshVersion = useRef(0);
  const refreshInFlight = useRef<{ choice: string; version: number } | null>(
    null,
  );
  const refresh = useCallback(async () => {
    if (refreshInFlight.current?.choice === overviewChoice) return;
    const version = ++refreshVersion.current;
    refreshInFlight.current = { choice: overviewChoice, version };
    try {
      const [o, c, total] = await Promise.all([
        api<Overview>(
          "/overview" +
            (overviewChoice
              ? `?task_id=${encodeURIComponent(overviewChoice)}`
              : ""),
        ),
        api<CookieAccount[]>("/cookies"),
        api<Record<string, number>>("/totals"),
      ]);
      if (version !== refreshVersion.current) return;
      setOverview(o);
      setCookies(c);
      setTotals(total);
      setError("");
    } catch (e) {
      if (version === refreshVersion.current) setError((e as Error).message);
    } finally {
      if (refreshInFlight.current?.version === version)
        refreshInFlight.current = null;
      if (version === refreshVersion.current) setLoading(false);
    }
  }, [overviewChoice]);
  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 5000);
    return () => {
      clearInterval(t);
      ++refreshVersion.current;
      refreshInFlight.current = null;
    };
  }, [refresh]);
  useEffect(() => {
    if (!selected) {
      return;
    }
    let cancelled = false;
    const load = () =>
      api<TaskDetailData>("/tasks/" + selected)
        .then((d) => {
          if (!cancelled) {
            if (page === "tasks") setDetail(d);
          }
        })
        .catch((e) => setError(e.message));
    load();
    const t = setInterval(load, 5000);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [selected, page]);
  useEffect(() => {
    if (!notice) return;
    const t = setTimeout(() => setNotice(""), 6000);
    return () => clearTimeout(t);
  }, [notice]);
  async function act(path: string, body?: unknown, method = "POST") {
    setBusy(path);
    try {
      await api(path, method, body);
      setNotice("操作已保存");
      await refresh();
      return true;
    } catch (e) {
      setNotice((e as Error).message);
      return false;
    } finally {
      setBusy("");
    }
  }
  function navigate(next: Page) {
    setPage(next);
    setMobile(false);
    setDetail(null);
    setSelected("");
  }
  const title =
    page === "create"
      ? "新建采集任务"
      : navs.find((x) => x.id === page)?.label || "";
  const currentTask = overview.tasks.find((t) => t.id === overview.task_id);
  const taskCookies = cookies.filter((c) =>
    currentTask?.cookie_ids.includes(c.id),
  );
  return (
    <div className="app-shell">
      <a href="#main" className="skip-link">
        跳到主要内容
      </a>
      {mobile && (
        <button
          className="nav-scrim"
          aria-label="关闭导航"
          tabIndex={-1}
          onClick={() => setMobile(false)}
        />
      )}
      <aside
        ref={sidebarRef}
        id="primary-navigation"
        className={`sidebar ${mobile ? "open" : ""}`}
        inert={narrow && !mobile}
        aria-hidden={narrow && !mobile ? true : undefined}
        role={narrow && mobile ? "dialog" : undefined}
        aria-modal={narrow && mobile ? true : undefined}
        aria-label="工作台导航"
      >
        {narrow && (
          <button
            className="drawer-close"
            aria-label="收起导航"
            onClick={() => setMobile(false)}
          >
            <X size={18} />
          </button>
        )}
        <a
          className="brand"
          href="#overview"
          onClick={() => navigate("overview")}
        >
          <span className="brand-symbol">
            <Layers3 size={23} />
          </span>
          <span>
            微博研究台<small>研究数据采集工作台</small>
          </span>
        </a>
        <nav aria-label="主导航">
          {navs.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              onClick={() => navigate(id)}
              className={page === id ? "active" : ""}
              aria-current={page === id ? "page" : undefined}
            >
              <Icon size={18} />
              {label}
              {id === "cookies" && (
                <span className="nav-count">{cookies.length}</span>
              )}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="connection">
            <Database size={17} />
            <span>
              MySQL 数据存储
              <small>{error ? "连接需要检查" : "已连接 · 独立研究库"}</small>
            </span>
            <i className={error ? "warn-dot" : "live-dot"} />
          </div>
        </div>
      </aside>
      <div className="workspace" inert={narrow && mobile}>
        <header className="topbar">
          <div>
            <button
              ref={menuRef}
              className="icon-button menu-button"
              aria-label="打开导航"
              aria-expanded={mobile}
              aria-controls="primary-navigation"
              onClick={() => setMobile(true)}
            >
              <Menu size={20} />
            </button>
            <span>自定义关键词 · 小时级采集</span>
            <ChevronRight size={14} />
            <strong>{title}</strong>
          </div>
          <div>
            <span className="sync-status">
              <i className="live-dot" />每 5 秒更新
            </span>
          </div>
        </header>
        <main id="main">
          <div className="page-heading">
            <div>
              <h1>{title}</h1>
              <p>
                {
                  {
                    overview:
                      "聚焦一个任务，查看它的数据、日期覆盖与采集状态。",
                    tasks: "分配检索范围与采集通道，追踪每一个任务。",
                    cookies: "每个独立 Cookie 对应一个并发采集通道。",
                    requirements: "了解采集能力与字段，原研究文档仅作参考。",
                    data: "浏览真实采集记录，检查关联与完整性。",
                    logs: "记录任务状态变化、失败与重要异常。",
                    create: "配置研究范围和通道，创建后由你决定何时启动。",
                  }[page]
                }
              </p>
            </div>
            {page !== "create" && (
              <button className="primary" onClick={() => navigate("create")}>
                <Plus size={17} />
                新建采集任务
              </button>
            )}
          </div>
          {error && (
            <div className="alert" role="alert">
              {error}
              <button onClick={refresh}>重试连接</button>
            </div>
          )}
          {loading ? (
            <div className="skeleton" aria-label="正在加载数据">
              <div />
              <div />
              <div />
            </div>
          ) : (
            <>
              {page === "overview" && (
                <>
                  <section className="global-totals" aria-label="全库数据总量">
                    <div>
                      <h2>全库数据总量</h2>
                      <p>
                        数据库实际条数，按 ID
                        去重，含所有记录状态；不随任务选择变化。
                      </p>
                    </div>
                    <dl>
                      <div>
                        <dt>帖子总数</dt>
                        <dd>{number(totals.posts)}</dd>
                      </div>
                      <div>
                        <dt>用户总数</dt>
                        <dd>{number(totals.users)}</dd>
                      </div>
                      <div>
                        <dt>评论与回复总数</dt>
                        <dd>{number(totals.comments)}</dd>
                        <small>其中回复 {number(totals.replies)} 条</small>
                      </div>
                    </dl>
                  </section>
                  <section className="overview-scope" aria-label="当前统计任务">
                    <div>
                      <h2>{currentTask?.name || "尚无采集任务"}</h2>
                      <p>
                        {currentTask
                          ? `${states[currentTask.status] || currentTask.status} · ${currentTask.start_at.replace("T", " ").slice(0, 16)} — ${currentTask.end_at.replace("T", " ").slice(0, 16)}（截止前，北京时间）`
                          : "新建任务后，这里会显示该任务的统计。"}
                      </p>
                      <p>
                        {overviewChoice
                          ? "已固定所选任务，不随自动刷新切换。"
                          : overview.tasks.some((t) => t.status === "running")
                            ? "自动关注最新创建的采集中任务，可手动切换其他任务。"
                            : "当前没有采集中任务，显示最近创建的任务。"}
                      </p>
                    </div>
                    <label>
                      查看任务
                      <select
                        aria-label="查看任务"
                        value={overviewChoice}
                        disabled={!overview.tasks.length}
                        onChange={(e) => {
                          ++refreshVersion.current;
                          setOverviewChoice(e.target.value);
                          setOverview((o) => ({
                            ...emptyOverview,
                            tasks: o.tasks,
                          }));
                          setLoading(true);
                        }}
                      >
                        <option value="">自动关注采集中任务</option>
                        {overview.tasks.map((t) => (
                          <option key={t.id} value={t.id}>
                            {t.name} · {states[t.status] || t.status}
                          </option>
                        ))}
                      </select>
                    </label>
                  </section>
                  <div className="stat-ledger">
                    {[
                      [
                        "任务帖子",
                        overview.counts.posts,
                        "含待补全正文，不含未命中候选",
                      ],
                      [
                        "任务用户",
                        overview.counts.users,
                        "本任务作者与评论者按 ID 去重",
                      ],
                      [
                        "评论与回复",
                        overview.counts.comments,
                        `${number(overview.counts.replies)} 条回复`,
                      ],
                      [
                        "待处理请求",
                        currentTask?.pending_jobs || 0,
                        `${number(currentTask?.failed_jobs)} 项异常`,
                      ],
                      [
                        "任务可用通道",
                        overview.active_cookies,
                        `${overview.running_workers} 个请求正在执行`,
                      ],
                    ].map(([label, value, note]) => (
                      <div key={label}>
                        <span>{label}</span>
                        <strong>{number(Number(value))}</strong>
                        <small>{note}</small>
                      </div>
                    ))}
                  </div>
                  <div className="overview-grid">
                    <section className="panel coverage-panel">
                      <div className="section-title">
                        <div>
                          <h2>任务日期覆盖</h2>
                          <p>
                            {overview.tasks.length
                              ? "仅统计当前任务 · 不合并其他任务的重叠日期"
                              : "尚未设置采集范围"}
                          </p>
                        </div>
                      </div>
                      <CoverageGrid
                        key={overview.task_id}
                        cells={overview.coverage}
                      />
                      <div className="coverage-note">
                        <CalendarDays size={15} />
                        <span>
                          按「检索词 ×
                          日期时长」加权；绿色表示搜索窗口已遍历，不等于评论已全部取得。
                        </span>
                      </div>
                    </section>
                    <section className="panel lanes">
                      <div className="section-title">
                        <h2>任务采集通道</h2>
                        <span className="subtle">{taskCookies.length} 个</span>
                      </div>
                      {taskCookies.length ? (
                        taskCookies.map((c, i) => (
                          <div className="lane" key={c.id}>
                            <span className="lane-number">
                              {String(i + 1).padStart(2, "0")}
                            </span>
                            <div>
                              <strong>{c.name}</strong>
                              <span>
                                {c.busy
                                  ? c.current_job?.task_id === currentTask?.id
                                    ? "正在处理本任务"
                                    : "正在处理其他任务"
                                  : c.status === "active" && c.waiting_interval
                                    ? "请求间隔等待"
                                    : c.status === "active"
                                      ? "就绪 · 等待任务"
                                      : states[c.status]}
                              </span>
                            </div>
                            <i
                              className={
                                c.busy || c.status === "active"
                                  ? "live-dot"
                                  : "warn-dot"
                              }
                            />
                          </div>
                        ))
                      ) : (
                        <p className="lane-empty">
                          当前任务尚未分配 Cookie 通道。
                        </p>
                      )}
                      <button
                        className="text-button"
                        onClick={() => navigate("cookies")}
                      >
                        管理 Cookie 通道
                        <ArrowRight size={15} />
                      </button>
                      <div className="lane-foot">
                        <ShieldCheck size={17} />
                        <p>
                          凭据加密保存
                          <br />
                          <span>同一 Cookie 不重复并发</span>
                        </p>
                      </div>
                    </section>
                  </div>
                  <section className="panel">
                    <div className="section-title">
                      <h2>
                        当前任务{" "}
                        <span className="heading-count">
                          {currentTask ? 1 : 0}
                        </span>
                      </h2>
                      <button
                        className="text-button"
                        onClick={() => navigate("tasks")}
                      >
                        查看全部
                        <ArrowRight size={15} />
                      </button>
                    </div>
                    <TaskTable
                      tasks={currentTask ? [currentTask] : []}
                      busy={busy}
                      act={act}
                      inspect={(t) => {
                        navigate("tasks");
                        setSelected(t.id);
                        setDetail(t);
                      }}
                    />
                  </section>
                  <section className="activity-preview">
                    <div className="section-title">
                      <h2>最近动态</h2>
                      <button
                        className="text-button"
                        onClick={() => navigate("logs")}
                      >
                        完整日志
                        <ArrowRight size={15} />
                      </button>
                    </div>
                    <LogList rows={overview.recent_logs.slice(0, 4)} />
                  </section>
                </>
              )}
              {page === "tasks" && (
                <>
                  <section className="panel">
                    <TaskTable
                      tasks={overview.tasks}
                      busy={busy}
                      act={act}
                      inspect={(t) => {
                        setSelected(t.id);
                        setDetail(t);
                      }}
                    />
                  </section>
                  {detail && (
                    <TaskDetail
                      task={overview.tasks.find((t) => t.id === selected)!}
                      detail={detail}
                      cookies={cookies}
                      busy={busy}
                      act={act}
                      close={() => {
                        setDetail(null);
                        setSelected("");
                      }}
                    />
                  )}
                </>
              )}
              {page === "cookies" && (
                <CookiesPage cookies={cookies} busy={busy} act={act} />
              )}
              {page === "create" && (
                <TaskForm
                  cookies={cookies}
                  onDone={async () => {
                    await refresh();
                    navigate("tasks");
                  }}
                />
              )}
              {page === "requirements" && <RequirementsPage />}
              {page === "data" && <DataPage tasks={overview.tasks} />}
              {page === "logs" && <LogsPage tasks={overview.tasks} />}
            </>
          )}
          <footer>
            微博公开内容研究采集
            <span>北京时间 UTC+8 · 缺口保留，不以估算替代实采</span>
          </footer>
        </main>
      </div>
      {notice && (
        <div className="toast" role="status">
          {notice}
          <button
            className="icon-button"
            aria-label="关闭提示"
            onClick={() => setNotice("")}
          >
            <X size={16} />
          </button>
        </div>
      )}
    </div>
  );
}
