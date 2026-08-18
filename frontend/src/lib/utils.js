import { clsx } from "clsx";
import { twMerge } from "tailwind-merge";

/**
 * Merge class names, letting later Tailwind utilities win over earlier ones.
 *
 * Required by shadcn/ui components; also the right way to accept a `className`
 * prop that can override a component's own defaults.
 */
export function cn(...inputs) {
  return twMerge(clsx(inputs));
}
