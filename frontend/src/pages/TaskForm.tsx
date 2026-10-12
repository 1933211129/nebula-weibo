import { Plus, Search } from "lucide-react";
import { FormEvent, useEffect, useState } from "react";
import { api, CookieAccount, Query, states } from "../api";

export function TaskForm({
  cookies,
  onDone,
}: {
  cookies: CookieAccount[];
  onDone: () => void;
}) {
  const [queries, setQueries] = useState<Query[]>([]),
    [selected, setSelected] = useState<string[]>([]),
    [ids, setIds] = useState<string[]>(
      cookies.filter((c) => c.status === "active").map((c) => c.id),
    ),
    [name, setName] = useState(""),
    [start, setStart] = useState(""),
    [end, setEnd] = useState(""),
    [text, setText] = useState(""),
    [mode, setMode] = useState("words"),
    [left, setLeft] = useState(""),
    [right, setRight] = useState(""),
    [search, setSearch] = useState(""),
    [comments, setComments] = useState(true),
    [searchMode, setSearchMode] = useState('adaptive'),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const keywords = [
    ...new Set(
      text
        .replace(/^\s*[·•●▪◦‣]+\s*/gm, "")
        .split(mode === "words" ? /[,，、;；\s]+/u : /[,，、;；\r\n]+/u)
        .map((word) => word.trim().replace(/\s+/gu, " "))
        .filter(Boolean),
    ),
  ];
  const splitGroup = (value: string) => [
    ...new Set(
      value
        .replace(/^\s*[·•●▪◦‣]+\s*/gm, "")
        .split(/[,，、;；\s]+/u)
        .filter(Boolean),
    ),
  ];
  const leftWords = splitGroup(left),
    rightWords = splitGroup(right);
  const pairCount = leftWords.length * rightWords.length;
  const combinations =
    pairCount <= 1000
      ? [
          ...new Set(
            leftWords.flatMap((a) =>
              rightWords.map((b) => (a === b ? a : `${a} ${b}`)),
            ),
          ),
        ]
      : [];
  const keywordCount = new Set([
    ...keywords,
    ...combinations,
    ...queries.filter((q) => selected.includes(q.query_id)).map((q) => q.query),
  ]).size;
  const hours =
    start && end
      ? (Date.parse(end + "+08:00") - Date.parse(start + "+08:00")) / 3600000
      : 0;
  const invalid =
    pairCount > 1000 ||
    Boolean(leftWords.length) !== Boolean(rightWords.length) ||
    keywordCount > 1000 ||
    [...keywords, ...combinations].some((word) => [...word].length > 200);
  useEffect(() => {
    api<Query[]>("/queries")
      .then(setQueries)
      .catch((e) => setError(e.message));
  }, []);
  async function submit(e: FormEvent) {
    e.preventDefault();
    setError("");
    if (!(hours > 0) || !Number.isInteger(hours)) {
      setError("请选择整点起止时间，截止时间须晚于开始时间。");
      return;
    }
    setBusy(true);
    try {
      await api("/tasks", "POST", {
        name,
        start_at: start,
        end_at: end,
        query_ids: selected,
        keywords_text: text,
        keyword_mode: mode,
        combination_left: left,
        combination_right: right,
        cookie_ids: ids,
        include_comments: comments,
        search_mode: searchMode,
      });
      onDone();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <form onSubmit={submit} className="task-create">
      <section className="panel configuration-form">
        <h2>任务范围</h2>
        <label>
          任务名称
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="例如：城市公共交通讨论"
            required
            maxLength={160}
          />
        </label>
        <div className="form-grid">
          <label>
            开始时间（北京时间）
            <input
              type="datetime-local"
              step="3600"
              value={start}
              onInput={(e) => setStart(e.currentTarget.value)}
              onChange={(e) => setStart(e.target.value)}
              required
            />
          </label>
          <label>
            截止时间（不包含此时刻）
            <input
              type="datetime-local"
              step="3600"
              min={start}
              value={end}
              onInput={(e) => setEnd(e.currentTarget.value)}
              onChange={(e) => setEnd(e.target.value)}
              required
            />
          </label>
        </div>
        <p className="helper">
          按北京时间选择范围，截止时刻不包含在范围内。
          {hours > 0 && Number.isInteger(hours)
            ? `当前范围共 ${hours.toLocaleString()} 小时。`
            : "请选择本次任务的时间范围。"}
        </p>
        <label className="check-label">
          <input
            type="checkbox"
            checked={comments}
            onChange={(e) => setComments(e.target.checked)}
          />
          同时采集全部公开可见的一级评论与回复
        </label>
        <h2>
          检索词 <span className="heading-count">共 {keywordCount} 个</span>
        </h2>
        <label>
          输入或粘贴关键词
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={6}
            maxLength={250000}
            placeholder={
              "· 远程心理健康、远程心理服务、数字心理健康\n· 互联网心理服务、网络心理服务、线上心理服务"
            }
          />
        </label>
        <p className="helper">
          支持中英文逗号、顿号、分号、空格和换行；自动清除行首圆点、空项和重复词。每个词独立检索，不自动生成组合。
        </p>
        <label className="check-label">
          <input
            type="checkbox"
            checked={mode === "phrases"}
            onChange={(e) => setMode(e.target.checked ? "phrases" : "words")}
          />
          保留词内空格（组合检索时使用）
        </label>
        <div className="keyword-preview" aria-live="polite">
          <p className="helper">
            拆分预览 · {keywords.length} 个词（每个任务最多 1000 个，单词最多
            200 字符）
          </p>
          {keywords.slice(0, 100).map((word) => (
            <span className="keyword-token" key={word}>
              {word}
            </span>
          ))}
          {keywords.length > 100 && (
            <p className="helper">
              另有 {keywords.length - 100} 个词，创建时一并加入。
            </p>
          )}
          {(keywords.length > 1000 ||
            keywords.some((word) => [...word].length > 200)) && (
            <p className="error">
              关键词数量或长度超出限制，请分批创建或调整拆分方式。
            </p>
          )}
        </div>
        <h2>
          两组词交叉组合 <span className="heading-count">可选</span>
        </h2>
        <p className="helper">
          A 组每个词与 B
          组每个词配对。例如“互联网医院”与“精神科”组成一个双词检索。可与上方普通关键词同时采集。
        </p>
        <div className="form-grid">
          <label>
            A 组关键词
            <textarea
              rows={4}
              value={left}
              onChange={(e) => setLeft(e.target.value)}
              maxLength={250000}
              placeholder="互联网医院、在线问诊、视频问诊"
            />
          </label>
          <label>
            B 组关键词
            <textarea
              rows={4}
              value={right}
              onChange={(e) => setRight(e.target.value)}
              maxLength={250000}
              placeholder="精神科、心理医生、抑郁"
            />
          </label>
        </div>
        <p className="helper">
          两组均支持逗号、顿号、分号、空格与换行拆分。{leftWords.length} ×{" "}
          {rightWords.length} = {pairCount}{" "}
          个配对；重复检索项自动合并，相同词配对退化为单词检索。
        </p>
        <div className="keyword-preview" aria-live="polite">
          {combinations.slice(0, 30).map((q) => (
            <span className="keyword-token" key={q}>
              {q}
            </span>
          ))}
          {combinations.length > 30 && (
            <p className="helper">
              另有 {combinations.length - 30} 个组合，创建时一并加入。
            </p>
          )}
        </div>
        <p className="helper">
          组合须在完整帖子正文中同时命中所有词才纳入任务；忽略英文大小写与全半角，不扩展同义词。长文补全后核验，不使用评论、用户名或被转发原文代替正文。核验详情在任务中查看。
        </p>
        {invalid && (
          <p className="error" role="alert">
            {Boolean(leftWords.length) !== Boolean(rightWords.length)
              ? "请同时填写 A 组与 B 组关键词，或清空两组只采集普通词。"
              : "配对及任务总检索项均不得超过 1000，单项最多 200 字符，请减少词数或分批创建。"}
          </p>
        )}
        <details>
          <summary>从已有词库补充（可选，已选 {selected.length} 个）</summary>
          <div className="query-tools">
            <div className="search-field">
              <Search size={16} />
              <input
                aria-label="搜索检索词"
                placeholder="搜索已有关键词"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </div>
            <button
              type="button"
              className="small-button"
              onClick={() => setSelected(queries.map((q) => q.query_id))}
            >
              全选
            </button>
            <button
              type="button"
              className="small-button"
              onClick={() => setSelected([])}
            >
              清空
            </button>
          </div>
          <div className="query-groups">
            {[
              ["remote_human_service", "远程心理健康服务"],
              ["generative_ai_service", "生成式 AI 心理健康服务"],
            ].map(([group, label]) => (
              <button
                key={group}
                type="button"
                className="secondary"
                onClick={() =>
                  setSelected([
                    ...new Set([
                      ...selected,
                      ...queries
                        .filter((q) => q.category === group)
                        .map((q) => q.query_id),
                    ]),
                  ])
                }
              >
                选择{label}
              </button>
            ))}
          </div>
          <div className="query-picker">
            {queries
              .filter((q) =>
                q.query.toLowerCase().includes(search.toLowerCase()),
              )
              .map((q) => (
                <label
                  className={selected.includes(q.query_id) ? "chosen" : ""}
                  key={q.query_id}
                >
                  <input
                    type="checkbox"
                    checked={selected.includes(q.query_id)}
                    onChange={() =>
                      setSelected(
                        selected.includes(q.query_id)
                          ? selected.filter((x) => x !== q.query_id)
                          : [...selected, q.query_id],
                      )
                    }
                  />
                  <span>{q.query}</span>
                  <small>{q.terms.length > 1 ? "组合" : "直接"}</small>
                </label>
              ))}
          </div>
        </details>
      </section>
      <aside className="panel create-settings">
        <h2>执行设置</h2>
        <h3>分配 Cookie 通道</h3>
        {cookies.length ? (
          cookies.map((c) => (
            <label className="check-label channel-option" key={c.id}>
              <input
                type="checkbox"
                checked={ids.includes(c.id)}
                onChange={() =>
                  setIds(
                    ids.includes(c.id)
                      ? ids.filter((x) => x !== c.id)
                      : [...ids, c.id],
                  )
                }
              />
              <span>
                {c.name}
                <small>{states[c.status]}</small>
              </span>
            </label>
          ))
        ) : (
          <p className="helper">
            尚未添加 Cookie。可以先创建任务，再配置通道。
          </p>
        )}
        <label>检索策略
          <select value={searchMode} onChange={e => setSearchMode(e.target.value)}>
            <option value="adaptive">按天探测，触顶拆小时（推荐）</option>
            <option value="hourly">始终逐小时检索</option>
          </select>
        </label>
        <p className="helper">先读取当天首页的结果条数：不足 500 条按天采集，达到 500 条拆为小时。未显示条数时参考分页；仍无法判断则拆小时。微博搜索可见性限制仍会影响召回。</p>
        <p className="helper">任务不设总请求上限。Cookie 按配置的随机间隔独立执行；异常通道暂停，断点保留。</p>
        <div className="create-summary">
          <span>
            检索词<strong>{keywordCount} 个</strong>
          </span>
          <span>
            计划检索词小时
            <strong>
              {hours > 0 ? (hours * keywordCount).toLocaleString() : "—"}
            </strong>
          </span>
          <span>
            分配通道<strong>{ids.length} 个</strong>
          </span>
          <span>
            首次状态<strong>待启动</strong>
          </span>
        </div>
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        <button className="primary" disabled={busy || !keywordCount || invalid}>
          <Plus size={17} />
          {busy ? "正在创建…" : "创建任务"}
        </button>
        <p className="helper">创建不会自动发起全量采集。</p>
      </aside>
    </form>
  );
}
