import { Outlet } from "react-router-dom";

import { AppHeader } from "@/components/layout/app-header";

/** Shell shared by every route: header plus a centred content column. */
export function AppLayout() {
  return (
    <div className="flex min-h-svh flex-col">
      <AppHeader />
      <main className="mx-auto w-full max-w-5xl flex-1 px-4 py-8">
        <Outlet />
      </main>
      <footer className="text-muted-foreground border-t px-4 py-4 text-center text-xs">
        LightNWonder
      </footer>
    </div>
  );
}
