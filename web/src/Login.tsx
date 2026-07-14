import { useEffect, useState, type FormEvent } from "react";

import { getAuthConfig, login } from "./api";
import { strings } from "./strings";
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
      setError(err instanceof Error ? err.message : strings.login.loginFailed);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-wrap">
      <form className="login-card" onSubmit={handleSubmit}>
        <Wordmark as="h1" />
        <p className="tagline">{strings.login.tagline}</p>
        <input
          type="email"
          placeholder={strings.login.email}
          aria-label={strings.login.email}
          autoComplete="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          required
        />
        <input
          type="password"
          placeholder={strings.login.password}
          aria-label={strings.login.password}
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
        />
        {error && <p className="error">{error}</p>}
        {ssoError && <p className="error">{ssoError}</p>}
        <button type="submit" disabled={busy}>
          {busy ? strings.login.signingIn : strings.login.signIn}
        </button>
        {ssoEnabled && (
          <button
            type="button"
            className="ghost"
            onClick={() => {
              window.location.href = "/auth/oidc/login";
            }}
          >
            {strings.login.ssoSignIn}
          </button>
        )}
        <p className="hint">
          {strings.login.hintIntro}
          <code>{strings.login.hintCommand}</code>
          {strings.login.hintOr}
          <code>{strings.login.hintDockerCommand}</code>
          {strings.login.hintClose}
        </p>
      </form>
    </div>
  );
}
