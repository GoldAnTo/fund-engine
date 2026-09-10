import { Link } from "react-router-dom";

interface Crumb {
  label: string;
  href?: string;
}

export function Breadcrumb({ items }: { items: Crumb[] }) {
  return (
    <nav className="breadcrumb" aria-label="面包屑">
      {items.map((c, i) => {
        const sep = i > 0 ? (
          <span className="breadcrumb__sep" aria-hidden>
            /
          </span>
        ) : null;
        return (
          <span key={`${i}-${c.label}`} className="breadcrumb__item">
            {sep}
            {c.href ? <Link to={c.href}>{c.label}</Link> : <span>{c.label}</span>}
          </span>
        );
      })}
    </nav>
  );
}