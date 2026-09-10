import type { ReactNode } from "react";

interface Props {
  items: ReactNode[];
}

// BulletList — 绿色圆点项目列表，与 prototype 1 / 2 / 3 中"核心结论"列表一致。
export function BulletList({ items }: Props) {
  return (
    <ul className="bullet-list">
      {items.map((node, i) => (
        <li key={i} className="bullet-list__item">
          <span className="bullet-list__dot" aria-hidden />
          <span className="bullet-list__text">{node}</span>
        </li>
      ))}
    </ul>
  );
}