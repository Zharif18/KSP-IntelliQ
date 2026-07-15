import { useState, useEffect } from "react";
import KSPIntelliQDashboard from "./components/Dashboard.jsx";
import LoginPage from "./components/LoginPage.jsx";
import catalyst from "./catalystInit.jsx";

function App() {
  const [authChecked, setAuthChecked] = useState(false);
  const [authenticated, setAuthenticated] = useState(false);

  useEffect(() => {
    if (!catalyst || !catalyst.auth) {
      // Catalyst SDK not loaded (e.g. local dev without the CLI proxy) —
      // don't hard-block the app, just skip the gate.
      setAuthChecked(true);
      return;
    }
    Promise.resolve(catalyst.auth.isUserAuthenticated())
      .then((result) => setAuthenticated(!!result))
      .catch(() => setAuthenticated(false))
      .finally(() => setAuthChecked(true));
  }, []);

  if (!authChecked) {
    return (
      <div style={{
        minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center",
        background: "#0e1116", color: "#a8adba", fontFamily: "'Inter', sans-serif", fontSize: 13,
      }}>
        Checking session…
      </div>
    );
  }

  if (!authenticated) return <LoginPage />;

  return <KSPIntelliQDashboard />;
}

export default App;