import { FileText, Search } from "lucide-react";
import { useEffect, useState } from "react";
import { api, Requirements } from "../api";

export function RequirementsPage() {
  const [data, setData] = useState<Requirements | null>(null),
    [tab, setTab] = useState(0),
    [error, setError] = useState(""),
    [search, setSearch] = useState("");
  useEffect(() => {
    api<Requirements>("/requirements")
      .then(setData)
      .catch((e) => setError(e.message));
  }, []);
  if (error) return <p className="error">{error}</p>;
  if (!data)
    return (
      <div className="skeleton">
        <div />
      </div>
    );
  return (
    <>
      <section className="panel requirements-content">
        <h2>可复用的采集能力</h2>
        <p>
          每个任务独立配置关键词、北京时间小时范围和 Cookie
          通道。搜索结果保存帖子正文及公开元数据，可选择继续采集一级评论与回复；同一条内容跨任务去重，各任务保留独立关联。
        </p>
        <p>
          任务支持暂停、失败重试和断点恢复。日期覆盖表示已遍历的搜索范围，不代表平台全部内容的召回率；公开字段缺失、分页限制和评论数量差异会如实记录。
        </p>
        <p>
          新建任务时可直接输入或粘贴关键词，无需修改配置文件。下方说明与词库为研究示例，不限制新任务的主题或日期。
        </p>
      </section>
      <details>
        <summary>查看检索示例与字段说明（可选参考）</summary>
        <div className="source-strip">
          <FileText size={20} />
          <span>
            <strong>采集说明与示例词库</strong>
            <small>
              公开采集说明 · {data.sections.length} 个需求部分 ·{" "}
              {data.queries.length} 个独立检索任务
            </small>
          </span>
        </div>
        <div className="tabs" role="tablist" aria-label="需求章节">
          {data.sections.map((s, i) => (
            <button
              role="tab"
              aria-selected={tab === i}
              key={s.title}
              onClick={() => setTab(i)}
            >
              {s.title.replace(/^\d+\.\s*/, "")}
            </button>
          ))}
        </div>
        <section className="panel requirements-content">
          <h2>{data.sections[tab]?.title.replace(/^\d+\.\s*/, "")}</h2>
          {data.sections[tab]?.lines.map((line, i) => (
            <p
              key={i}
              className={/^[A-Za-z_]+[：:]/.test(line) ? "field-line" : ""}
            >
              {/^[A-Za-z_]+[：:]/.test(line) ? (
                <>
                  <code>{line.split(/[：:]/)[0]}</code>
                  <span>{line.slice(line.search(/[：:]/) + 1)}</span>
                </>
              ) : (
                line
              )}
            </p>
          ))}
        </section>
        {tab === 1 && (
          <section className="panel query-catalog">
            <div className="section-title">
              <h2>展开后的检索任务</h2>
              <div className="search-field">
                <Search size={16} />
                <input
                  aria-label="筛选检索任务"
                  placeholder="筛选关键词"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                />
              </div>
            </div>
            <div className="catalog-grid">
              {data.queries
                .filter((q) =>
                  q.query.toLowerCase().includes(search.toLowerCase()),
                )
                .map((q) => (
                  <div key={q.query_id}>
                    <span>{q.query}</span>
                    <small>
                      {q.category === "remote_human_service"
                        ? "远程服务"
                        : "生成式 AI"}{" "}
                      · {q.terms.length > 1 ? "组合检索" : "直接检索"}
                    </small>
                  </div>
                ))}
            </div>
          </section>
        )}
      </details>
    </>
  );
}
