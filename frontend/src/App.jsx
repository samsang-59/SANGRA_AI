import { Navigate, Route, Routes } from "react-router-dom";
import { AnyAuthRoute, ProtectedRoute, PublicOnlyRoute } from "./auth/guards.jsx";
import Layout from "./components/Layout.jsx";
import AccountPage from "./pages/AccountPage.jsx";
import ForgotPasswordPage from "./pages/ForgotPasswordPage.jsx";
import LoginPage from "./pages/LoginPage.jsx";
import NotFoundPage from "./pages/NotFoundPage.jsx";
import PlannedPage from "./pages/PlannedPage.jsx";
import HistoryPage from "./pages/HistoryPage.jsx";
import ProjectFormPage from "./pages/ProjectFormPage.jsx";
import ProjectsPage from "./pages/ProjectsPage.jsx";
import RegisterPage from "./pages/RegisterPage.jsx";
import ResetPasswordPage from "./pages/ResetPasswordPage.jsx";
import StartRunPage from "./pages/StartRunPage.jsx";
import VerifyEmailPage from "./pages/VerifyEmailPage.jsx";

// The route table of 05_frontend_design.md F1, with the guards of F3. Pages not built yet show which phase builds them.
export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/projects" replace />} />
      <Route element={<PublicOnlyRoute />}>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/register" element={<RegisterPage />} />
        <Route path="/forgot-password" element={<ForgotPasswordPage />} />
        <Route path="/reset-password" element={<ResetPasswordPage />} />
      </Route>
      <Route element={<AnyAuthRoute />}>
        <Route path="/verify-email" element={<VerifyEmailPage />} />
      </Route>
      <Route element={<ProtectedRoute />}>
        <Route element={<Layout />}>
          <Route path="/account" element={<AccountPage />} />
          <Route path="/projects" element={<ProjectsPage />} />
          <Route path="/projects/new" element={<ProjectFormPage />} />
          <Route path="/projects/:projectId/edit" element={<ProjectFormPage />} />
          <Route path="/projects/:projectId/run" element={<StartRunPage />} />
          <Route path="/runs" element={<HistoryPage />} />
          <Route path="/runs/:runId" element={<PlannedPage title="Run" phase="FE5" />} />
        </Route>
      </Route>
      <Route path="*" element={<NotFoundPage />} />
    </Routes>
  );
}
