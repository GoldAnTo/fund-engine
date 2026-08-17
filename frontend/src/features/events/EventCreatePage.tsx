import { useEffect, useRef, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";

import { researchClient } from "../../data/researchClient";

export function EventCreatePage() {
  const navigate = useNavigate();
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const canSubmit = Boolean(input.trim()) && !busy;

  useEffect(() => {
    if (error && !busy) inputRef.current?.focus();
  }, [busy, error]);

  async function startResearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const researchInput = input.trim();
    if (!researchInput || busy) return;

    setBusy(true);
    setError(null);
    try {
      const started = await researchClient.startAutomaticResearch(researchInput);
      navigate(`/events/${started.caseId}/automatic-research`);
    } catch {
      setError("自动研究未能启动，请稍后重试。");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="ros-page automatic-research-entry">
      <header className="automatic-research-entry__heading">
        <p className="ros-eyebrow">自动研究</p>
        <h1>告诉系统你想研究什么</h1>
        <p>
          输入一个问题或事件，系统会自动采集资料、分析证据并生成结论。你只需查看处理过程和结果。
        </p>
      </header>

      <form className="automatic-research-entry__form" onSubmit={startResearch}>
        <label htmlFor="automatic-research-input">研究问题</label>
        <textarea
          id="automatic-research-input"
          ref={inputRef}
          value={input}
          onChange={(event) => setInput(event.target.value)}
          placeholder="例如：英伟达新产品会如何影响供应链？"
          rows={7}
          disabled={busy}
          aria-invalid={Boolean(error)}
          aria-describedby={error ? "automatic-research-error" : "automatic-research-help"}
        />
        <p id="automatic-research-help" className="automatic-research-entry__help">
          写清楚关注的事件、公司或影响，系统会自动完成其余步骤。
        </p>
        {error && (
          <p id="automatic-research-error" className="ros-error" role="alert">
            {error}
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
