import { useEffect, useState, type FormEvent } from "react";

import { getAuthConfig, login } from "./api";
import Wordmark from "./Wordmark";

export default function Login({
  onLogin,
  ssoError,
}: {
  onLogin: (token: string) => void;
  ssoError?: string | null;
}) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [ssoEnabled, setSsoEnabled] = useState(false);

  useEffect(() => {
    getAuthConfig()
      .then((config) => setSsoEnabled(config.sso_enabled))
      .catch(() => setSsoEnabled(false));
  }, []);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      onLogin(await login(email, password));
    } catch (err) {
      setError(err instanceof Error ? err.message : "login failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-wrap">
      <form className="login-card" onSubmit={handleSubmit}>
        <Wordmark as="h1" />
        <p className="tagline">Ask your documents.</p>
        <input
          type="email"
          placeholder="email"
          aria-label="email"
          autoComplete="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          required
        />
        <input
          type="password"
          placeholder="password"
          aria-label="password"
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
        />
        {error && <p className="error">{error}</p>}
        {ssoError && <p className="error">{ssoError}</p>}
        <button type="submit" disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
        {ssoEnabled && (
          <button
            type="button"
            className="ghost"
            onClick={() => {
              window.location.href = "/auth/oidc/login";
            }}
          >
            Sign in with SSO
          </button>
        )}
        <p className="hint">
          No account? Create one: <code>kilnworks create-user you@example.com</code> (or{" "}
          <code>docker compose exec api kilnworks create-user you@example.com</code>)
        </p>
      </form>
    </div>
  );
}
