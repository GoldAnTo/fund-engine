import type { ReactNode } from "react";

import { useAuth } from "./AuthProvider";

export function RequireAuth({ children }: { children: ReactNode }) {
  const auth = useAuth();
  if (auth.status === "authenticated") return children;
  if (auth.status === "loading") {
    return <main className="ros-page"><section className="ros-empty" aria-busy="true"><strong>正在确认登录身份…</strong><p>系统会先确认用户和研究权限，再读取任何 Case 数据。</p></section></main>;
  }
  if (auth.status === "unauthenticated") {
    return <main className="ros-page"><section className="ros-empty"><strong>{auth.reason === "expired" ? "登录已过期" : "需要登录研究系统"}</strong><p>登录后会回到当前事件页面，尚未读取任何 Case 数据。</p><button className="ros-button ros-button--primary" type="button" onClick={() => void auth.login()}>{auth.reason === "expired" ? "重新登录" : "登录研究系统"}</button></section></main>;
  }
  if (auth.status === "permission_denied") {
    return <main className="ros-page"><section className="ros-empty" role="alert"><strong>没有研究系统权限</strong><p>身份已经确认，但当前用户没有进入研究工作台所需的权限。请联系管理员分配团队和 Case 权限。</p><button className="ros-button ros-button--secondary" type="button" onClick={() => void auth.logout()}>切换账号</button></section></main>;
  }
  return <main className="ros-page"><section className="ros-empty" role="alert"><strong>登录状态读取失败</strong><p>身份服务暂时不可用，系统没有加载 Case 数据。可以稍后重试。</p><button className="ros-button ros-button--primary" type="button" onClick={auth.retry}>重新读取登录状态</button></section></main>;
}
