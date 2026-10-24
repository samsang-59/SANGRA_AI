// The frame of the logged-out pages (login, register, forgot, reset, verify): the brand, a line under it, a card.
import Brand from "./Brand.jsx";

export default function AuthLayout({ subtitle, children, footer, wide = false }) {
  return (
    <main className="auth-page">
      <div className={`auth-column${wide ? " auth-column-wide" : ""}`}>
        <div className="auth-heading">
          <Brand size={28} />
          {subtitle && <p className="auth-subtitle">{subtitle}</p>}
        </div>
        {children}
        {footer && <p className="auth-footer">{footer}</p>}
      </div>
    </main>
  );
}
