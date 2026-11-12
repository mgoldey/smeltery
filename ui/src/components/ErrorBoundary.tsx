import { Component, type ErrorInfo, type ReactNode } from "react";
import { Link } from "react-router-dom";

type Props = { children: ReactNode; resetKey?: string };
type State = { error: Error | null };

/**
 * A throw while rendering (Mol*, Plotly and RDKit.js are third-party and do throw) shows what went wrong and a way
 * out, instead of blanking the app. `resetKey` is the route: navigating away clears the error.
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("render error", error, info.componentStack);
  }

  componentDidUpdate(prev: Props) {
    if (this.state.error && prev.resetKey !== this.props.resetKey) this.setState({ error: null });
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    return (
      <main role="alert" className="error-boundary" data-testid="error-boundary">
        <h1>Something went wrong</h1>
        <p className="mono">{error.message || String(error)}</p>
        <p>
          <button type="button" className="primary" onClick={() => window.location.reload()}>
            Reload
          </button>{" "}
          <Link to="/bench">Back to runs</Link>
        </p>
      </main>
    );
  }
}
