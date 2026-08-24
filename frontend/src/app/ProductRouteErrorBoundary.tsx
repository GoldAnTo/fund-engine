import { Component, type ErrorInfo, type ReactNode } from "react";

type Props = { children: ReactNode };
type State = { error: Error | null };

export class ProductRouteErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(_error: Error, _info: ErrorInfo) {
    // Rendering a local recovery state is sufficient; application telemetry is not configured.
  }

  render() {
    if (this.state.error) {
      return (
        <main className="ir-page">
          <div className="ir-alert" role="alert">
            <h1>产品页面载入失败</h1>
            <p>页面代码未能载入；可以留在独立投资研究产品内重试。</p>
            <button className="ir-button" onClick={() => this.setState({ error: null })} type="button">
              重试载入产品页面
            </button>
          </div>
        </main>
      );
    }
    return this.props.children;
  }
}
