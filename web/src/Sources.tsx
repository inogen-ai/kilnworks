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
import FileIcon from "./FileIcon";
import { fileKind } from "./fileType";
import { metadataRows } from "./metadataView";
import { strings } from "./strings";

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
      else setNotice(strings.sources.couldntLoadDocuments);
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
      else setNotice(strings.sources.couldntLoadConnectors);
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

  async function handleUpload(files: File[]) {
    if (files.length === 0) return;
    setUploading(true);
    // jobId -> filename for every file that enqueued successfully; POST failures
    // (the file never became a document row) are tracked separately for the summary.
    const inFlight = new Map<number, string>();
    const failedUploads: string[] = [];
    try {
      // Phase 1: enqueue each file (one POST + ingestion job apiece).
      for (const file of files) {
        setNotice(strings.sources.uploadingFile(file.name));
        try {
          const jobId = await uploadDocument(token, file);
          inFlight.set(jobId, file.name);
        } catch (err) {
          if (!aliveRef.current) return;
          if (err instanceof ApiError && err.status === 401) {
            onAuthError();
            return;
          }
          failedUploads.push(file.name);
        }
        if (!aliveRef.current) return;
      }
      // Show the freshly-enqueued documents (as "pending") right away.
      await refreshDocuments();
      if (!aliveRef.current) return;

      // Phase 2: poll the batch until every job reaches a terminal state, so the
      // per-row status dots flip pending -> ready/failed as the worker drains them.
      // Individual ingestion failures surface on their own row (red dot + Details);
      // the notice only reports batch progress and files that never uploaded.
      let polls = 0;
      let consecutiveErrors = 0;
      while (inFlight.size > 0) {
        await new Promise((resolve) => setTimeout(resolve, 1500));
        if (!aliveRef.current) return;
        polls += 1;
        if (polls > MAX_POLLS) {
          setNotice(strings.sources.stillProcessing);
          break;
        }
        const finished: number[] = [];
        let tickHadError = false;
        for (const jobId of inFlight.keys()) {
          let job: JobInfo;
          try {
            job = await getJob(token, jobId);
          } catch (err) {
            if (!aliveRef.current) return;
            if (err instanceof ApiError && err.status === 401) {
              onAuthError();
              return;
            }
            tickHadError = true;
            continue; // leave it in flight; retry next tick
          }
          if (job.status === "done" || job.status === "failed") finished.push(jobId);
        }
        for (const jobId of finished) inFlight.delete(jobId);
        if (tickHadError) {
          consecutiveErrors += 1;
          if (consecutiveErrors > MAX_CONSECUTIVE_ERRORS) break;
        } else {
          consecutiveErrors = 0;
        }
        if (!aliveRef.current) return;
        if (inFlight.size > 0 && files.length > 1) {
          setNotice(strings.sources.processingCount(inFlight.size, files.length));
        }
        await refreshDocuments();
        if (!aliveRef.current) return;
      }
      setNotice(
        failedUploads.length > 0
          ? strings.sources.someUploadsFailed(failedUploads.length)
          : null,
      );
      await refreshDocuments();
    } catch (err) {
      if (!aliveRef.current) return;
      if (err instanceof ApiError && err.status === 401) {
        onAuthError();
        return;
      }
      setNotice(err instanceof Error ? err.message : strings.sources.uploadFailed);
    } finally {
      if (aliveRef.current) {
        setUploading(false);
        if (fileRef.current) fileRef.current.value = "";
      }
    }
  }

  async function handleDelete(doc: DocumentInfo) {
    if (!confirm(strings.sources.confirmDelete(doc.title))) return;
    try {
      await deleteDocument(token, doc.id);
      await refreshDocuments();
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) onAuthError();
      else setNotice(err instanceof Error ? err.message : strings.sources.deleteFailed);
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
        <h2>{strings.sources.documents}</h2>
        <div className="sources-actions">
          <button className="ghost small" onClick={selectAllDocuments}>
            {strings.sources.all}
          </button>
          <button className="ghost small" onClick={selectNoDocuments}>
            {strings.sources.none}
          </button>
          <button
            className="ghost"
            disabled={uploading}
            onClick={() => fileRef.current?.click()}
          >
            {uploading ? strings.sources.uploading : strings.sources.upload}
          </button>
        </div>
        <input
          ref={fileRef}
          type="file"
          multiple
          accept=".md,.txt,.pdf,.docx,.html,.htm,.csv,.tsv,.xlsx,.png,.jpg,.jpeg,.gif,.webp,.mp3,.wav,.m4a,.mp4,.mov"
          hidden
          onChange={(e) => {
            if (e.target.files && e.target.files.length > 0) {
              void handleUpload(Array.from(e.target.files));
            }
          }}
        />
      </div>
      {notice && <p className="notice">{notice}</p>}
      <ul className="source-list">
        {documents.map((doc) => {
          const kind = fileKind(doc.source_uri);
          const open = openDetails.has(doc.id);
          const rows = open
            ? metadataRows(doc.metadata ?? {}, doc.created_at, kind.label)
            : [];
          return (
            <li key={doc.id} className="source-row">
              <label className="source-row-main">
                <input
                  type="checkbox"
                  checked={selection.documentIds.has(doc.id)}
                  onChange={() => toggleDocument(doc.id)}
                />
                <span className={`dot ${doc.status}`} />
                <FileIcon category={kind.category} />
                <span className="doc-title">{doc.title}</span>
              </label>
              <div className="source-row-actions">
                <button
                  className="ghost small"
                  title={open ? strings.sources.hideDetailsTitle : strings.sources.detailsTitle}
                  aria-expanded={open}
                  aria-controls={`doc-meta-${doc.id}`}
                  onClick={() => toggleDetails(doc.id)}
                >
                  {open ? strings.sources.hideDetails : strings.sources.details}
                </button>
                <button
                  className="ghost small danger"
                  title={strings.sources.deleteTitle}
                  onClick={() => void handleDelete(doc)}
                >
                  {strings.sources.deleteSymbol}
                </button>
              </div>
              {open && (
                <div className="doc-meta" id={`doc-meta-${doc.id}`}>
                  <dl className="doc-meta-list">
                    {rows.map((row) => (
                      <div className="doc-meta-row" key={row.label}>
                        <dt>{row.label}</dt>
                        <dd>{row.value}</dd>
                      </div>
                    ))}
                  </dl>
                  {doc.status === "failed" && doc.error && (
                    <p className="doc-error">{doc.error}</p>
                  )}
                </div>
              )}
            </li>
          );
        })}
        {documents.length === 0 && <li className="empty">{strings.sources.noDocuments}</li>}
      </ul>

      <div className="sources-head">
        <h2>{strings.sources.connectors}</h2>
        {connectors.length > 0 && (
          <div className="sources-actions">
            <button className="ghost small" onClick={selectAllConnectors}>
              {strings.sources.all}
            </button>
            <button className="ghost small" onClick={selectNoConnectors}>
              {strings.sources.none}
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
                    {connector.needs_login ? strings.sources.needsLogin : strings.sources.down}
                  </span>
                )}
              </label>
            </li>
          );
        })}
        {connectors.length === 0 && (
          <li className="empty">
            <p className="notice">{strings.sources.noConnectors}</p>
            <a
              href="https://github.com/inogen-ai/kilnworks#connectors-beta"
              target="_blank"
              rel="noreferrer"
            >
              {strings.sources.setUpConnectors}
            </a>
          </li>
        )}
      </ul>
    </aside>
  );
}
