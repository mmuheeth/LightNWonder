import { Route, Routes } from "react-router-dom";

import { AppLayout } from "@/components/layout/app-layout";
import { AnalyzeSpinPage } from "@/pages/analyze-spin-page";
import { CapturesPage } from "@/pages/captures-page";
import { DashboardPage } from "@/pages/dashboard-page";
import { GameConfigPage } from "@/pages/game-config-page";
import { NotFoundPage } from "@/pages/not-found-page";

/** Route table. Add pages under the shared layout. */
export default function App() {
  return (
    <Routes>
      <Route element={<AppLayout />}>
        <Route index element={<DashboardPage />} />
        <Route path="event-captures" element={<CapturesPage />} />
        <Route path="event-captures/:runId" element={<CapturesPage />} />
        <Route path="game-config" element={<GameConfigPage />} />
        <Route path="analyze-spin" element={<AnalyzeSpinPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
}
