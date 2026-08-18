import { AlertCircle } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { ApiError } from "@/lib/api-error";

/**
 * Renders an `ApiError` consistently wherever a request fails.
 *
 * Shows the backend's `message`, any field-level `details`, and the request id —
 * which is the fastest way to find the matching server log line.
 */
export function ApiErrorAlert({ error, onRetry, className }) {
  if (!error) return null;

  const apiError = error instanceof ApiError ? error : ApiError.from(error);

  return (
    <Alert variant="destructive" className={className}>
      <AlertCircle />
      <AlertTitle>{apiError.message}</AlertTitle>
      <AlertDescription>
        {apiError.details.length > 0 ? (
          <ul className="list-disc pl-4">
            {apiError.details.map((detail, index) => (
              <li key={`${detail.field ?? "error"}-${index}`}>
                {detail.field ? (
                  <span className="font-medium">{detail.field}: </span>
                ) : null}
                {detail.message}
              </li>
            ))}
          </ul>
        ) : null}
        <p className="text-muted-foreground font-mono text-xs">
          {apiError.code}
          {apiError.status ? ` · HTTP ${apiError.status}` : ""}
          {apiError.requestId ? ` · ${apiError.requestId}` : ""}
        </p>
        {onRetry && apiError.isRetryable ? (
          <Button variant="outline" size="sm" onClick={onRetry} className="mt-1">
            Retry
          </Button>
        ) : null}
      </AlertDescription>
    </Alert>
  );
}
