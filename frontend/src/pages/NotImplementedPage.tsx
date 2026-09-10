import { Link } from "react-router-dom";

interface Props {
  title: string;
  hint?: string;
}

export function NotImplementedPage({ title, hint }: Props) {
  return (
    <section className="page page--not-implemented">
      <h1>{title}</h1>
      <p className="muted">
        {hint ??
          "该模块正在建设中，当前原型先以研究总览 + 行业研究 + 关系模式 + 证据库 + 审核队列为主流程。"}
      </p>
      <p>
        <Link to="/">← 返回研究总览</Link>
      </p>
    </section>
  );
}