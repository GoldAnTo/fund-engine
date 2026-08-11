import { useEffect, useRef, useState } from "react";

import { researchOsApi, type FundDisclosureSyncDetail } from "../../app/researchOsApi";

function formatWhen(value: string | null): string {
  return value ? new Date(value).toLocaleString("zh-CN") : "尚未配置";
}

function frequencyLabel(value: "weekly" | "monthly"): string {
  return value === "weekly" ? "每周一 09:00" : "每月首日 09:00";
}

function summaryText(payload: Record<string, unknown>): string {
  const labels: Record<string, string> = {
    holding_rows_seen: "持仓行",
    matched_reports: "已匹配季报",
    pending_match_rows: "待匹配",
    pending_permission_rows: "待确认展示",
    holding_disclosures_written: "写入披露",
    holding_disclosures_skipped_duplicate: "跳过重复",
    invalid_rows: "无效行",
  };
  return Object.entries(payload)
    .filter(([key]) => key in labels)
    .map(([key, value]) => `${labels[key]} ${String(value)}`)
    .join(" · ");
}

function failureText(payload: Record<string, unknown>): string | null {
  const errorType = typeof payload.error_type === "string" ? payload.error_type : null;
  const error = typeof payload.error === "string" ? payload.error : null;
  if (!errorType && !error) return null;
  return [errorType ? `失败类型：${errorType}` : null, error ? `原因：${error}` : null]
    .filter(Boolean)
    .join("；");
}

function announceRunRefresh(): void {
  window.dispatchEvent(new Event("research-os-run-refresh"));
}

export function FundDisclosureSyncTask({
  caseId,
  scopeRevision,
  onCompleted,
}: {
  caseId: string;
  scopeRevision: number;
  onCompleted: () => void;
}) {
  const [detail, setDetail] = useState<FundDisclosureSyncDetail | null>(null);
  const [selectedCodes, setSelectedCodes] = useState<string[]>([]);
  const [manualCodes, setManualCodes] = useState("");
  const [frequency, setFrequency] = useState<"weekly" | "monthly">("monthly");
  const [reason, setReason] = useState("");
  const [state, setState] = useState<"loading" | "saving" | "running" | "idle">("loading");
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const reloadSequence = useRef(0);

  async function reload() {
    const sequence = ++reloadSequence.current;
    setError(null);
    try {
      const next = await researchOsApi.fundDisclosureSync(caseId);
      if (
        !Array.isArray(next.suggestions) ||
        !Array.isArray(next.config_history) ||
        !Array.isArray(next.runs)
      ) {
        throw new Error("fund disclosure sync response is incomplete");
      }
      if (sequence !== reloadSequence.current) return;
      setDetail(next);
      const configured = next.effective_config;
      setSelectedCodes(configured?.fund_codes ?? next.suggestions.map((item) => item.fund_code));
      setFrequency(configured?.frequency ?? "monthly");
    } catch {
      if (sequence !== reloadSequence.current) return;
      setError("暂时无法读取基金披露补充记录。不会以空白记录替代，请重试读取。");
    } finally {
      if (sequence === reloadSequence.current) setState("idle");
    }
  }

  useEffect(() => {
    setState("loading");
    void reload();
  }, [caseId, scopeRevision]);

  function toggleCode(code: string) {
    setSelectedCodes((current) => current.includes(code)
      ? current.filter((item) => item !== code)
      : [...current, code]);
  }

  function configuredCodes(): string[] {
    return Array.from(new Set([
      ...selectedCodes,
      ...manualCodes.split(/[\s,，]+/).map((item) => item.trim().toUpperCase()).filter(Boolean),
    ]));
  }

  async function save() {
    const fundCodes = configuredCodes();
    if (!fundCodes.length) {
      setError("请至少选择一只建议基金，或填写一个基金代码。");
      return;
    }
    if (!reason.trim()) {
      setError("请记录本次配置调整的原因，便于后续回放。");
      return;
    }
    setState("saving");
    setError(null);
    try {
      const config = await researchOsApi.saveFundDisclosureSync(caseId, {
        actor: "human:researcher",
        fund_codes: fundCodes,
        frequency,
        allow_display: true,
        change_reason: reason.trim(),
      });
      setNotice(`已保存配置版本 ${config.version}，后续运行将冻结这组基金与股票范围。`);
      await reload();
    } catch {
      setState("idle");
      setError("保存配置未完成。请检查基金代码和理由后重试，既有配置不会被覆盖。");
    }
  }

  async function start() {
    setState("running");
    setError(null);
    announceRunRefresh();
    try {
      const run = await researchOsApi.startFundDisclosureSync(caseId);
      // A just-created run is authoritative even when an earlier background
      // reload resolves late.  Researchers should see its frozen scope and
      // failure/success record immediately, never a blank task history.
      setDetail((current) => current
        ? { ...current, runs: [run, ...current.runs.filter((item) => item.id !== run.id)] }
        : current,
      );
      setNotice(run.status === "failed" ? "本次补充未完成，失败原因已写入运行记录。" : "已完成一次基金披露补充，结果已写入运行记录。");
      await reload();
      onCompleted();
    } catch {
      setState("idle");
      setError("无法创建本次补充记录。请先保存配置，或稍后重试。");
    } finally {
      announceRunRefresh();
    }
  }

  async function retry(runId: string) {
    setState("running");
    setError(null);
    announceRunRefresh();
    try {
      const run = await researchOsApi.retryFundDisclosureSync(caseId, runId);
      setDetail((current) => current
        ? { ...current, runs: [run, ...current.runs.filter((item) => item.id !== run.id)] }
        : current,
      );
      setNotice("已按失败运行当时冻结的范围发起重试。");
      await reload();
      onCompleted();
    } catch {
      setState("idle");
      setError("重试未能创建。原失败记录仍保留，可稍后再次操作。");
    } finally {
      announceRunRefresh();
    }
  }

  if (state === "loading" && !detail) {
    return <section className="ros-fund-sync" aria-label="基金披露补充加载中">正在读取基金披露补充范围与运行记录…</section>;
  }
  if (error && !detail) {
    return <section className="ros-fund-sync" role="alert"><p>{error}</p><button className="ros-button ros-button--secondary" type="button" onClick={() => { setState("loading"); void reload(); }}>重试读取基金披露补充</button></section>;
  }
  if (!detail) return null;
  const isBusy = state === "saving" || state === "running";
  const saveReady = configuredCodes().length > 0 && Boolean(reason.trim());
  const saveRequirement = !configuredCodes().length
    ? "选择至少一只建议基金或填写基金代码"
    : !reason.trim()
      ? "说明配置调整理由"
      : null;

  return (
    <section className="ros-fund-sync" aria-labelledby="fund-sync-heading">
      <p className="ros-eyebrow">历史披露补充 · 可配置任务</p>
      <h3 id="fund-sync-heading">建议补充的基金披露</h3>
      <p>先列出与当前已审核股票相关的历史披露基金。每次补充只按同基金、同报告期季报写入，不表示实时持仓。</p>

      {detail.suggestions.length ? (
        <fieldset className="ros-fund-sync__suggestions">
          <legend>与当前股票相关的建议基金</legend>
          {detail.suggestions.map((fund) => (
            <label key={fund.fund_code}>
              <input aria-label={`建议基金：${fund.fund_name}（${fund.fund_code}）`} type="checkbox" checked={selectedCodes.includes(fund.fund_code)} onChange={() => toggleCode(fund.fund_code)} />
              <span>建议基金：{fund.fund_name}（{fund.fund_code}）</span>
              <small>命中 {fund.matching_stock_codes.join("、")} · 最近报告期 {fund.latest_report_period}</small>
            </label>
          ))}
        </fieldset>
      ) : (
        <p className="ros-note">当前还没有能从历史披露推得的建议基金。可直接填写基金代码，不会调用基金筛选或假设推荐。</p>
      )}

      <div className="ros-fund-sync__config">
        <label>手动补充基金代码<textarea value={manualCodes} onChange={(event) => setManualCodes(event.target.value)} placeholder="例如 005827, 110011" /></label>
        <label>补充频率<select value={frequency} onChange={(event) => setFrequency(event.target.value as "weekly" | "monthly")}><option value="weekly">每周一 09:00</option><option value="monthly">每月首日 09:00</option></select></label>
        <label>配置调整理由<textarea aria-label="配置调整理由" value={reason} onChange={(event) => setReason(event.target.value)} placeholder="说明为何增减基金或调整周期" /></label>
        <div className="ros-fund-sync__actions"><button className="ros-button ros-button--secondary" type="button" disabled={isBusy || !saveReady} onClick={() => void save()}>{state === "saving" ? "正在保存配置…" : "保存基金披露配置"}</button><button className="ros-button ros-button--primary" type="button" disabled={isBusy || !detail.effective_config} onClick={() => void start()}>{state === "running" ? "正在补充…" : "立即补充一次"}</button></div>
        {saveRequirement && <p className="ros-note">保存前还需：{saveRequirement}</p>}
      </div>
      {detail.effective_config && <p className="ros-note">当前为版本 {detail.effective_config.version} · 下次定时：{formatWhen(detail.next_scheduled_at)} · 冻结股票：{detail.effective_config.stock_codes.join("、") || "当前未绑定股票"}</p>}
      {notice && <p className="ros-success" role="status">{notice}</p>}
      {error && <p className="ros-error" role="alert">{error}</p>}

      <section className="ros-fund-sync__config-history" aria-live="polite">
        <h4>配置版本与调整理由</h4>
        <p>保存不会覆盖旧配置；每次补充与重试都会保留当时冻结的范围。</p>
        {detail.config_history.length ? (
          <ol>
            {detail.config_history.slice(0, 3).map((config) => (
              <li key={config.id}>
                <header>
                  <strong>版本 {config.version} · {frequencyLabel(config.frequency)}</strong>
                  <small>{formatWhen(config.created_at)}</small>
                </header>
                <p>{config.change_reason}</p>
                <small>基金：{config.fund_codes.join("、")} · 冻结股票：{config.stock_codes.join("、") || "未绑定"} · 配置人：{config.changed_by}</small>
              </li>
            ))}
          </ol>
        ) : <p className="ros-note">尚未保存配置。选择基金、频率并说明理由后，会在这里追加首个版本。</p>}
      </section>

      <section className="ros-fund-sync__history" aria-live="polite">
        <h4>本次补充记录</h4>
        {detail.runs.length ? detail.runs.slice(0, 3).map((run) => (
          <article key={run.id}>
            <header><strong>{run.trigger === "scheduled" ? "定时补充" : run.trigger === "retry" ? "失败重试" : "立即补充"}</strong><span className={`ros-pill ros-pill--${run.status === "failed" ? "risk" : "system"}`}>{run.status === "failed" ? "失败" : run.status === "queued" ? "等待执行" : "已完成"}</span></header>
            <small>基金 {run.fund_codes.join("、")} · 股票 {run.stock_codes.join("、") || "未绑定"} · {new Date(run.created_at).toLocaleString("zh-CN")}</small>
            {run.events.filter((event) => event.stage === "provider_capability").map((event) => {
              const payload = event.payload as Record<string, unknown>;
              const tools = Array.isArray(payload.used_tools) ? payload.used_tools.join("、") : "未确认";
              const fields = Array.isArray(payload.required_fields) ? payload.required_fields.join("、") : "未确认";
              return <section className="ros-fund-sync__capability" key={`${event.seq}-capability`}><strong>本次数据能力与字段</strong><p>供应商：{String(payload.provider || "未确认")} · 已使用：{tools}</p><p>必需字段：{fields}</p><small>未验证能力不会被当作基金持仓、实时仓位或推荐：{Array.isArray(payload.unverified_capabilities) ? payload.unverified_capabilities.join("、") : "未确认"}</small></section>;
            })}
            <ol>{run.events.map((event) => <li key={event.seq}><b>{event.stage}</b><span>{event.message}</span>{summaryText(event.payload) && <small>{summaryText(event.payload)}</small>}{failureText(event.payload) && <small>{failureText(event.payload)}</small>}</li>)}</ol>
            {run.status === "failed" && <button className="ros-button ros-button--secondary" type="button" disabled={isBusy} onClick={() => void retry(run.id)}>按冻结范围重试</button>}
          </article>
        )) : <p className="ros-note">还没有补充记录。保存配置后可立即执行，或由周/月任务在对应时间运行。</p>}
      </section>
    </section>
  );
}
