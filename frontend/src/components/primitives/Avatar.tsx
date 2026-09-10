interface Props {
  name: string;
  size?: number;
}

// Avatar — 圆形首字头像，模仿原型中每个活动 / 任务右侧的头像圆点。
export function Avatar({ name, size = 22 }: Props) {
  const initial = name.trim().slice(0, 1);
  return (
    <span
      className="avatar"
      aria-hidden
      style={{ width: size, height: size, fontSize: Math.round(size * 0.55) }}
    >
      {initial}
    </span>
  );
}