import { useCallback, useEffect, useRef, useState, type Dispatch, type SetStateAction } from "react";

import {
  ApiError,
  deleteDocument,
  getJob,
  listConnectors,
  listDocuments,
  uploadDocument,
  type ConnectorInfo,
  type DocumentInfo,
  type JobInfo,
} from "./api";

export type Selection = {
  documentIds: Set<string>;
  connectorNames: Set<string>;
};

export const emptySelection: Selection = {
  documentIds: new Set(),
  connectorNames: new Set(),
};

function isConnectorSelectable(connector: ConnectorInfo): boolean {
  return !connector.needs_login && connector.status !== "down";
}

export type Catalog = {
  // False until the first documents *and* connectors fetch has settled, so
  // callers know when "known" is trustworthy enough to treat a matching
  // selection as "everything".
  loaded: boolean;
  documentIds: string[];
  connectorNames: string[];
};

export const emptyCatalog: Catalog = { loaded: false, documentIds: [], connectorNames: [] };

export default function Sources({
  token,
  onAuthError,
  selection,
  setSelection,
  onCatalogChange,
}: {
  token: string;
  onAuthError: () => void;
  selection: Selection;
  setSelection: Dispatch<SetStateAction<Selection>>;
  onCatalogChange?: (catalog: Catalog) => void;
}) {
  const [documents, setDocuments] = useState<DocumentInfo[]>([]);
  const [connectors, setConnectors] = useState<ConnectorInfo[]>([]);
  const [documentsLoaded, setDocumentsLoaded] = useState(false);
  const [connectorsLoaded, setConnectorsLoaded] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [openDetails, setOpenDetails] = useState<Set<string>>(new Set());
  const fileRef = useRef<HTMLInputElement>(null);
  const aliveRef = useRef(true);
  // Ids/names we've already applied a default-selected decision for, so a later
  // refresh doesn't re-select something the user deliberately unchecked.
  const seenDocumentsRef = useRef<Set<string>>(new Set());
  const seenConnectorsRef = useRef<Set<string>>(new Set());

  useEffect(() => {
    aliveRef.current = true; // re-arm on StrictMode's dev remount, not just first mount
    return () => {
      aliveRef.current = false;
    };
  }, []);

  const refreshDocuments = useCallback(async () => {
    try {
      const docs = await listDocuments(token);
      if (!aliveRef.current) return;
      setDocuments(docs);
      const liveIds = new Set(docs.map((d) => d.id));
      setSelection((current) => {
        const next = new Set([...current.documentIds].filter((id) => liveIds.has(id)));
        for (const doc of docs) {
          if (!seenDocumentsRef.current.has(doc.id)) {
            seenDocumentsRef.current.add(doc.id);
            next.add(doc.id); // default: newly-seen documents start selected
          }
        }
        return { ...current, documentIds: next };
      });
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) onAuthError();
      else setNotice("couldn't load documents");
    } finally {
      if (aliveRef.current) setDocumentsLoaded(true);
    }
  }, [token, onAuthError, setSelection]);

  const refreshConnectors = useCallback(async () => {
    try {
      const conns = await listConnectors(token);
      if (!aliveRef.current) return;
      setConnectors(conns);
      const selectableNames = new Set(conns.filter(isConnectorSelectable).map((c) => c.name));
      setSelection((current) => {
        const next = new Set(
          [...current.connectorNames].filter((n) => selectableNames.has(n)),
        );
        for (const c of conns) {
          if (isConnectorSelectable(c) && !seenConnectorsRef.current.has(c.name)) {
            seenConnectorsRef.current.add(c.name);
            next.add(c.name); // default: newly-seen connectors start selected
          }
        }
        return { ...current, connectorNames: next };
      });
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) onAuthError();
      else setNotice("couldn't load connectors");
    } finally {
      if (aliveRef.current) setConnectorsLoaded(true);
    }
  }, [token, onAuthError, setSelection]);

  useEffect(() => {
    void refreshDocuments();
    void refreshConnectors();
  }, [refreshDocuments, refreshConnectors]);

  useEffect(() => {
    onCatalogChange?.({
      loaded: documentsLoaded && connectorsLoaded,
      documentIds: documents.map((d) => d.id),
      connectorNames: connectors.filter(isConnectorSelectable).map((c) => c.name),
    });
  }, [documents, connectors, documentsLoaded, connectorsLoaded, onCatalogChange]);

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
      await refreshDocuments();
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

  async function handleDelete(doc: DocumentInfo) {
    if (!confirm(`Delete "${doc.title}"? This cannot be undone.`)) return;
    try {
      await deleteDocument(token, doc.id);
      await refreshDocuments();
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) onAuthError();
      else setNotice(err instanceof Error ? err.message : "delete failed");
    }
  }

  function toggleDetails(id: string) {
    setOpenDetails((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleDocument(id: string) {
    setSelection((current) => {
      const next = new Set(current.documentIds);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return { ...current, documentIds: next };
    });
  }

  function toggleConnector(name: string) {
    setSelection((current) => {
      const next = new Set(current.connectorNames);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return { ...current, connectorNames: next };
    });
  }

  function selectAllDocuments() {
    setSelection((current) => ({
      ...current,
      documentIds: new Set(documents.map((d) => d.id)),
    }));
  }

  function selectNoDocuments() {
    setSelection((current) => ({ ...current, documentIds: new Set() }));
  }

  function selectAllConnectors() {
    setSelection((current) => ({
      ...current,
      connectorNames: new Set(connectors.filter(isConnectorSelectable).map((c) => c.name)),
    }));
  }

  function selectNoConnectors() {
    setSelection((current) => ({ ...current, connectorNames: new Set() }));
  }

  return (
    <aside className="sources">
      <div className="sources-head">
        <h2>Documents</h2>
        <div className="sources-actions">
          <button className="ghost small" onClick={selectAllDocuments}>
            All
          </button>
          <button className="ghost small" onClick={selectNoDocuments}>
            None
          </button>
          <button
            className="ghost"
            disabled={uploading}
            onClick={() => fileRef.current?.click()}
          >
            {uploading ? "…" : "+ Upload"}
          </button>
        </div>
        <input
          ref={fileRef}
          type="file"
          accept=".md,.txt,.pdf,.docx,.html,.htm,.csv,.tsv,.xlsx,.png,.jpg,.jpeg,.gif,.webp,.mp3,.wav,.m4a,.mp4,.mov"
          hidden
          onChange={(e) => e.target.files?.[0] && void handleUpload(e.target.files[0])}
        />
      </div>
      {notice && <p className="notice">{notice}</p>}
      <ul className="source-list">
        {documents.map((doc) => (
          <li key={doc.id} className="source-row">
            <label className="source-row-main">
              <input
                type="checkbox"
                checked={selection.documentIds.has(doc.id)}
                onChange={() => toggleDocument(doc.id)}
              />
              <span className={`dot ${doc.status}`} />
              <span className="doc-title">{doc.title}</span>
            </label>
            <div className="source-row-actions">
              {doc.status === "failed" && doc.error && (
                <button className="ghost small" onClick={() => toggleDetails(doc.id)}>
                  Details
                </button>
              )}
              <button
                className="ghost small danger"
                title="Delete"
                onClick={() => void handleDelete(doc)}
              >
                ×
              </button>
            </div>
            {doc.status === "failed" && doc.error && openDetails.has(doc.id) && (
              <p className="doc-error">{doc.error}</p>
            )}
          </li>
        ))}
        {documents.length === 0 && <li className="empty">No documents yet.</li>}
      </ul>

      <div className="sources-head">
        <h2>Connectors</h2>
        {connectors.length > 0 && (
          <div className="sources-actions">
            <button className="ghost small" onClick={selectAllConnectors}>
              All
            </button>
            <button className="ghost small" onClick={selectNoConnectors}>
              None
            </button>
          </div>
        )}
      </div>
      <ul className="source-list">
        {connectors.map((connector) => {
          const disabled = !isConnectorSelectable(connector);
          return (
            <li key={connector.name} className="source-row">
              <label className={`source-row-main ${disabled ? "disabled" : ""}`}>
                <input
                  type="checkbox"
                  checked={!disabled && selection.connectorNames.has(connector.name)}
                  disabled={disabled}
                  onChange={() => toggleConnector(connector.name)}
                />
                <span className={`dot ${connector.status}`} />
                <span className="doc-title">{connector.name}</span>
                {disabled && (
                  <span className="connector-note">
                    {connector.needs_login ? "needs login" : "down"}
                  </span>
                )}
              </label>
            </li>
          );
        })}
        {connectors.length === 0 && (
          <li className="empty">
            <p className="notice">
              Connectors let you federate live queries to systems like Salesforce,
              Microsoft&nbsp;365, ServiceNow, and HubSpot.
            </p>
            <a
              href="https://github.com/inogen-ai/kilnworks#connectors-beta"
              target="_blank"
              rel="noreferrer"
            >
              Set up connectors →
            </a>
          </li>
        )}
      </ul>
    </aside>
  );
}
