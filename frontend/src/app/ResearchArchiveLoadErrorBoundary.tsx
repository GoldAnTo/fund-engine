import { Component, type ReactNode } from "react";

type Props = { children: ReactNode; resetKey?: string };
type State = { hasError: boolean };

export class ResearchArchiveLoadErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false };

  static getDerivedStateFromError(): State {
    return { hasError: true };
  }

  componentDidUpdate(previousProps: Props) {
    if (this.state.hasError && previousProps.resetKey !== this.props.resetKey) {
      this.setState({ hasError: false });
    }
  }

  render() {
    if (!this.state.hasError) return this.props.children;
    return (
      <main className="ros-page" role="alert" aria-live="assertive">
        <header className="ros-page-head"><div>
          <p className="ros-eyebrow">研究资产 · Research Archive</p>
          <h1>档案暂时无法载入</h1>
          <p>公司／行业档案未能载入。不会以当前或最新研究替代这个版本。</p>
          <p>请刷新页面后重试，或返回档案目录重新打开。</p>
          <a href="/underwriting/research">返回档案目录</a>
        </div></header>
      </main>
    );
  }
}
