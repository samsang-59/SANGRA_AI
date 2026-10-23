import { Navigate, Route, Routes } from "react-router-dom";
import NotFoundPage from "./pages/NotFoundPage.jsx";
import PlannedPage from "./pages/PlannedPage.jsx";

// The route table of 05_frontend_design.md F1. Pages not built yet show which phase builds them.
export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/projects" replace />} />
      <Route path="/login" element={<PlannedPage title="Log in" phase="FE2" />} />
      <Route path="/register" element={<PlannedPage title="Create your account" phase="FE2" />} />
      <Route path="/verify-email" element={<PlannedPage title="Verify email" phase="FE2" />} />
      <Route path="/forgot-password" element={<PlannedPage title="Forgot password" phase="FE2" />} />
      <Route path="/reset-password" element={<PlannedPage title="Set a new password" phase="FE2" />} />
      <Route path="/account" element={<PlannedPage title="Account" phase="FE2" />} />
      <Route path="/projects" element={<PlannedPage title="Projects" phase="FE3" />} />
      <Route path="/projects/new" element={<PlannedPage title="New project" phase="FE3" />} />
      <Route path="/projects/:projectId/edit" element={<PlannedPage title="Edit project" phase="FE3" />} />
      <Route path="/projects/:projectId/run" element={<PlannedPage title="Start a run" phase="FE4" />} />
      <Route path="/runs" element={<PlannedPage title="History" phase="FE4" />} />
      <Route path="/runs/:runId" element={<PlannedPage title="Run" phase="FE5" />} />
      <Route path="*" element={<NotFoundPage />} />
    </Routes>
  );
}
