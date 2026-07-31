import { ButtonHTMLAttributes, forwardRef } from "react";

type Variant = "ghost" | "chip" | "primary" | "danger" | "bare";
type Size = "xs" | "sm" | "md";

interface Props extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
}

// Button — prototype-aligned button system:
//   ghost  = 默认。透明背景、无边框，hover 出现浅灰底或下划线（对应原型中所有"返回/收藏/导出/分享/···"）。
//   chip   = 边框圆角矩形，hover 边框变深。对应 "+ 新建任务"、"全部 ⌄"、"AI 提议"、"公开" 等。
//   primary = 实心色块（苔绿色 / 橙色）。对应"标记为已复核"、"发起复核"、"新增"等主动操作。
//   danger = 实心陶土红。仅用于驳回。
//   bare   = 完全透明，最小占用（用于 icon 按钮）。
export const Button = forwardRef<HTMLButtonElement, Props>(function Button(
  { variant = "ghost", size = "sm", className, children, ...rest },
  ref
) {
  return (
    <button
      ref={ref}
      className={`btn btn--${variant} btn--${size}${className ? ` ${className}` : ""}`}
      {...rest}
    >
      {children}
    </button>
  );
});