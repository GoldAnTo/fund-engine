import type { ThesisAssessment } from "../domain/types";
import { StatusMark } from "./StatusMark";
import { Breadcrumb } from "./primitives/Breadcrumb";
import { Chip } from "./primitives/Chip";
import { Button } from "./primitives/Button";
import { Avatar } from "./primitives/Avatar";

interface Props {
  title: string;
  topic: string;
  author: string;
  updatedAt: string;
  assessment: ThesisAssessment | null;
  onMarkForReview?: () => void;
}

const CONCLUSION_LABEL = {
  supported: "已支持",
  contradicted: "反证",
  insufficient_evidence: "证据不足",
} as const;

export function ResearchCaseHeader({
  title,
  topic,
  author,
  updatedAt,
  assessment,
  onMarkForReview,
}: Props) {
  const breadcrumbItems = [
    { label: "行业研究", href: "/cases" },
    {
      label: topic.split("·")[0]?.trim() ?? topic,
      href: "/cases",
    },
    { label: "行业案例", href: "/cases" },
    { label: title },
  ];

  return (
    <header className="case-header" data-testid="case-header">
      <Breadcrumb items={breadcrumbItems} />
      <div className="case-header__actions">
        <Button variant="ghost" size="sm" type="button">← 返回</Button>
        <Button variant="ghost" size="sm" type="button">☆ 收藏</Button>
        <Button variant="ghost" size="sm" type="button">⤓ 导出</Button>
        <Button variant="ghost" size="sm" type="button">↗ 分享</Button>
        <Button variant="ghost" size="sm" type="button">···</Button>
        <span className="case-header__updated">最后更新 {updatedAt}</span>
        {onMarkForReview && (
          <Button
            variant="primary"
            size="sm"
            type="button"
            onClick={onMarkForReview}
          >
            标记为已复核
          </Button>
        )}
      </div>
      <h1>{title}</h1>
      <p className="case-header__byline">
        <span className="case-header__byline__row">
          <span className="case-header__byline__author">
            <Avatar name={author} size={18} />
            <span>{author}</span>
          </span>
          <span className="case-header__byline__sep">·</span>
          <span>创建时间 {updatedAt}</span>
        </span>
        <Button variant="ghost" size="xs" type="button">✏ 编辑</Button>
      </p>

      <p className="case-header__conclusion">
        当前判断：
        {assessment ? (
          <>
            <strong data-conclusion={assessment.conclusion}>
              {CONCLUSION_LABEL[assessment.conclusion]}
            </strong>
            {assessment.provisional && !assessment.review && (
              <Chip tone="amber" bordered size="xs">
                未人工复核
              </Chip>
            )}
            {assessment.review && (
              <>
                <StatusMark
                  status={
                    assessment.review.outcome === "rejected"
                      ? "human_rejected"
                      : assessment.review.outcome === "modified"
                        ? "human_modified"
                        : "human_confirmed"
                  }
                />
                ：{assessment.review.reason}
              </>
            )}
          </>
        ) : (
          <span data-testid="no-assessment">尚无 AI 判断</span>
        )}
      </p>
    </header>
  );
}