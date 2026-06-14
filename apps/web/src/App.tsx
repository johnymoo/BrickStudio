import { Routes, Route, Navigate } from "react-router-dom";
import { JobList } from "@features/jobs/JobList";
import { JobDetail } from "@features/jobs/JobDetail";
import { ParametricPage } from "@features/parametric/ParametricPage";
import { CaptureDetail } from "@features/captures/CaptureDetail";
import { LibraryList } from "@features/library/LibraryList";
import { AppHeader } from "@components/AppHeader";
import { InstallPrompt } from "@components/InstallPrompt";

export default function App() {
  return (
    <div className="flex min-h-full flex-col bg-page">
      <AppHeader />
      <main className="flex-1">
        <Routes>
          <Route path="/" element={<JobList />} />
          <Route path="/parametric" element={<ParametricPage />} />
          <Route path="/library" element={<LibraryList />} />
          <Route path="/jobs/:id" element={<JobDetail />} />
          <Route path="/captures/:id" element={<CaptureDetail />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
      <InstallPrompt />
    </div>
  );
}
