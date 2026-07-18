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
    <div className={`login-wrap theme-${theme}`} data-theme={theme}>
      <style>{`
        .login-wrap {
          min-height: 100vh; display: flex; flex-direction: column; align-items: center;
          justify-content: center; font-family: -apple-system, 'Inter', sans-serif; position: relative;
          overflow: hidden; background: var(--ink); color: var(--text);
          transition: background 0.4s ease, color 0.4s ease;
        }

        /* Soft ambient glow, not a flat gradient — sits behind everything,
           drifts slowly so the screen never feels static. */
        .login-wrap::before, .login-wrap::after {
          content: ''; position: absolute; border-radius: 50%; filter: blur(90px);
          pointer-events: none; opacity: 0.5;
        }
        .login-wrap::before {
          width: 620px; height: 620px; top: -220px; right: -160px;
          background: radial-gradient(circle, var(--gold) 0%, transparent 70%);
          opacity: 0.16; animation: drift-a 16s ease-in-out infinite alternate;
        }
        .login-wrap::after {
          width: 480px; height: 480px; bottom: -200px; left: -140px;
          background: radial-gradient(circle, var(--gold-strong) 0%, transparent 70%);
          opacity: 0.1; animation: drift-b 20s ease-in-out infinite alternate;
        }
        @keyframes drift-a { from { transform: translate(0,0); } to { transform: translate(-40px, 30px); } }
        @keyframes drift-b { from { transform: translate(0,0); } to { transform: translate(30px, -20px); } }

        .theme-toggle {
          position: absolute; top: 24px; right: 24px; width: 38px; height: 38px;
          border-radius: 12px; background: var(--panel); backdrop-filter: var(--blur);
          -webkit-backdrop-filter: var(--blur); border: 1px solid var(--panel-border);
          display: flex; align-items: center; justify-content: center; cursor: pointer;
          color: var(--text); transition: transform 0.2s var(--ease-rise), border-color 0.2s ease;
          z-index: 5;
        }
        .theme-toggle:hover { transform: translateY(-2px); border-color: var(--gold); }

        .login-brand {
          display: flex; flex-direction: column; align-items: center; gap: 12px;
          margin-bottom: 32px; position: relative; z-index: 2;
        }
        .login-brand-icon {
          width: 56px; height: 56px; border-radius: 16px; display: flex; align-items: center;
          justify-content: center; background: var(--panel); backdrop-filter: var(--blur);
          -webkit-backdrop-filter: var(--blur); border: 1px solid var(--panel-border);
          box-shadow: var(--shadow);
        }
        .login-title { font-size: 24px; font-weight: 650; letter-spacing: -0.01em; }
        .login-sub {
          color: var(--muted); font-size: 11px; letter-spacing: 0.14em; text-transform: uppercase;
        }

        .login-card {
          background: var(--panel); backdrop-filter: var(--blur); -webkit-backdrop-filter: var(--blur);
          border: 1px solid var(--panel-border); border-radius: var(--radius-lg);
          padding: 32px; width: 380px; max-width: 90vw; min-height: 340px; box-sizing: border-box;
          box-shadow: var(--shadow); display: flex; flex-direction: column; align-items: center;
          justify-content: center; overflow: visible; position: relative; z-index: 2;
        }
        /* Fine top highlight — the "glass catching light" detail that sells depth. */
        .login-card::before {
          content: ''; position: absolute; top: 0; left: 16px; right: 16px; height: 1px;
          background: linear-gradient(90deg, transparent, var(--panel-border), transparent);
        }

        .login-status { color: var(--muted); font-size: 12.5px; text-align: center; padding: 20px; line-height: 1.6; }
        .login-status.error { color: #d99b9b; }

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

      <div className="theme-toggle rise-in" style={{ "--rise-delay": "0.05s" }} onClick={toggleTheme} title="Toggle theme">
        {theme === "dark" ? <Sun size={15} /> : <Moon size={15} />}
      </div>

      <div className="login-brand rise-in" style={{ "--rise-delay": "0.05s" }}>
        <div className="login-brand-icon">
          <Shield size={26} color="var(--gold-strong)" />
        </div>
        <div className="login-title">KSP IntelliQ</div>
        <div className="login-sub">Crime Intelligence &amp; Decision Support</div>
      </div>

      <div className="login-card rise-in" style={{ "--rise-delay": "0.18s" }}>
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