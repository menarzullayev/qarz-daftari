import { useSyncExternalStore, type ReactNode } from "react";

/**
 * A hash router in a few lines. The three entry points are static files under /app/, /panel/ and
 * /admin/, so hash routes need no server rewrite rules, and the whole router costs well under 1 KB of
 * the 300 KB budget (NFR-010).
 *
 * Telegram opens a Mini App with its launch parameters in the fragment ("#tgWebAppData=..."). Only a
 * fragment that starts with "#/" is a route; anything else, including Telegram's, is the home route.
 */
export function parseHash(hash: string): string {
  if (!hash.startsWith("#/")) {
    return "/";
  }
  const path = hash.slice(1).split("?")[0] ?? "/";
  return path.length > 1 ? path.replace(/\/+$/, "") || "/" : "/";
}

export function hrefFor(path: string): string {
  return `#${path}`;
}

function subscribe(onChange: () => void): () => void {
  window.addEventListener("hashchange", onChange);
  return () => window.removeEventListener("hashchange", onChange);
}

function readHash(): string {
  return window.location.hash;
}

/** Current route path; re-renders the caller when the fragment changes. */
export function useHashPath(): string {
  return parseHash(useSyncExternalStore(subscribe, readHash, () => ""));
}

type LinkProps = {
  to: string;
  current?: boolean;
  className?: string;
  children: ReactNode;
};

/** A real anchor, so it works with the keyboard, a long press, and "open in new tab". */
export function Link({ to, current = false, className, children }: LinkProps) {
  return (
    <a href={hrefFor(to)} className={className} aria-current={current ? "page" : undefined}>
      {children}
    </a>
  );
}
