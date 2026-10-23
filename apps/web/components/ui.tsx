"use client";
import { ReactNode, useState } from "react";

export function Card({ title, actions, children, className = "" }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`card ${className}`}>
      {(title || actions) && (
        <header className="card-head">
          <h2>{title}</h2>
          <div className="row">{actions}</div>
        </header>
      )}
      {children}
    </section>
  );
}

export function Badge({ tone = "neutral", children }: { tone?: "neutral" | "ok" | "warn" | "bad" | "info"; children: ReactNode }) {
  return <span className={`badge ${tone}`}>{children}</span>;
}

export const flagTone = (f?: string | null) => (f === "HH" || f === "LL" ? "bad" : f === "H" || f === "L" ? "warn" : "ok");

export function ErrorBox({ error }: { error?: string | null }) {
  return error ? <div className="error" role="alert">{error}</div> : null;
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="muted">{children}</p>;
}

export function AsyncButton({ onClick, children, className = "", disabled }: { onClick: () => Promise<unknown>; children: ReactNode; className?: string; disabled?: boolean }) {
  const [busy, setBusy] = useState(false);
  return (
    <button className={className} disabled={busy || disabled} onClick={async () => {
      setBusy(true);
      try { await onClick(); } finally { setBusy(false); }
    }}>
      {busy ? "Working..." : children}
    </button>
  );
}

export function Tabs({ tabs, value, onChange }: { tabs: { id: string; label: ReactNode }[]; value: string; onChange: (id: string) => void }) {
  return (
    <nav className="tabs" role="tablist">
      {tabs.map((t) => (
        <button key={t.id} role="tab" aria-selected={value === t.id} className={value === t.id ? "active" : ""} onClick={() => onChange(t.id)}>{t.label}</button>
      ))}
    </nav>
  );
}

export function Json({ value }: { value: unknown }) {
  return <pre className="json">{JSON.stringify(value, null, 2)}</pre>;
}
