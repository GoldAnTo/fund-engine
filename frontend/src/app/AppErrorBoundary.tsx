import { Component, createRef, type ReactNode } from "react";

export class AppErrorBoundary extends Component<{
  children: ReactNode;
  onRetry?: () => void;
}, { failed: boolean }> {
  override state = { failed: false };
  private content = createRef<HTMLElement>();

  static getDerivedStateFromError() { return { failed: true }; }

  override componentDidCatch() { this.content.current?.focus(); }

  override render() {
    if (!this.state.failed) return this.props.children;
    return <main className="app-error" ref={this.content} tabIndex={-1}>
      <h1>暂时无法打开研究页面</h1>
      <p role="alert">页面暂时无法显示。请检查网络后重新加载。</p>
      <p>重新加载会保留当前地址；尚未提交的输入可能需要重新填写。</p>
      <button type="button" onClick={this.props.onRetry ?? (() => window.location.reload())}>重新加载当前页面</button>
      <a href="/">返回研究空间</a>
    </main>;
  }
}
