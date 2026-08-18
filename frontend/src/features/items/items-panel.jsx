import { Package, Plus, Trash2 } from "lucide-react";
import { useState } from "react";

import { ApiErrorAlert } from "@/components/api-error-alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { useCreateItem, useDeleteItem, useItems } from "@/features/items/use-items";
import { ApiError } from "@/lib/api-error";

const PAGE_SIZE = 5;

/**
 * Example CRUD panel. Demonstrates list + create + delete, pagination from
 * `meta`, and mapping `error.details` back onto the offending input.
 */
export function ItemsPanel() {
  const [page, setPage] = useState(1);
  const [name, setName] = useState("");

  const { data, error, isPending, refetch } = useItems({ page, pageSize: PAGE_SIZE });
  const createItem = useCreateItem();
  const deleteItem = useDeleteItem();

  const pagination = data?.pagination;
  const items = data?.items ?? [];

  // Field-level problems come back in error.details; ApiError maps them to
  // input names so a form can show them inline.
  const createError = createItem.error instanceof ApiError ? createItem.error : null;
  const nameError = createError?.fieldErrors.name;

  function handleSubmit(event) {
    event.preventDefault();
    createItem.mutate(
      { name: name.trim(), quantity: 0 },
      { onSuccess: () => setName("") },
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Package className="size-4" />
          Items
        </CardTitle>
        <CardDescription>
          Example resource — <code className="font-mono text-xs">/api/items</code>
        </CardDescription>
      </CardHeader>

      <CardContent className="space-y-4">
        <form onSubmit={handleSubmit} className="space-y-2">
          <div className="flex gap-2">
            <Input
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="New item name"
              aria-label="New item name"
              aria-invalid={Boolean(nameError)}
              disabled={createItem.isPending}
            />
            <Button type="submit" disabled={createItem.isPending || !name.trim()}>
              <Plus />
              Add
            </Button>
          </div>
          {nameError ? <p className="text-destructive text-sm">{nameError}</p> : null}
        </form>

        {/* Non-field errors (e.g. a 409 duplicate name) get the full alert. */}
        {createError && !nameError ? <ApiErrorAlert error={createError} /> : null}

        {isPending ? (
          <div className="space-y-2">
            <Skeleton className="h-9 w-full" />
            <Skeleton className="h-9 w-full" />
            <Skeleton className="h-9 w-full" />
          </div>
        ) : error ? (
          <ApiErrorAlert error={error} onRetry={() => refetch()} />
        ) : items.length === 0 ? (
          <p className="text-muted-foreground py-6 text-center text-sm">
            No items yet. Add one above.
          </p>
        ) : (
          <ul className="divide-y">
            {items.map((item) => (
              <li
                key={item.id}
                className="flex items-center justify-between gap-2 py-2"
              >
                <span className="flex items-center gap-2 text-sm">
                  <span className="font-medium">{item.name}</span>
                  <Badge variant="outline">qty {item.quantity}</Badge>
                </span>
                <Button
                  variant="ghost"
                  size="icon"
                  onClick={() => deleteItem.mutate(item.id)}
                  disabled={deleteItem.isPending}
                  aria-label={`Delete ${item.name}`}
                >
                  <Trash2 />
                </Button>
              </li>
            ))}
          </ul>
        )}

        {deleteItem.error ? <ApiErrorAlert error={deleteItem.error} /> : null}
      </CardContent>

      {pagination && pagination.total_pages > 1 ? (
        <CardFooter className="justify-between border-t">
          <span className="text-muted-foreground text-sm">
            Page {pagination.page} of {pagination.total_pages} ·{" "}
            {pagination.total_items} total
          </span>
          <span className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => setPage((current) => current - 1)}
              disabled={!pagination.has_previous}
            >
              Previous
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setPage((current) => current + 1)}
              disabled={!pagination.has_next}
            >
              Next
            </Button>
          </span>
        </CardFooter>
      ) : null}
    </Card>
  );
}
