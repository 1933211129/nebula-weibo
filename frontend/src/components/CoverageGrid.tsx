import { X } from "lucide-react";
import { useState } from "react";
import { Coverage } from "../api";

export function CoverageGrid({ cells }: { cells: Coverage[] }) {
  const [month, setMonth] = useState("");
  const chosen = cells.find((c) => c.month === month);
  const years = [
    ...new Set(cells.map((c) => Number(c.month.slice(0, 4)))),
  ].sort((a, b) => a - b);
  if (!cells.length)
    return (
      <p className="helper">
        尚无日期覆盖。创建任务后，将按该任务的关键词和小时范围显示进度。
      </p>
    );
  return (
    <>
      <div className="coverage-scroll">
        <div className="coverage-grid">
          <span />
          {Array.from({ length: 12 }, (_, i) => (
            <span className="month-label" key={i}>
              {i + 1}月
            </span>
          ))}
          {years.map((year) => (
            <div className="year-row" key={year}>
              <span className="year-label">{year}</span>
              {Array.from({ length: 12 }, (_, i) => {
                const m = `${year}-${String(i + 1).padStart(2, "0")}`,
                  c = cells.find((x) => x.month === m),
                  out = !c;
                return (
                  <button
                    key={m}
                    disabled={out}
                    onClick={() => setMonth(m)}
                    aria-pressed={month === m}
                    aria-label={`${m} ${out ? "研究范围外" : c ? `覆盖 ${c.progress}%` : "尚无任务"}`}
                    className={`month-cell ${out ? "out" : c?.limited ? "limited" : c?.progress === 100 ? "complete" : c?.done ? "partial" : c?.running ? "working" : c ? "planned" : "unset"} ${month === m ? "selected" : ""}`}
                    title={m}
                  >
                    <span>
                      {c?.progress ? `${c.progress === 100 && !c.limited ? 100 : Math.min(c.progress, 99.99)}%` : ""}
                    </span>
                  </button>
                );
              })}
            </div>
          ))}
        </div>
      </div>
      <div className="coverage-legend">
        <span>
          <i className="unset" />
          无任务
        </span>
        <span>
          <i className="planned" />
          待采集
        </span>
        <span>
          <i className="working" />
          进行中
        </span>
        <span>
          <i className="complete" />
          已遍历
        </span>
        <span>
          <i className="limited" />
          有缺口
        </span>
      </div>
      {month && (
        <div className="month-inspector">
          <strong>{month}</strong>
          <span>
            {chosen
              ? `搜索覆盖 ${chosen.progress}% · 已覆盖 ${(chosen.done / 3600).toFixed(1)} / ${(chosen.total / 3600).toFixed(1)} 个检索词小时 · 受限 ${(chosen.limited / 3600).toFixed(1)} 个检索词小时（不代表正文和评论完整率）`
              : "这个月份尚未分配采集任务。"}
          </span>
          <button
            className="icon-button"
            aria-label="关闭月份详情"
            onClick={() => setMonth("")}
          >
            <X size={14} />
          </button>
        </div>
      )}
    </>
  );
}
