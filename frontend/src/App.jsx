import { Route, Routes } from "react-router-dom";

import { AppLayout } from "@/components/layout/app-layout";
import { CapturesPage } from "@/pages/captures-page";
import { DashboardPage } from "@/pages/dashboard-page";
import { NotFoundPage } from "@/pages/not-found-page";

/** Route table. Add pages under the shared layout. */
export default function App() {
  return (
    <Routes>
      <Route element={<AppLayout />}>
        <Route index element={<DashboardPage />} />
        <Route path="captures" element={<CapturesPage />} />
        <Route path="captures/:runId" element={<CapturesPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
}
