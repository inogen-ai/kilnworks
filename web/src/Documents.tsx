import { useCallback, useEffect, useRef, useState } from "react";

import {
  ApiError,
  getJob,
  listDocuments,
  uploadDocument,
  type DocumentInfo,
  type JobInfo,
} from "./api";

export default function Documents({
  token,
  onAuthError,
}: {
  token: string;
  onAuthError: () => void;
}) {
  const [documents, setDocuments] = useState<DocumentInfo[]>([]);
  const [uploading, setUploading] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const aliveRef = useRef(true);

  useEffect(() => {
    aliveRef.current = true; // re-arm on StrictMode's dev remount, not just first mount
    return () => {
      aliveRef.current = false;
    };
  }, []);

  const refresh = useCallback(async () => {
    try {
      setDocuments(await listDocuments(token));
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) onAuthError();
      else setNotice("couldn't load documents");
    }
  }, [token, onAuthError]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const MAX_POLLS = 200;
  const MAX_CONSECUTIVE_ERRORS = 4;

  async function handleUpload(file: File) {
    setUploading(true);
    setNotice(`uploading ${file.name}…`);
    try {
      const jobId = await uploadDocument(token, file);
      if (!aliveRef.current) return;
      let status = "queued";
      let polls = 0;
      let consecutiveErrors = 0;
      while (status === "queued" || status === "running") {
        await new Promise((resolve) => setTimeout(resolve, 1500));
        if (!aliveRef.current) return;
        polls += 1;
        if (polls > MAX_POLLS) {
          setNotice("still processing — refresh to check status");
          break;
        }
        let job: JobInfo;
        try {
          job = await getJob(token, jobId);
        } catch (err) {
          if (!aliveRef.current) return;
          if (err instanceof ApiError && err.status === 401) {
            onAuthError();
            return;
          }
          consecutiveErrors += 1;
          if (consecutiveErrors > MAX_CONSECUTIVE_ERRORS) {
            setNotice(err instanceof Error ? err.message : "upload failed");
            break;
          }
          continue;
        }
        if (!aliveRef.current) return;
        consecutiveErrors = 0;
        status = job.status;
        if (status === "failed") setNotice(job.error ?? "ingestion failed");
      }
      if (!aliveRef.current) return;
      if (status === "done") setNotice(null);
      await refresh();
    } catch (err) {
      if (!aliveRef.current) return;
      if (err instanceof ApiError && err.status === 401) {
        onAuthError();
        return;
      }
      setNotice(err instanceof Error ? err.message : "upload failed");
    } finally {
      if (aliveRef.current) {
        setUploading(false);
        if (fileRef.current) fileRef.current.value = "";
      }
    }
  }

  return (
    <aside className="documents">
      <div className="documents-head">
        <h2>Documents</h2>
        <button
          className="ghost"
          disabled={uploading}
          onClick={() => fileRef.current?.click()}
        >
          {uploading ? "…" : "+ Upload"}
        </button>
        <input
          ref={fileRef}
          type="file"
          accept=".md,.txt,.pdf,.docx,.html,.htm"
          hidden
          onChange={(e) => e.target.files?.[0] && void handleUpload(e.target.files[0])}
        />
      </div>
      {notice && <p className="notice">{notice}</p>}
      <ul>
        {documents.map((doc) => (
          <li key={doc.id} title={doc.error ?? undefined}>
            <span className={`dot ${doc.status}`} />
            <span className="doc-title">{doc.title}</span>
            {doc.error && <span className="doc-error">{doc.error}</span>}
          </li>
        ))}
        {documents.length === 0 && <li className="empty">No documents yet.</li>}
      </ul>
    </aside>
  );
}
