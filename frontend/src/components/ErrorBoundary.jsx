// The error screen (FE7-F02): a page that throws while drawing shows a friendly message with "reload", never a white
// page. React needs a class component for this. A new resetKey (another address) clears the error.
import { Component } from "react";

export function CrashScreen() {
  return (
    <main className="crash-page" role="alert">
      <div className="auth-column crash">
        <p className="crash-shell mono">$ sangra --page {"→"} unexpected error</p>
        <h1>Something went wrong</h1>
        <p className="page-subtitle">This page ran into a problem. Your runs and projects are safe on the server.</p>
        <div className="row">
          <button type="button" className="button button-primary" onClick={() => window.location.reload()}>reload</button>
          <a className="button" href="/projects">go to projects</a>
        </div>
      </div>
    </main>
  );
}

export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { error: null, key: props.resetKey };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  static getDerivedStateFromProps(props, state) {
    return props.resetKey !== state.key ? { error: null, key: props.resetKey } : null;
  }

  componentDidCatch(error, info) {
    console.error("The page crashed:", error, info?.componentStack);
  }

  render() {
    return this.state.error ? <CrashScreen /> : this.props.children;
  }
}
