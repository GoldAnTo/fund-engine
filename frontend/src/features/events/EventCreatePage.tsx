import {
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type FormEvent,
} from "react";
import { useNavigate } from "react-router-dom";

import { researchClient } from "../../data/researchClient";

export const AUTOMATIC_RESEARCH_ENTRY_COLORS = {
  secondary: { lightness: .52, chroma: .018, hue: 65 },
  fieldBorder: { lightness: .59, chroma: .018, hue: 75 },
} as const;

const automaticResearchEntryStyle = {
  "--automatic-entry-secondary": toCssOklch(AUTOMATIC_RESEARCH_ENTRY_COLORS.secondary),
  "--automatic-entry-field-border": toCssOklch(AUTOMATIC_RESEARCH_ENTRY_COLORS.fieldBorder),
} as CSSProperties;

function toCssOklch(color: { lightness: number; chroma: number; hue: number }) {
  return `oklch(${color.lightness} ${color.chroma} ${color.hue})`;
}

export function EventCreatePage() {
  const navigate = useNavigate();
  const errorRef = useRef<HTMLParagraphElement>(null);
  const mountedRef = useRef(false);
  const requestTokenRef = useRef(0);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const canSubmit = Boolean(input.trim()) && !busy;

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      requestTokenRef.current += 1;
    };
  }, []);

  useEffect(() => {
    if (error && !busy) errorRef.current?.focus();
  }, [busy, error]);

  async function startResearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const researchInput = input.trim();
    if (!researchInput || busy) return;

    const requestToken = ++requestTokenRef.current;
    setBusy(true);
    setError(null);
    try {
      const started = await researchClient.startAutomaticResearch(researchInput);
      if (!mountedRef.current || requestToken !== requestTokenRef.current) return;
      navigate(`/events/${encodeURIComponent(started.caseId)}/automatic-research`);
    } catch {
      if (!mountedRef.current || requestToken !== requestTokenRef.current) return;
      setError("自动研究未能启动，请稍后重试。");
    } finally {
      if (mountedRef.current && requestToken === requestTokenRef.current) {
        setBusy(false);
      }
    }
  }

  return (
    <main className="ros-page automatic-research-entry" style={automaticResearchEntryStyle}>
      <header className="automatic-research-entry__heading">
        <p className="ros-eyebrow">自动研究</p>
        <h1 id="automatic-research-heading">告诉系统你想研究什么</h1>
        <p>
          输入一个研究主题，也可以直接粘贴公告、研报或其他原始材料。系统会自动采集资料、分析证据并生成结论。
        </p>
      </header>

      <form
        className="automatic-research-entry__form"
        onSubmit={startResearch}
        aria-labelledby="automatic-research-heading"
        aria-busy={busy}
      >
        <label htmlFor="automatic-research-input">研究主题或材料</label>
        <textarea
          id="automatic-research-input"
          value={input}
          onChange={(event) => setInput(event.target.value)}
          placeholder="例如：英伟达新产品会如何影响供应链？也可以直接粘贴公告、研报或原始材料。"
          rows={7}
          disabled={busy}
          aria-invalid={false}
          aria-describedby="automatic-research-help"
        />
        <p id="automatic-research-help" className="automatic-research-entry__help">
          写清楚关注的事件、公司或影响，或直接粘贴公告、研报或其他原始材料。系统会自动完成其余步骤。
        </p>
        {error && (
          <p
            id="automatic-research-error"
            ref={errorRef}
            className="ros-error automatic-research-entry__error"
            role="alert"
            tabIndex={-1}
          >
            {error}
          </p>
        )}
        {busy && (
          <p className="automatic-research-entry__status" role="status">
            自动研究正在启动，请稍候。
          </p>
        )}
        <button
          className="ros-button ros-button--primary automatic-research-entry__submit"
          type="submit"
          disabled={!canSubmit}
        >
          {busy ? "正在启动…" : "开始自动研究"}
        </button>
      </form>
    </main>
  );
}
