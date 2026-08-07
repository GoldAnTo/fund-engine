import { useState } from "react";
import { researchClient } from "../../data/researchClient";

interface EventFactorScopeEditorProps {
  caseId: string;
  factors: string[];
  onClose: () => void;
  onSaved: () => Promise<void>;
}

const minimumFactors = 3;
const maximumFactors = 5;

export function EventFactorScopeEditor({ caseId, factors: initialFactors, onClose, onSaved }: EventFactorScopeEditorProps) {
  const [factors, setFactors] = useState(() => initialFactors.slice(0, maximumFactors));
  const [error, setError] = useState<string | null>(null);
  const [isSaving, setIsSaving] = useState(false);

  function replaceFactor(index: number, value: string) {
    setFactors((current) => current.map((factor, factorIndex) => factorIndex === index ? value : factor));
  }

  function moveFactor(index: number, direction: -1 | 1) {
    const destination = index + direction;
    if (destination < 0 || destination >= factors.length) return;
    setFactors((current) => {
      const next = [...current];
      [next[index], next[destination]] = [next[destination], next[index]];
      return next;
    });
  }

  async function save(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const normalizedFactors = factors.map((factor) => factor.trim());
    if (normalizedFactors.length < minimumFactors || normalizedFactors.length > maximumFactors || normalizedFactors.some((factor) => !factor)) {
      setError("请填写全部 3 至 5 个因素。");
      return;
    }

    setError(null);
    setIsSaving(true);
    try {
      await researchClient.updateEventResearchScope({ caseId, factors: normalizedFactors, changedBy: "研究员" });
      await onSaved();
      onClose();
    } catch (reason) {
      setError(`保存失败：${reason instanceof Error ? reason.message : "请稍后重试"}`);
    } finally {
      setIsSaving(false);
    }
  }

  return <section className="event-factor-editor" role="dialog" aria-modal="true" aria-labelledby="event-factor-editor-title">
    <div className="prototype-paper event-factor-editor__panel">
      <div className="event-factor-editor__heading">
        <div><p className="section-kicker">研究范围</p><h2 id="event-factor-editor-title">编辑研究因素</h2></div>
        <button className="prototype-button quiet" type="button" onClick={onClose} disabled={isSaving}>取消</button>
      </div>
      <p className="event-factor-editor__notice">已核验证据会保留并重新归类。</p>
      <form className="prototype-form" onSubmit={save}>
        <div className="event-factor-editor__list">
          {factors.map((factor, index) => <div className="event-factor-editor__row" key={index}>
            <label htmlFor={`event-factor-${index}`}>因素 {index + 1}<input id={`event-factor-${index}`} type="text" value={factor} onChange={(event) => replaceFactor(index, event.target.value)} disabled={isSaving} /></label>
            <div className="event-factor-editor__row-actions" aria-label={`调整因素 ${index + 1}`}>
              <button className="prototype-button quiet" type="button" aria-label={`上移因素 ${index + 1}`} onClick={() => moveFactor(index, -1)} disabled={isSaving || index === 0}>上移</button>
              <button className="prototype-button quiet" type="button" aria-label={`下移因素 ${index + 1}`} onClick={() => moveFactor(index, 1)} disabled={isSaving || index === factors.length - 1}>下移</button>
              <button className="prototype-button quiet" type="button" onClick={() => setFactors((current) => current.filter((_, factorIndex) => factorIndex !== index))} disabled={isSaving || factors.length <= minimumFactors}>删除</button>
            </div>
          </div>)}
        </div>
        <button className="prototype-button" type="button" onClick={() => setFactors((current) => current.length < maximumFactors ? [...current, ""] : current)} disabled={isSaving || factors.length >= maximumFactors}>添加因素</button>
        {error ? <p className="form-error" role="alert">{error}</p> : null}
        <div className="event-factor-editor__footer"><button className="prototype-button primary" type="submit" disabled={isSaving}>{isSaving ? "正在保存…" : "保存因素并继续自动研究"}</button></div>
      </form>
    </div>
  </section>;
}
