import { clsx } from "clsx";
import { twMerge } from "tailwind-merge";

// Merges class names, letting later Tailwind utilities win — required by
// shadcn/ui components and for accepting an overriding `className` prop.
export function cn(...inputs) {
  return twMerge(clsx(inputs));
}
