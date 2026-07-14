import { useCallback, useState } from "react";

import { parseAuthFragment } from "./api";
import Chat from "./Chat";
import Login from "./Login";
import Sources, { emptyCatalog, emptySelection, type Catalog, type Selection } from "./Sources";
import { strings } from "./strings";
import Wordmark from "./Wordmark";

// Parsed and scrubbed exactly once at module load: as a useState lazy initializer,
// StrictMode's dev double-invoke would re-parse the already-scrubbed (empty) hash on
// the second call and win, silently dropping the SSO token.
const fragmentAuth = (() => {
  const { token, error } = parseAuthFragment(window.location.hash);
  if (token || error) {
    history.replaceState(null, "", window.location.pathname);
  }
  return { token: token ?? null, error: error ?? null };
})();

export default function App() {
  const [token, setToken] = useState<string | null>(() => {
    if (fragmentAuth.token) {
      sessionStorage.setItem("kilnworks-token", fragmentAuth.token);
      return fragmentAuth.token;
    }
    return sessionStorage.getItem("kilnworks-token");
  });
  const [ssoError] = useState<string | null>(fragmentAuth.error);
  const [selection, setSelection] = useState<Selection>(emptySelection);
  const [catalog, setCatalog] = useState<Catalog>(emptyCatalog);
  const handleCatalogChange = useCallback((next: Catalog) => setCatalog(next), []);

  // These are passed as props into Sources/Chat, whose data-fetching effects list
  // them in useCallback deps. They MUST be stable references or those effects
  // re-run every render — an unstable handleLogout here previously caused an
  // infinite fetch loop (documents/connectors re-fetched on every render).
  const handleLogin = useCallback((newToken: string) => {
    sessionStorage.setItem("kilnworks-token", newToken);
    setToken(newToken);
  }, []);

  const handleLogout = useCallback(() => {
    sessionStorage.removeItem("kilnworks-token");
    setToken(null);
  }, []);

  if (!token) return <Login onLogin={handleLogin} ssoError={ssoError} />;

  return (
    <div className="app">
      <header className="topbar">
        <Wordmark />
        <button className="ghost" onClick={handleLogout}>
          {strings.app.logout}
        </button>
      </header>
      <div className="body">
        <Sources
          token={token}
          onAuthError={handleLogout}
          selection={selection}
          setSelection={setSelection}
          onCatalogChange={handleCatalogChange}
        />
        <Chat
          token={token}
          onAuthError={handleLogout}
          selection={selection}
          catalog={catalog}
        />
      </div>
      <footer className="footer">
        {strings.app.builtBy}{" "}
        <a href="https://inogen.ai" target="_blank" rel="noreferrer">
          InoGen
        </a>
      </footer>
    </div>
  );
}
