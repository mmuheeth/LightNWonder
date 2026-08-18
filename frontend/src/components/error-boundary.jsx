import { Component } from "react";

import { Button } from "@/components/ui/button";

/**
 * Catches render-time exceptions so one broken component cannot blank the page.
 *
 * This only catches errors thrown during rendering. Failed requests are handled
 * by react-query and surfaced per component; see `ApiErrorAlert`.
 */
export class ErrorBoundary extends Component {
  state = { error: null };

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    // Replace with your error reporter (Sentry, Datadog, …).
    console.error("Unhandled render error:", error, info?.componentStack);
  }

  handleReset = () => this.setState({ error: null });

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;

    return (
      <div className="flex min-h-svh flex-col items-center justify-center gap-4 p-6 text-center">
        <div className="space-y-2">
          <h1 className="text-2xl font-semibold tracking-tight">Something broke</h1>
          <p className="text-muted-foreground max-w-md text-sm">
            An unexpected error stopped the page from rendering.
          </p>
        </div>
        {import.meta.env.DEV ? (
          <pre className="bg-muted max-w-xl overflow-auto rounded-md p-4 text-left text-xs">
            {String(error?.stack || error)}
          </pre>
        ) : null}
        <div className="flex gap-2">
          <Button onClick={this.handleReset} variant="outline">
            Try again
          </Button>
          <Button onClick={() => window.location.reload()}>Reload page</Button>
        </div>
      </div>
    );
  }
}
