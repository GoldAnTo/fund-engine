import type { WorkbenchResponse } from "../types";

export function AssessmentHeader({ data }: { data: WorkbenchResponse }) {
  const { assessment, review, major_gap } = data;
  return (
    <header className="assessment-header">
      <h1>{data.case.title}</h1>
      {data.focus_thesis && (
        <p className="focus-thesis">命题：{data.focus_thesis.statement}</p>
      )}
      {assessment && (
        <div className="assessment">
          <span className={`conclusion ${assessment.conclusion}`}>
            AI 判断：{assessment.conclusion}
          </span>
          {assessment.provisional && !review && (
            <span className="provisional">AI 临时判断，未经人工复核</span>
          )}
          {review && <span className="review">已复核：{review.outcome}</span>}
          <p className="rationale">{assessment.rationale}</p>
        </div>
      )}
      {major_gap && <p className="major-gap">主要阻塞：{major_gap}</p>}
    </header>
  );
}
