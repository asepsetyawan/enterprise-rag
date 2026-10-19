import React, { useCallback, useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  MessageSquare,
  Files,
  Activity,
  ShieldCheck,
  FlaskConical,
  LogOut,
  ArrowUpRight,
  ArrowUp,
  Upload,
  Search,
  Check,
  CheckCircle2,
  LockKeyhole,
  ChevronRight,
  ChevronDown,
  FileText,
  RefreshCw,
  X,
  Plus,
  ArrowRight,
  ThumbsUp,
  CircleAlert,
  FolderOpen,
  LoaderCircle,
} from "lucide-react";
import "@fontsource/ibm-plex-sans/latin-400.css";
import "@fontsource/ibm-plex-sans/latin-500.css";
import "@fontsource/ibm-plex-sans/latin-600.css";
import "@fontsource/ibm-plex-sans/latin-700.css";
import "./style.css";

type Me = {
  user_id: string;
  name: string;
  csrf: string;
  tenants: { id: string; name: string; role: string }[];
};
type Doc = {
  id: string;
  title: string;
  filename: string;
  status: string;
  owner_id: string;
  acl_users: string[];
  acl_groups: string[];
  acl_revision: number;
  retention_until: string | null;
  legal_hold: boolean;
};
type Answer = {
  query_id: string;
  answer: string;
  abstained: boolean;
  citations: { source_id: string; quote: string }[];
  sources: {
    source_id: string;
    document_id: string;
    version_id: string;
    title: string;
    headings?: string[];
    pages?: number[];
  }[];
};
type Row = Record<string, unknown>;
const statusNames: Record<string, string> = {
  ready: "Ready",
  queued: "Queued",
  processing: "Processing",
  failed: "Failed",
  dead_letter: "Needs review",
  done: "Done",
  completed: "Done",
};
const roleNames: Record<string, string> = {
  tenant_admin: "Administrator",
  editor: "Editor",
  viewer: "Reader",
  auditor: "Auditor",
};
const suggestions = [
  "What is the domestic travel hotel allowance?",
  "Which documents are required for reimbursement?",
  "When is the travel claim deadline?",
];
function Brand() {
  return (
    <div className="brand">
      <span className="brand-mark" aria-hidden="true">
        <span />
        <span />
        <span />
      </span>
      <div>
        <strong>CNF</strong>
        <span>Knowledge</span>
      </div>
    </div>
  );
}
function Status({ value }: { value: string }) {
  return (
    <span className={"status " + value}>
      {["ready", "done", "completed"].includes(value) ? (
        <CheckCircle2 size={13} />
      ) : value === "processing" ? (
        <LoaderCircle size={13} className="spin" />
      ) : (
        <span className="status-dot" />
      )}
      {statusNames[value] || value}
    </span>
  );
}
function EvaluationMetrics({ value }: { value: unknown }) {
  const metrics =
    value && typeof value === "object" && !Array.isArray(value)
      ? Object.entries(value)
      : [];
  const labels: Record<string, string> = {
    cases: "Questions",
    failed: "Failed",
    pass_rate: "Pass rate",
    p95_latency_ms: "p95 latency",
  };
  if (!metrics.length) return <span>No metrics yet</span>;
  return (
    <dl className="metric-list">
      {metrics.map(([key, value]) => (
        <div key={key}>
          <dt>{labels[key] || key.replaceAll("_", " ")}</dt>
          <dd>
            {typeof value === "number" && key === "pass_rate"
              ? `${(value * 100).toLocaleString("en-US", { maximumFractionDigits: 1 })}%`
              : typeof value === "number" && key === "p95_latency_ms"
                ? `${(value / 1000).toLocaleString("en-US", { maximumFractionDigits: 1 })} s`
                : typeof value === "object"
                  ? JSON.stringify(value)
                  : String(value)}
          </dd>
        </div>
      ))}
    </dl>
  );
}
function Portal() {
  const [me, setMe] = useState<Me | null>(null),
    [tenant, setTenant] = useState(""),
    [tab, setTab] = useState("chat");
  const [authReady, setAuthReady] = useState(false);
  const modalRef = useRef<HTMLDialogElement>(null);
  const [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [busy, setBusy] = useState(false);
  const [docs, setDocs] = useState<Doc[]>([]),
    [rows, setRows] = useState<Row[]>([]),
    [question, setQuestion] = useState(""),
    [selected, setSelected] = useState<string[]>([]),
    [answer, setAnswer] = useState<Answer | null>(null);
  const [dev, setDev] = useState(false),
    [username, setUsername] = useState("alice"),
    [password, setPassword] = useState(""),
    [filter, setFilter] = useState("");
  const [editing, setEditing] = useState<Doc | null>(null),
    [aclUsers, setAclUsers] = useState<string[]>([]),
    [all, setAll] = useState(false),
    [hold, setHold] = useState(false),
    [retention, setRetention] = useState(""),
    [members, setMembers] = useState<
      { user_id: string; name: string; role: string }[]
    >([]);

  useEffect(() => {
    const dialog = modalRef.current;
    if (editing && dialog) {
      dialog.showModal();
      return () => dialog.close();
    }
  }, [editing]);
  const api = useCallback(
    async (path: string, init: RequestInit = {}) => {
      const headers = new Headers(init.headers);
      if (tenant) headers.set("X-Tenant-ID", tenant);
      if (me?.csrf) headers.set("X-CSRF-Token", me.csrf);
      if (init.body && !(init.body instanceof FormData))
        headers.set("Content-Type", "application/json");
      const response = await fetch(path, {
        ...init,
        headers,
        credentials: "same-origin",
      });
      if (!response.ok) {
        let data;
        try {
          data = await response.json();
        } catch {
          data = { detail: "Service unavailable" };
        }
        if (response.status === 401) setMe(null);
        throw new Error(
          typeof data.detail === "string" ? data.detail : "Check your input",
        );
      }
      return response.json();
    },
    [tenant, me?.csrf],
  );
  const loadMe = async () => {
    try {
      const response = await fetch("/api/me");
      if (response.ok) {
        const data: Me = await response.json();
        setMe(data);
        setTenant(data.tenants[0]?.id || "");
      }
    } catch {
      setError("Cannot reach the service");
    } finally {
      setAuthReady(true);
    }
  };
  useEffect(() => {
    void loadMe();
  }, []);
  const load = useCallback(async () => {
    if (!me || !tenant) return;
    try {
      const documents = await api("/api/documents");
      setDocs(documents);
      if (["jobs", "audit", "evaluations"].includes(tab))
        setRows(await api("/api/" + tab));
    } catch (e) {
      setError((e as Error).message);
    }
  }, [api, me, tenant, tab]);
  useEffect(() => {
    setRows([]);
    setAnswer(null);
    setSelected([]);
    void load();
    const timer = setInterval(() => void load(), 10000);
    return () => clearInterval(timer);
  }, [load]);
  async function action(fn: () => Promise<void>) {
    setError("");
    setNotice("");
    setBusy(true);
    try {
      await fn();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const role = me?.tenants.find((t) => t.id === tenant)?.role;
  const editor = role === "editor" || role === "tenant_admin",
    admin = role === "tenant_admin",
    auditor = admin || role === "auditor";
  const nav = [
    { id: "chat", label: "Ask", icon: MessageSquare },
    { id: "documents", label: "Documents", icon: Files },
    ...(editor ? [{ id: "jobs", label: "Processing", icon: Activity }] : []),
    ...(auditor
      ? [
          { id: "audit", label: "Audit trail", icon: ShieldCheck },
          { id: "evaluations", label: "Evaluation", icon: FlaskConical },
        ]
      : []),
  ];
  async function uploadFile(file: File) {
    const form = new FormData();
    form.append("file", file);
    await api("/api/documents", { method: "POST", body: form });
    setNotice("Document queued. Status updates automatically.");
    await load();
  }
  async function ask() {
    setAnswer(null);
    const data = await api("/api/chat", {
      method: "POST",
      body: JSON.stringify({ question, document_ids: selected, mode: "mix" }),
    });
    setAnswer(data);
  }
  async function permissions(doc: Doc) {
    setMembers(await api("/api/members"));
    setEditing(doc);
    setAclUsers(doc.acl_users);
    setAll(doc.acl_groups.includes("all"));
    setHold(doc.legal_hold);
    setRetention(doc.retention_until ? doc.retention_until.slice(0, 10) : "");
  }
  async function savePermissions() {
    if (!editing) return;
    await api(`/api/documents/${editing.id}/permissions`, {
      method: "PUT",
      body: JSON.stringify({
        users: aclUsers,
        groups: all ? ["all"] : editing.acl_groups.filter((g) => g !== "all"),
        expected_revision: editing.acl_revision,
        legal_hold: admin ? hold : editing.legal_hold,
        retention_until: admin
          ? retention
            ? new Date(retention + "T23:59:59").toISOString()
            : null
          : editing.retention_until,
      }),
    });
    setEditing(null);
    setNotice(
      "Permissions updated. The next question uses the latest access.",
    );
    await load();
  }
  const readyDocs = docs.filter((d) => d.status === "ready");
  const filteredDocs = docs.filter((d) =>
    d.title.toLowerCase().includes(filter.toLowerCase()),
  );
  const tenantName =
    me?.tenants.find((t) => t.id === tenant)?.name || "Workspace";
  const sectionName = nav.find((n) => n.id === tab)?.label;
  async function downloadSource(source: Answer["sources"][number]) {
    const response = await fetch(
      `/api/documents/${source.document_id}/download?version_id=${source.version_id}`,
      { headers: { "X-Tenant-ID": tenant } },
    );
    if (!response.ok) throw new Error("Source unavailable");
    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement("a");
    link.href = url;
    link.download = source.title;
    link.click();
    URL.revokeObjectURL(url);
  }
  const alerts = (
    <>
      {error && (
        <div role="alert" className="alert error">
          <CircleAlert size={18} />
          <span>{error}</span>
          <button
            aria-label="Dismiss error"
            onClick={() => setError("")}
          >
            <X size={16} />
          </button>
        </div>
      )}
      {notice && (
        <div role="status" className="alert notice">
          <CheckCircle2 size={18} />
          <span>{notice}</span>
          <button
            aria-label="Dismiss notice"
            onClick={() => setNotice("")}
          >
            <X size={16} />
          </button>
        </div>
      )}
    </>
  );
  if (!authReady)
    return (
      <div className="startup">
        <Brand />
        <LoaderCircle size={24} className="spin" />
        <p>Opening the knowledge workspace…</p>
      </div>
    );
  if (!me)
    return (
      <div className="login">
        <section className="login-story">
          <Brand />
          <div className="login-intro">
            <span className="section-kicker">
              <span />
              Company knowledge workspace
            </span>
            <h1>
              Shared knowledge.
              <br />
              <span>Clearer next steps.</span>
            </h1>
            <p>
              From documents to answers you can trace.
              <br />
              Find the context you need to do the work.
            </p>
            <div className="knowledge-flow" aria-hidden="true">
              <div className="flow-document">
                <FileText size={24} />
                <span>Policies & procedures</span>
                <i />
                <i />
                <i />
              </div>
              <div className="flow-connector">
                <span />
                <ArrowRight size={20} />
              </div>
              <div className="flow-answer">
                <span className="flow-answer-label">
                  <CheckCircle2 size={16} />
                  Grounded in sources
                </span>
                <div />
                <div />
                <div />
                <span className="flow-citation">
                  1 <span>Document citation</span>
                </span>
              </div>
            </div>
          </div>
          <div className="login-foot">
            <ShieldCheck size={16} />
            Private infrastructure. Access follows permissions.
          </div>
        </section>
        <section className="login-panel">
          <div className="login-form">
            <span className="section-kicker">Employee portal</span>
            <h2>
              Welcome
              <br />
              to CNF Knowledge.
            </h2>
            <p>
              Use your company account to sign in
              <br className="desktop-break" /> to the workspace.
            </p>
            {alerts}
            <a className="primary sso" href="/auth/login">
              Sign in with SSO
              <ArrowRight size={18} />
            </a>
            <div className="login-security">
              <LockKeyhole size={17} />
              <span>
                Documents follow your workspace
                <br />
                and your access permissions.
              </span>
            </div>
            <div className="dev-access">
              <button
                className="text-button"
                aria-expanded={dev}
                onClick={() => setDev(!dev)}
              >
                Local development access
                <ChevronDown size={14} className={dev ? "rotated" : ""} />
              </button>
              {dev && (
                <form
                  onSubmit={(e) => {
                    e.preventDefault();
                    void action(async () => {
                      await api("/auth/dev-login", {
                        method: "POST",
                        body: JSON.stringify({ username, password }),
                      });
                      setPassword("");
                      await loadMe();
                    });
                  }}
                >
                  <label>
                    Demo account
                    <select
                      value={username}
                      onChange={(e) => setUsername(e.target.value)}
                    >
                      <option>alice</option>
                      <option>bob</option>
                      <option>mallory</option>
                    </select>
                  </label>
                  <label>
                    Development password
                    <input
                      type="password"
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      autoComplete="current-password"
                      required
                    />
                  </label>
                  <button className="secondary" disabled={busy}>
                    Sign in locally
                  </button>
                  <small>
                    The password is in the local file .secrets/dev_password.
                  </small>
                </form>
              )}
            </div>
          </div>
          <span className="login-panel-foot">
            CNF Global · Internal knowledge workspace
          </span>
        </section>
      </div>
    );
  return (
    <div className="layout">
      <a className="skip-link" href="#workspace-content">
        Skip navigation
      </a>
      <aside className="sidebar">
        <Brand />
        <div className="workspace-picker">
          <label htmlFor="tenant">Active workspace</label>
          <div className="select-wrap">
            <span className="workspace-monogram">{tenantName[0]}</span>
            <select
              id="tenant"
              disabled={busy}
              value={tenant}
              onChange={(e) => {
                setTenant(e.target.value);
                setQuestion("");
                setFilter("");
              }}
            >
              {me.tenants.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name}
                </option>
              ))}
            </select>
            <ChevronDown size={14} />
          </div>
        </div>
        <nav aria-label="Main navigation">
          {nav.map((n) => (
            <button
              key={n.id}
              disabled={busy}
              aria-current={tab === n.id ? "page" : undefined}
              className={tab === n.id ? "active" : ""}
              onClick={() => {
                setTab(n.id);
                setError("");
                setNotice("");
              }}
            >
              <n.icon size={19} />
              <span>{n.label}</span>
              {tab === n.id && <span className="nav-indicator" />}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="private-note">
            <ShieldCheck size={18} />
            <div>
              <b>Protected knowledge</b>
              <span>Only sources you are allowed to read.</span>
            </div>
          </div>
          <div className="profile">
            <div className="avatar">{me.name.slice(0, 2).toUpperCase()}</div>
            <div>
              <b>{me.name}</b>
              <small>{roleNames[role || ""] || role}</small>
            </div>
            <button
              title="Sign out"
              aria-label="Sign out"
              disabled={busy}
              onClick={() =>
                void action(async () => {
                  await api("/auth/logout", { method: "POST" });
                  setMe(null);
                })
              }
            >
              <LogOut size={17} />
            </button>
          </div>
        </div>
      </aside>
      <main id="workspace-content">
        <header className="topbar">
          <div className="breadcrumb">
            <span>{tenantName}</span>
            <ChevronRight size={14} />
            <strong>{sectionName}</strong>
          </div>
          <div className="topbar-actions">
            <span className="environment">
              <span />
              Local model <span className="environment-divider">/</span>{" "}
              Internal
            </span>
            <button
              className="mobile-logout icon-button"
              title="Sign out"
              aria-label="Sign out"
              disabled={busy}
              onClick={() =>
                void action(async () => {
                  await api("/auth/logout", { method: "POST" });
                  setMe(null);
                })
              }
            >
              <LogOut size={16} />
            </button>
          </div>
        </header>
        <div className={"page " + (tab === "chat" ? "chat-page" : "")}>
          {alerts}
          {!tenant && (
            <div className="empty-state">
              <FolderOpen size={30} />
              <h2>No workspace yet</h2>
              <p>Contact an administrator to get workspace access.</p>
            </div>
          )}
          {tab === "chat" && (
            <div className="knowledge-layout">
              <section className="conversation">
                <div className="chat-heading">
                  <span className="section-kicker">
                    <span />
                    A place to find answers
                  </span>
                  <h1>
                    Shared knowledge.
                    <br />
                    <span>What do you want to know?</span>
                  </h1>
                  <p>
                    Search company knowledge. Get answers with sources you can
                    check.
                  </p>
                </div>
                <form
                  className={"composer " + (busy ? "is-busy" : "")}
                  onSubmit={(e) => {
                    e.preventDefault();
                    void action(ask);
                  }}
                >
                  <label className="sr-only" htmlFor="question">
                    Your question
                  </label>
                  <textarea
                    id="question"
                    disabled={busy}
                    value={question}
                    onChange={(e) => setQuestion(e.target.value)}
                    placeholder="Ask about a policy, a procedure, or something you need…"
                    maxLength={8000}
                    required
                    minLength={2}
                  />
                  <div className="composer-footer">
                    <span className="composer-scope">
                      <Files size={16} />
                      {selected.length
                        ? `${selected.length} documents selected`
                        : "Automatic source selection"}
                    </span>
                    <button
                      className="primary ask-button"
                      aria-label={busy ? "Searching for an answer" : "Search for an answer"}
                      disabled={busy || !tenant}
                    >
                      {busy ? (
                        <LoaderCircle size={18} className="spin" />
                      ) : (
                        <ArrowUp size={19} />
                      )}
                      <span>{busy ? "Searching…" : "Ask"}</span>
                    </button>
                  </div>
                </form>
                <div className="composer-note">
                  <LockKeyhole size={13} />
                  Document access is checked every time you ask.
                </div>
                {answer ? (
                  <article className="answer">
                    <div className="answer-heading">
                      <span
                        className={
                          "answer-icon " + (answer.abstained ? "abstained" : "")
                        }
                      >
                        {answer.abstained ? (
                          <CircleAlert size={19} />
                        ) : (
                          <MessageSquare size={19} />
                        )}
                      </span>
                      <div>
                        <h2>
                          {answer.abstained
                            ? "Not enough information"
                            : "Answer from your knowledge"}
                        </h2>
                        <span>{answer.citations.length} source citations</span>
                      </div>
                    </div>
                    <p className="answer-text">{answer.answer}</p>
                    {answer.citations.length > 0 && (
                      <div className="evidence">
                        <h3>Sources & citations</h3>
                        {answer.citations.map((c, i) => {
                          const s = answer.sources.find(
                            (source) => source.source_id === c.source_id,
                          );
                          return (
                            <div
                              className="citation"
                              key={`${c.source_id}-${i}`}
                            >
                              <span className="citation-number">{i + 1}</span>
                              <div>
                                <b>{s?.title || c.source_id}</b>
                                {s && (s.headings?.length || s.pages?.length) ? (
                                  <p>{[s.headings?.join(" → "), s.pages?.length ? `Page ${s.pages.join(", ")}` : ""].filter(Boolean).join(" · ")}</p>
                                ) : null}
                                <blockquote>{c.quote}</blockquote>
                                {s && (
                                  <a
                                    href={`/api/documents/${s.document_id}/download?version_id=${s.version_id}`}
                                    onClick={(e) => {
                                      e.preventDefault();
                                      void action(() => downloadSource(s));
                                    }}
                                  >
                                    Download source
                                    <ArrowUpRight size={13} />
                                  </a>
                                )}
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    )}
                    <div className="feedback">
                      <span>Was this answer helpful?</span>
                      <div>
                        {[
                          ["helpful", "Helpful"],
                          ["incorrect", "Not accurate"],
                          ["unsupported", "Sources don’t fit"],
                        ].map(([rating, label]) => (
                          <button
                            key={rating}
                            disabled={busy}
                            onClick={() =>
                              void action(async () => {
                                await api(
                                  `/api/queries/${answer.query_id}/feedback`,
                                  {
                                    method: "POST",
                                    body: JSON.stringify({ rating }),
                                  },
                                );
                                setNotice(
                                  "Feedback saved for quality evaluation.",
                                );
                              })
                            }
                          >
                            {rating === "helpful" && <ThumbsUp size={13} />}{" "}
                            {label}
                          </button>
                        ))}
                      </div>
                    </div>
                  </article>
                ) : busy ? (
                  <div className="searching" role="status">
                    <LoaderCircle className="spin" size={23} />
                    <div>
                      <b>Searching your sources</b>
                      <p>Composing an answer with the relevant citations…</p>
                    </div>
                  </div>
                ) : (
                  <div className="discovery">
                    <div className="discovery-title">
                      <span>Start with one of these</span>
                      <span>Example questions</span>
                    </div>
                    {suggestions.map((suggestion, i) => (
                      <button
                        className="suggestion"
                        key={suggestion}
                        disabled={busy}
                        onClick={() => {
                          setQuestion(suggestion);
                          document.getElementById("question")?.focus();
                        }}
                      >
                        <span className="suggestion-index">0{i + 1}</span>
                        <span>{suggestion}</span>
                        <Plus size={17} />
                      </button>
                    ))}
                    <div className="reading-note">
                      <span className="reading-symbol">
                        <FileText size={22} />
                        <Check size={11} />
                      </span>
                      <p>
                        <b>Every answer has context.</b>
                        <br />
                        Check the citations and source documents before you
                        decide.
                      </p>
                    </div>
                  </div>
                )}
              </section>
              <details className="source-panel" open>
                <summary>
                  <span>
                    <Files size={19} />
                    Knowledge sources
                  </span>
                  <span className="source-total">{readyDocs.length}</span>
                  <ChevronDown className="source-chevron" size={16} />
                </summary>
                <div className="source-content">
                  <p>
                    Select sources to focus the answer, or leave them empty to
                    automatically find relevant documents you can access.
                  </p>
                  <div className="source-toolbar">
                    <span>
                      {selected.length
                        ? `${selected.length} selected`
                        : "Automatic selection"}
                    </span>
                    {selected.length > 0 && (
                      <button
                        className="text-button"
                        disabled={busy}
                        onClick={() => setSelected([])}
                      >
                        Reset
                      </button>
                    )}
                  </div>
                  <div className="source-list">
                    {readyDocs.map((d) => (
                      <label
                        className={
                          "source-option " +
                          (selected.includes(d.id) ? "selected" : "")
                        }
                        key={d.id}
                      >
                        <input
                          type="checkbox"
                          disabled={
                            busy ||
                            (!selected.includes(d.id) && selected.length >= 20)
                          }
                          checked={selected.includes(d.id)}
                          onChange={(e) =>
                            setSelected(
                              e.target.checked
                                ? [...selected, d.id]
                                : selected.filter((id) => id !== d.id),
                            )
                          }
                        />
                        <FileText className="source-file" size={22} />
                        <span>
                          <b>{d.title}</b>
                          <small>
                            {d.acl_groups.includes("all") ? (
                              <Files size={11} />
                            ) : (
                              <LockKeyhole size={11} />
                            )}{" "}
                            {d.acl_groups.includes("all")
                              ? "Workspace members"
                              : "Restricted access"}
                          </small>
                        </span>
                      </label>
                    ))}
                  </div>
                  {!readyDocs.length && (
                    <div className="source-empty">
                      <FolderOpen size={29} />
                      <b>No sources ready yet</b>
                      <p>Documents that finish processing appear here.</p>
                    </div>
                  )}
                  <button
                    className="library-link"
                    disabled={busy}
                    onClick={() => setTab("documents")}
                  >
                    Open the document library
                    <ArrowRight size={16} />
                  </button>
                  <div className="scope-note">
                    <ShieldCheck size={19} />
                    <p>
                      <b>Sources match your access</b>
                      <span>
                        The answer is withheld if permissions or the document
                        version change while it is being prepared.
                      </span>
                    </p>
                  </div>
                </div>
              </details>
            </div>
          )}
          {tab === "documents" && (
            <>
              <div className="page-heading">
                <div>
                  <span className="section-kicker">Knowledge library</span>
                  <h1>Documents</h1>
                  <p>Company knowledge, stored and managed in one place.</p>
                </div>
                {editor && (
                  <label
                    className={
                      "primary upload-button " + (busy ? "disabled" : "")
                    }
                  >
                    <Upload size={17} />
                    Upload document
                    <input
                      type="file"
                      accept=".txt,.md,.pdf,.docx"
                      aria-label="Upload document"
                      disabled={busy}
                      onChange={(e) => {
                        const file = e.target.files?.[0];
                        if (file) void action(() => uploadFile(file));
                        e.target.value = "";
                      }}
                    />
                  </label>
                )}
              </div>
              <div className="library-summary">
                <div>
                  <Files size={21} />
                  <span>
                    <b>{docs.length}</b> documents in the library
                  </span>
                </div>
                <div>
                  <span className="status-dot ready-dot" />
                  <span>
                    <b>{readyDocs.length}</b> ready to use
                  </span>
                </div>
                <p>New documents are visible only to their owner.</p>
              </div>
              <section className="data-surface">
                <div className="table-toolbar">
                  <div className="table-search">
                    <Search size={18} />
                    <input
                      aria-label="Search documents"
                      placeholder="Search document titles…"
                      value={filter}
                      onChange={(e) => setFilter(e.target.value)}
                    />
                    {filter && (
                      <button
                        aria-label="Clear search"
                        onClick={() => setFilter("")}
                      >
                        <X size={15} />
                      </button>
                    )}
                  </div>
                  <span>
                    TXT, MD, PDF, DOCX <span>·</span> Max 25 MB
                  </span>
                </div>
                <div className="table-scroll">
                  <table className="document-table">
                    <thead>
                      <tr>
                        <th>Document name</th>
                        <th>Status</th>
                        <th>Access</th>
                        <th className="actions-heading">Manage</th>
                      </tr>
                    </thead>
                    <tbody>
                      {filteredDocs.map((d) => (
                        <tr key={d.id}>
                          <td>
                            <div className="doc-title">
                              <span className="document-icon">
                                <FileText size={21} />
                              </span>
                              <div>
                                <b>{d.title}</b>
                                <small>
                                  {d.filename.split(".").pop()?.toUpperCase()}{" "}
                                  <span>·</span>{" "}
                                  {d.legal_hold
                                    ? "Legal hold active"
                                    : "Versioned document"}
                                </small>
                              </div>
                            </div>
                          </td>
                          <td>
                            <Status value={d.status} />
                          </td>
                          <td>
                            <span className="access-label">
                              {d.acl_groups.includes("all") ? (
                                <Files size={14} />
                              ) : (
                                <LockKeyhole size={14} />
                              )}{" "}
                              {d.acl_groups.includes("all")
                                ? "Workspace members"
                                : d.acl_users.length
                                  ? `${d.acl_users.length} users`
                                  : "Owner only"}
                            </span>
                          </td>
                          <td>
                            {editor && (d.owner_id === me.user_id || admin) && (
                              <div className="row-actions">
                                <label
                                  className={
                                    "text-button version-button " +
                                    (busy ? "disabled" : "")
                                  }
                                >
                                  New version
                                  <input
                                    type="file"
                                    aria-label={`Upload a new version of ${d.title}`}
                                    accept=".txt,.md,.pdf,.docx"
                                    disabled={busy}
                                    onChange={(e) => {
                                      const file = e.target.files?.[0];
                                      if (file)
                                        void action(async () => {
                                          const form = new FormData();
                                          form.append("file", file);
                                          await api(
                                            `/api/documents/${d.id}/versions`,
                                            { method: "POST", body: form },
                                          );
                                          await load();
                                        });
                                      e.target.value = "";
                                    }}
                                  />
                                </label>
                                <button
                                  disabled={busy}
                                  onClick={() =>
                                    void action(() => permissions(d))
                                  }
                                >
                                  Permissions
                                </button>
                                <button
                                  disabled={
                                    busy ||
                                    ["queued", "processing"].includes(d.status)
                                  }
                                  onClick={() =>
                                    void action(async () => {
                                      await api(
                                        `/api/documents/${d.id}/reindex`,
                                        { method: "POST" },
                                      );
                                      await load();
                                    })
                                  }
                                >
                                  Reindex
                                </button>
                                <button
                                  className="danger"
                                  disabled={busy}
                                  onClick={() => {
                                    if (
                                      window.confirm(
                                        `Delete ${d.title}? Access stops immediately and the data is scheduled for purge.`,
                                      )
                                    )
                                      void action(async () => {
                                        await api(`/api/documents/${d.id}`, {
                                          method: "DELETE",
                                        });
                                        await load();
                                      });
                                  }}
                                >
                                  Delete
                                </button>
                              </div>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {!filteredDocs.length && (
                  <div className="empty-state">
                    <FolderOpen size={32} />
                    <h3>
                      {filter
                        ? "No documents found"
                        : "Your library starts here"}
                    </h3>
                    <p>
                      {filter
                        ? "Try another keyword or clear the search."
                        : "Upload the first document to start asking about company knowledge."}
                    </p>
                  </div>
                )}
              </section>
              <div className="table-foot">
                <span>
                  {filteredDocs.length} of {docs.length} documents
                </span>
                <span>
                  <RefreshCw size={12} />
                  Status updates automatically
                </span>
              </div>
            </>
          )}
          {["jobs", "audit", "evaluations"].includes(tab) && (
            <>
              <div className="page-heading">
                <div>
                  <span className="section-kicker">Workspace operations</span>
                  <h1>
                    {tab === "jobs"
                      ? "Document processing"
                      : tab === "audit"
                        ? "Audit trail"
                        : "Quality evaluation"}
                  </h1>
                  <p>
                    {tab === "jobs"
                      ? "Watch document processing, retries, and jobs that need review."
                      : tab === "audit"
                        ? "Review activity and changes in your workspace."
                        : "Review answer-quality test results for each release."}
                  </p>
                </div>
                <button
                  className="secondary"
                  disabled={busy}
                  onClick={() => void load()}
                >
                  <RefreshCw size={16} />
                  Refresh
                </button>
              </div>
              <div className="operations-context">
                <span>
                  {tab === "jobs" ? (
                    <Activity size={20} />
                  ) : tab === "audit" ? (
                    <ShieldCheck size={20} />
                  ) : (
                    <FlaskConical size={20} />
                  )}
                </span>
                <p>
                  {tab === "jobs"
                    ? "Queue status and the latest processing results"
                    : tab === "audit"
                      ? "Workspace activity log"
                      : "Evaluation gate results"}
                  <b>{rows.length} records</b>
                </p>
                <span className="context-live">
                  <span />
                  Updates automatically
                </span>
              </div>
              <section className="data-surface operations">
                <div className="table-scroll">
                  {rows.length ? (
                    <table>
                      <thead>
                        <tr>
                          {(tab === "jobs"
                            ? [
                                "Document / job",
                                "Kind",
                                "Status",
                                "Attempts",
                                "Action",
                              ]
                            : tab === "audit"
                              ? ["Time", "Actor", "Activity", "Resource"]
                              : ["Time", "Dataset / release", "Result", "Metrics"]
                          ).map((h) => (
                            <th key={h}>{h}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {rows.map((r, i) => (
                          <tr key={i}>
                            {tab === "jobs" ? (
                              <>
                                <td>
                                  <b>
                                    {docs.find((d) => d.id === r.document_id)
                                      ?.title ||
                                      String(r.document_id).slice(0, 8)}
                                  </b>
                                  <small>
                                    Job ID: {String(r.id).slice(0, 8)}
                                  </small>
                                </td>
                                <td>{String(r.kind)}</td>
                                <td>
                                  <Status value={String(r.status)} />
                                  {Boolean(r.error) && (
                                    <small className="job-error">
                                      {String(r.error)}
                                    </small>
                                  )}
                                </td>
                                <td>{String(r.attempts)}</td>
                                <td>
                                  {admin && r.status === "dead_letter" && (
                                    <button
                                      className="secondary small-button"
                                      disabled={busy}
                                      onClick={() =>
                                        void action(async () => {
                                          await api(`/api/jobs/${r.id}/retry`, {
                                            method: "POST",
                                          });
                                          await load();
                                        })
                                      }
                                    >
                                      Retry
                                    </button>
                                  )}
                                </td>
                              </>
                            ) : tab === "audit" ? (
                              <>
                                <td className="time-cell">
                                  {new Date(
                                    String(r.created_at),
                                  ).toLocaleDateString("en-US", {
                                    day: "numeric",
                                    month: "short",
                                    year: "numeric",
                                  })}
                                  <small>
                                    {new Date(
                                      String(r.created_at),
                                    ).toLocaleTimeString("en-US")}
                                  </small>
                                </td>
                                <td title={String(r.actor_id)}>
                                  {r.actor_id === me.user_id
                                    ? me.name
                                    : String(r.actor_id).slice(0, 12)}
                                </td>
                                <td>
                                  <span className="audit-action">
                                    {String(r.action)}
                                  </span>
                                </td>
                                <td
                                  className="resource-cell"
                                  title={String(r.resource)}
                                >
                                  {String(r.resource)}
                                </td>
                              </>
                            ) : (
                              <>
                                <td className="time-cell">
                                  {new Date(
                                    String(r.created_at),
                                  ).toLocaleDateString("en-US", {
                                    day: "numeric",
                                    month: "short",
                                    year: "numeric",
                                  })}
                                  <small>
                                    {new Date(
                                      String(r.created_at),
                                    ).toLocaleTimeString("en-US")}
                                  </small>
                                </td>
                                <td className="revision-cell">
                                  <b title={String(r.dataset_revision)}>
                                    {String(r.dataset_revision)}
                                  </b>
                                  <small title={String(r.release_revision)}>
                                    Release {String(r.release_revision)}
                                  </small>
                                </td>
                                <td>
                                  <span
                                    className={
                                      "status " +
                                      (r.passed ? "ready" : "failed")
                                    }
                                  >
                                    {r.passed ? (
                                      <CheckCircle2 size={13} />
                                    ) : (
                                      <CircleAlert size={13} />
                                    )}{" "}
                                    {r.passed ? "Passed" : "Failed"}
                                  </span>
                                </td>
                                <td>
                                  <EvaluationMetrics value={r.metrics} />
                                </td>
                              </>
                            )}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  ) : (
                    <div className="empty-state">
                      {tab === "evaluations" ? (
                        <FlaskConical size={32} />
                      ) : (
                        <Activity size={32} />
                      )}
                      <h3>
                        {tab === "evaluations"
                          ? "No evaluation results yet"
                          : "No activity yet"}
                      </h3>
                      <p>
                        {tab === "evaluations"
                          ? "Run the evaluation runner with a company dataset to see results here."
                          : "Workspace activity will appear here."}
                      </p>
                    </div>
                  )}
                </div>
              </section>
            </>
          )}
        </div>
      </main>
      {editing && (
        <dialog
          ref={modalRef}
          className="permission-dialog"
          onCancel={() => setEditing(null)}
          aria-labelledby="permission-title"
        >
          <div className="modal-top">
            <span className="modal-icon">
              <ShieldCheck size={23} />
            </span>
            <button
              className="icon-button"
              aria-label="Close permissions dialog"
              disabled={busy}
              onClick={() => setEditing(null)}
            >
              <X size={20} />
            </button>
          </div>
          <span className="section-kicker">Document access</span>
          <h2 id="permission-title">Manage permissions</h2>
          <p className="modal-document">
            <FileText size={16} />
            {editing.title}
          </p>
          <p>The owner always has access. Changes apply immediately.</p>
          <label className="checkbox tenant-checkbox">
            <input
              type="checkbox"
              checked={all}
              onChange={(e) => setAll(e.target.checked)}
            />
            <span>
              <b>All workspace members</b>
              <small>Allow every member to read this document.</small>
            </span>
          </label>
          <h3 className="member-heading">Specific people</h3>
          <div className="member-list">
            {members
              .filter((m) => m.user_id !== editing.owner_id)
              .map((m) => (
                <label className="checkbox" key={m.user_id}>
                  <input
                    type="checkbox"
                    checked={aclUsers.includes(m.user_id)}
                    onChange={(e) =>
                      setAclUsers(
                        e.target.checked
                          ? [...aclUsers, m.user_id]
                          : aclUsers.filter((id) => id !== m.user_id),
                      )
                    }
                  />
                  <span className="member-avatar">
                    {m.name.slice(0, 2).toUpperCase()}
                  </span>
                  <span>
                    {m.name}
                    <small>{roleNames[m.role] || m.role}</small>
                  </span>
                </label>
              ))}
          </div>
          {admin && (
            <div className="retention-section">
              <h3>Retention & storage</h3>
              <label className="date-label">
                Keep until
                <input
                  type="date"
                  value={retention}
                  onChange={(e) => setRetention(e.target.value)}
                />
              </label>
              <label className="checkbox">
                <input
                  type="checkbox"
                  checked={hold}
                  onChange={(e) => setHold(e.target.checked)}
                />
                <span>
                  <b>Legal hold</b>
                  <small>Prevent permanent deletion while the hold is active.</small>
                </span>
              </label>
            </div>
          )}
          {error && <p className="alert error">{error}</p>}
          <div className="modal-actions">
            <button
              className="secondary"
              disabled={busy}
              onClick={() => setEditing(null)}
            >
              Cancel
            </button>
            <button
              className="primary"
              disabled={busy}
              onClick={() => void action(savePermissions)}
            >
              {busy ? "Saving…" : "Save permissions"}
            </button>
          </div>
        </dialog>
      )}
    </div>
  );
}
createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <Portal />
  </React.StrictMode>,
);
