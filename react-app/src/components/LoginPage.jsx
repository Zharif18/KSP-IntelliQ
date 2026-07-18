import { useEffect, useState, useRef } from "react";
import { Shield, Sun, Moon } from "lucide-react";
import catalyst from "../catalystInit.jsx";

/* ---------------------------------------------------------------------
   Embeds Catalyst's native login form as an iFrame inside #catalyst-login.

   Requires, in the Catalyst console (Cloud Scale > Authentication):
     1. Native Catalyst Authentication > Embedded Authentication — enabled
     2. Public Signup toggle — ON, if you want people to self-register

   THEMING NOTE: the embedded form lives in an iframe served from Zoho's
   own domain, a different origin from this app. It cannot see this page's
   CSS variables, so we ship two standalone stylesheets
   (embeddediframe-dark.css / embeddediframe-light.css) with the same
   colors hardcoded, and tell Catalyst which one to load via css_url.
   That URL also has to be ABSOLUTE (window.location.origin + the real
   deployed path) — a root-relative path like "/css/x.css" resolves
   against the iframe's own origin, not this app's, and 404s silently,
   which is why the form was rendering completely unstyled before.

   The SDK script finishes its own async setup AFTER the script tag runs,
   so calling catalyst.auth.signIn() on the very first render can fire
   before it's ready and silently no-op. We poll briefly for
   catalyst.auth to exist before embedding.
------------------------------------------------------------------------ */

function getSystemTheme() {
  if (typeof window !== "undefined" && window.matchMedia) {
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  return "dark";
}

export default function LoginPage() {
  const [theme, setTheme] = useState(getSystemTheme);
  const [manualOverride, setManualOverride] = useState(false);
  const [status, setStatus] = useState("loading"); // loading | ready | timeout
  const embedded = useRef(false);
  const pollTimer = useRef(null);

  // Auto-follow the OS/browser light-dark setting, unless the person has
  // clicked the toggle themselves this session.
  useEffect(() => {
    if (!window.matchMedia) return;
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const handleChange = (e) => {
      if (!manualOverride) setTheme(e.matches ? "dark" : "light");
    };
    mq.addEventListener("change", handleChange);
    return () => mq.removeEventListener("change", handleChange);
  }, [manualOverride]);

  // (Re)embed the Catalyst widget whenever the theme changes, so the
  // iframe's own stylesheet always matches the current mode.
  useEffect(() => {
    const container = document.getElementById("catalyst-login");
    if (container) container.innerHTML = "";
    embedded.current = false;
    setStatus("loading");
    if (pollTimer.current) clearTimeout(pollTimer.current);

    let attempts = 0;
    const maxAttempts = 25; // ~5s at 200ms intervals

    const tryEmbed = () => {
      const c = catalyst || window.catalyst;
      if (c && c.auth && typeof c.auth.signIn === "function") {
        if (!embedded.current) {
          embedded.current = true;

          const config = {
            css_url: `${window.location.origin}/app/css/embeddediframe-${theme}.css`,
            service_url: `${window.location.origin}/app/index.html`,
          };

          c.auth.signIn("catalyst-login", config);
          setStatus("ready");
        }
        return;
      }
      attempts += 1;
      if (attempts >= maxAttempts) {
        setStatus("timeout");
        return;
      }
      pollTimer.current = setTimeout(tryEmbed, 200);
    };

    tryEmbed();
    return () => {
      if (pollTimer.current) clearTimeout(pollTimer.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [theme]);

  const toggleTheme = () => {
    setManualOverride(true);
    setTheme((t) => (t === "dark" ? "light" : "dark"));
  };

  return (
    <div className={`login-wrap theme-${theme}`}>
      <style>{`
        .login-wrap {
          min-height: 100vh; display: flex; flex-direction: column; align-items: center;
          justify-content: center; font-family: 'Inter', sans-serif; position: relative;
        }
        .theme-dark {
          --ink: #0e1116; --panel: #171b23; --gold: #d4b073; --gold-strong: #e8c98d;
          --text: #f3f1ea; --muted: #a8adba; --border: rgba(255,255,255,0.1);
          background: var(--ink); color: var(--text);
          background-image: radial-gradient(circle at 85% 0%, rgba(212,176,115,0.07), transparent 50%);
        }
        .theme-light {
          --ink: #f3efe6; --panel: #ffffff; --gold: #93692e; --gold-strong: #7a5624;
          --text: #201d17; --muted: #5c5749; --border: rgba(32,29,23,0.12);
          background: var(--ink); color: var(--text);
          background-image: radial-gradient(circle at 85% 0%, rgba(147,105,46,0.05), transparent 50%);
        }
        .theme-toggle { position: absolute; top: 20px; right: 20px; width: 34px; height: 34px;
          border-radius: 8px; background: var(--panel); border: 1px solid var(--border);
          display: flex; align-items: center; justify-content: center; cursor: pointer; color: var(--text); }
        .login-brand { display: flex; flex-direction: column; align-items: center; gap: 10px; margin-bottom: 28px; }
        .login-title { font-size: 22px; font-weight: 700; }
        .login-sub { color: var(--muted); font-size: 11px; letter-spacing: 0.12em; text-transform: uppercase; }
        .login-card { background: var(--panel); border: 1px solid var(--border); border-radius: 16px;
          padding: 28px; width: 360px; max-width: 90vw; min-height: 340px; box-sizing: border-box;
          box-shadow: 0 12px 40px rgba(0,0,0,0.2); display: flex; flex-direction: column; align-items: center; justify-content: center;
          overflow: visible; }
        .login-status { color: var(--muted); font-size: 12.5px; text-align: center; padding: 20px; line-height: 1.6; }
        .login-status.error { color: #c17a7a; }

        /* Force parent frame matching for the embedded container.
           IMPORTANT: no forced height on the iframe. The widget's own steps
           (email / password / OTP / TOTP) are different heights, and a fixed
           height clips taller steps, forcing an internal scrollbar inside
           the white box (which reads as "nothing renders, just scrolls").
           min-height gives a sane floor for the first paint; the iframe is
           free to grow past it once content loads. */
        #catalyst-login {
          width: 100%;
          min-height: 460px;
          background: transparent;
        }
        #catalyst-login iframe {
          width: 100% !important;
          min-height: 460px;
          border: none !important;
          background: transparent !important;
          color-scheme: ${theme};
        }
      `}</style>

      <div className="theme-toggle" onClick={toggleTheme} title="Toggle theme">
        {theme === "dark" ? <Sun size={15} /> : <Moon size={15} />}
      </div>

      <div className="login-brand">
        <Shield size={32} color="var(--gold-strong)" />
        <div className="login-title">KSP IntelliQ</div>
        <div className="login-sub">Crime Intelligence &amp; Decision Support</div>
      </div>

      <div className="login-card">
        {status === "timeout" && (
          <div className="login-status error">
            Sign-in form didn't load. This usually means Embedded Authentication isn't
            enabled yet in the Catalyst console (Cloud Scale → Authentication → Native
            Catalyst Authentication). Check that, then reload this page.
          </div>
        )}
        {status === "loading" && <div className="login-status">Loading sign-in…</div>}
        <div id="catalyst-login" style={{ display: status === "ready" ? "block" : "none" }} />
      </div>
    </div>
  );
}