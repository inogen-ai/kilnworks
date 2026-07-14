import { readSseStream, type SseEvent } from "./sse";

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
  ) {
    super(message);
  }
}

export type Citation = {
  index: number;
  chunk_id: string;
  source_uri: string;
  title: string;
  heading_path: string[];
  locator: string | null;
};

export type Answer = { text: string; citations: Citation[]; model: string };

export type DocumentInfo = {
  id: string;
  source_uri: string;
  title: string;
  status: string;
  error: string | null;
};

export type JobInfo = {
  id: number;
  kind: string;
  status: string;
  attempts: number;
  error: string | null;
};

export type ConnectorInfo = {
  name: string;
  status: string;
  needs_login: boolean;
};

function authHeaders(token: string): Record<string, string> {
  return { authorization: `Bearer ${token}` };
}

export function errorDetailToMessage(detail: unknown, status: number): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const parts = detail
      .map((item) =>
        item && typeof item === "object" && "msg" in item
          ? String((item as { msg: unknown }).msg)
          : JSON.stringify(item),
      )
      .filter(Boolean);
    if (parts.length > 0) return parts.join("; ");
  }
  return `request failed: ${status}`;
}

async function checked(response: Response): Promise<Response> {
  if (!response.ok) {
    const detail = await response.json().then((body) => body.detail).catch(() => null);
    throw new ApiError(errorDetailToMessage(detail, response.status), response.status);
  }
  return response;
}

export function parseAuthFragment(hash: string): { token?: string; error?: string } {
  const params = new URLSearchParams(hash.startsWith("#") ? hash.slice(1) : hash);
  const token = params.get("token");
  const error = params.get("sso_error");
  const result: { token?: string; error?: string } = {};
  if (token) result.token = token;
  if (error) result.error = error;
  return result;
}

export async function getAuthConfig(): Promise<{ sso_enabled: boolean }> {
  const response = await checked(await fetch("/auth/config"));
  return response.json();
}

export async function login(email: string, password: string): Promise<string> {
  const response = await checked(
    await fetch("/auth/token", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ email, password }),
    }),
  );
  return (await response.json()).access_token as string;
}

export async function listDocuments(token: string): Promise<DocumentInfo[]> {
  const response = await checked(
    await fetch("/documents", { headers: authHeaders(token) }),
  );
  return response.json();
}

export async function uploadDocument(token: string, file: File): Promise<number> {
  const body = new FormData();
  body.append("file", file);
  const response = await checked(
    await fetch("/documents", { method: "POST", headers: authHeaders(token), body }),
  );
  return (await response.json()).job_id as number;
}

export async function getJob(token: string, jobId: number): Promise<JobInfo> {
  const response = await checked(
    await fetch(`/jobs/${jobId}`, { headers: authHeaders(token) }),
  );
  return response.json();
}

export async function deleteDocument(token: string, id: string): Promise<void> {
  await checked(
    await fetch(`/documents/${id}`, { method: "DELETE", headers: authHeaders(token) }),
  );
}

export async function listConnectors(token: string): Promise<ConnectorInfo[]> {
  const response = await checked(
    await fetch("/connectors", { headers: authHeaders(token) }),
  );
  return response.json();
}

export async function* askStream(
  token: string,
  question: string,
  signal?: AbortSignal,
  sourceIds?: string[],
  connectors?: string[],
): AsyncGenerator<SseEvent> {
  const body: Record<string, unknown> = { question };
  if (sourceIds !== undefined) body.source_ids = sourceIds;
  if (connectors !== undefined) body.connectors = connectors;
  const response = await checked(
    await fetch("/ask/stream", {
      method: "POST",
      headers: { ...authHeaders(token), "content-type": "application/json" },
      body: JSON.stringify(body),
      signal,
    }),
  );
  if (!response.body) throw new Error("streaming not supported");
  yield* readSseStream(response.body);
}
