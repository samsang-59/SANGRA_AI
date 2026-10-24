// One function per auth route (F2.2). The public ones are exempt from the 401 rule (F2.4).
import { request } from "./client.js";

const pub = { public: true };

export const register = (name, email, password) =>
  request("POST", "/auth/register", { ...pub, body: { name, email, password } });
export const login = (email, password) => request("POST", "/auth/login", { ...pub, body: { email, password } });
export const verifyEmail = (token) => request("POST", "/auth/verify-email", { ...pub, body: { token } });
export const forgotPassword = (email) => request("POST", "/auth/forgot-password", { ...pub, body: { email } });
export const resetPassword = (token, newPassword) =>
  request("POST", "/auth/reset-password", { ...pub, body: { token, new_password: newPassword } });
export const resendVerification = () => request("POST", "/auth/resend-verification");
export const changePassword = (oldPassword, newPassword) =>
  request("POST", "/auth/change-password", { body: { old_password: oldPassword, new_password: newPassword } });
export const getMe = () => request("GET", "/auth/me");
