import { Check, Plus, Settings2, ShieldCheck, X } from "lucide-react";
import { FormEvent, useState } from "react";
import { CookieAccount, number } from "../api";

import { Badge, Empty } from "../components/Common";

export function CookiesPage({
  cookies,
  busy,
  act,
}: {
  cookies: CookieAccount[];
  busy: string;
  act: (p: string, b?: unknown, m?: string) => Promise<boolean>;
}) {
  const [form, setForm] = useState(false),
    [editing, setEditing] = useState(""),
    [name, setName] = useState(""),
    [value, setValue] = useState(""),
    [intervalMin, setIntervalMin] = useState("1.000"),
    [intervalMax, setIntervalMax] = useState("2.500"),
    [failureLimit, setFailureLimit] = useState("5");
  async function save(e: FormEvent) {
    e.preventDefault();
    if (
      await act(
        "/cookies" + (editing ? "/" + editing : ""),
        { name, value: value || undefined, request_interval_min: Number(intervalMin), request_interval_max: Number(intervalMax), failure_limit: Number(failureLimit) },
        editing ? "PUT" : "POST",
      )
    ) {
      setForm(false);
      setValue("");
      setName("");
      setEditing("");
    }
  }
  return (
    <>
      <div className="toolbar">
        <p>
          <strong>{cookies.length}</strong> 个已配置通道{" "}
          <span className="subtle">· 同一登录凭据自动去重</span>
        </p>
        <button
          className="secondary"
          onClick={() => {
            setEditing("");
            setName("");
            setValue("");
            setIntervalMin("1.000");
            setIntervalMax("2.500");
            setFailureLimit("5");
            setForm(!form);
          }}
        >
          <Plus size={16} />
          添加 Cookie
        </button>
      </div>
      {form && (
        <form className="panel configuration-form" onSubmit={save}>
          <div className="section-title">
            <h2>{editing ? "更新 Cookie" : "添加采集通道"}</h2>
            <button
              type="button"
              className="icon-button"
              aria-label="关闭编辑"
              onClick={() => setForm(false)}
            >
              <X size={18} />
            </button>
          </div>
          <div className="form-grid">
            <label>
              通道名称
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                required
                maxLength={100}
                placeholder="例如：研究账号 02"
              />
            </label>
            <label>
              最短随机间隔（秒）
              <input
                type="number"
                min="1"
                max="120"
                step="0.001"
                required
                value={intervalMin}
                onChange={(e) => setIntervalMin(e.target.value)}
              />
            </label>
            <label>最长随机间隔（秒）
              <input type="number" min={intervalMin || '1'} max="120" step="0.001" required value={intervalMax} onChange={e => setIntervalMax(e.target.value)} />
            </label>
          </div>
          <p className="helper">每次请求随机取区间内的三位小数秒数，默认 1.000–2.500 秒。同一 Cookie 不并发；失败退避及平台限流优先，随机间隔不保证免于限流。</p>
          <label>
            连续请求失败上限
            <input type="number" min="1" max="100" required value={failureLimit}
              onChange={(e) => setFailureLimit(e.target.value)} />
          </label>
          <p className="helper">默认连续 5 次网络或服务异常后暂停此 Cookie，成功一次即清零。明确登录失效或访问受限时立即停用，不等待累计次数。内容删除、字段解析错误不计入此阈值。</p>
          <label>
            Cookie 凭据
            <textarea
              value={value}
              onChange={(e) => setValue(e.target.value)}
              required={!editing}
              autoComplete="off"
              spellCheck={false}
              placeholder={
                editing
                  ? "留空保留原凭据，填入新值后需重新验证"
                  : "粘贴 Cookie 请求头，或浏览器导出的 JSON 数组"
              }
            />
          </label>
          <p className="helper">
            凭据加密写入
            MySQL，保存后不回显。新增或更新后，请点击“验证”确认可用。
          </p>
          <button className="primary" disabled={!!busy}>
            <Check size={16} />
            保存通道
          </button>
        </form>
      )}
      <section className="panel">
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>采集通道</th>
                <th>可用状态</th>
                <th>请求间隔</th>
                <th>累计请求</th>
                <th>最近验证</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {cookies.map((c) => (
                <tr key={c.id}>
                  <td>
                    <strong>{c.name}</strong>
                    <small>
                      {c.busy ? "正在采集" : c.last_error || "独立采集通道"}
                    </small>
                  </td>
                  <td>
                    <Badge status={c.status} />
                    <small>连续失败 {c.consecutive_failures ?? 0} / {c.failure_limit ?? 5}</small>
                  </td>
                  <td>{c.request_interval_min.toFixed(3)}–{c.request_interval_max.toFixed(3)} 秒</td>
                  <td>{number(c.request_count)}</td>
                  <td>
                    {c.last_checked_at
                      ? new Date(c.last_checked_at + "Z").toLocaleString(
                          "zh-CN",
                          { hour12: false, timeZone: "Asia/Shanghai" },
                        )
                      : "尚未验证"}
                  </td>
                  <td>
                    <div className="row-actions">
                      <button
                        className="small-button"
                        disabled={!!busy || c.busy}
                        onClick={() => act("/cookies/" + c.id + "/validate")}
                      >
                        <ShieldCheck size={14} />
                        {busy.includes(c.id) ? "处理中" : "验证"}
                      </button>
                      <button
                        className="icon-button"
                        aria-label={`编辑 ${c.name}`}
                        onClick={() => {
                          setEditing(c.id);
                          setName(c.name);
                          setIntervalMin(c.request_interval_min.toFixed(3));
                          setIntervalMax(c.request_interval_max.toFixed(3));
                          setFailureLimit(String(c.failure_limit ?? 5));
                          setValue("");
                          setForm(true);
                        }}
                      >
                        <Settings2 size={16} />
                      </button>
                      {c.status !== "disabled" && (
                        <button
                          className="small-button"
                          disabled={!!busy}
                          onClick={() => act("/cookies/" + c.id + "/disable")}
                        >
                          停用
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {!cookies.length && (
          <Empty title="添加你的第一个采集通道">
            每个有效 Cookie 可独立执行一个请求，多通道可并行采集。
          </Empty>
        )}
      </section>
      <div className="plain-note">
        <ShieldCheck size={20} />
        <p>
          任务不设总请求上限。异常 Cookie 单独暂停，其他可用通道继续；无可用通道时任务暂停并保留断点。更新凭据并验证后可恢复。
        </p>
      </div>
    </>
  );
}
