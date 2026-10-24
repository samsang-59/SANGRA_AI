// Small line icons used by the screens (user, mail, clock ...), drawn inline so no icon font is loaded.
const base = { width: 18, height: 18, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.8,
  strokeLinecap: "round", strokeLinejoin: "round", "aria-hidden": "true" };

export const UserIcon = () => (
  <svg {...base}><circle cx="12" cy="8" r="4" /><path d="M4 21c1.5-4 4.5-6 8-6s6.5 2 8 6" /></svg>
);
export const MailIcon = () => (
  <svg {...base}><rect x="3" y="5" width="18" height="14" rx="2" /><path d="M3 7l9 6 9-6" /></svg>
);
export const DownloadIcon = () => (
  <svg {...base}><path d="M12 4v11" /><path d="M7 10l5 5 5-5" /><path d="M5 20h14" /></svg>
);
export const CheckIcon = () => (
  <svg {...base}><path d="M5 12.5l4.5 4.5L19 7.5" /></svg>
);
export const WarningIcon = () => (
  <svg {...base}><path d="M12 4l9 16H3z" /><path d="M12 10v4" /><path d="M12 17.2v.1" /></svg>
);
