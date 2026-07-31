import type { PageStateError } from "../domain/types";

interface Props {
  error: PageStateError | null;
  isHistorical: boolean;
  writeDisabled?: boolean;
}

export function PageStateBanners({ error, isHistorical, writeDisabled }: Props) {
  if (!error && !isHistorical && !writeDisabled) return null;

  if (error?.kind === "backend_unavailable") {
    return (
      <div
        className="page-banner page-banner--offline"
        role="status"
        data-testid="banner-offline"
      >
        <strong>后端不可用</strong> · 浏览保持只读，写操作已禁用。已缓存内容仍然可见。
      </div>
    );
  }
  if (error?.kind === "permission_denied") {
    return (
      <div
        className="page-banner page-banner--permission"
        role="status"
        data-testid="banner-permission"
      >
        <strong>权限不足</strong> · 不可执行的操作已隐藏，你可以继续查看有权访问的研究上下文。
      </div>
    );
  }
  if (isHistorical) {
    return (
      <div
        className="page-banner page-banner--historical"
        role="status"
        data-testid="banner-historical"
      >
        ⏱ <strong>历史回放</strong> · 截止日之后的材料已隐藏；当前视图不是真实当前状态。
      </div>
    );
  }
  if (writeDisabled) {
    return (
      <div
        className="page-banner page-banner--readonly"
        role="status"
        data-testid="banner-readonly"
      >
        <strong>只读模式</strong> · 此视图下写操作不可用。
      </div>
    );
  }
  return null;
}