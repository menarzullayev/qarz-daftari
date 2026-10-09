import { useCallback, useEffect, useRef, useState } from "react";

import { type ApiError, isAbort, newIdempotencyKey, type Page, toApiError } from "./api";

/**
 * A box that always holds the value of the latest render. An effect or a handler reads it when it runs,
 * so it uses what the component has now without running again because that value is a new object.
 */
export function useLatest<T>(value: T): { readonly current: T } {
  const box = useRef(value);
  box.current = value;
  return box;
}

function sameItems(before: readonly unknown[], after: readonly unknown[]): boolean {
  return before.length === after.length && before.every((item, index) => Object.is(item, after[index]));
}

/**
 * A number that changes when an item of `deps` does (compared with `Object.is`, as React compares an
 * effect's dependencies), and stays when a render passes a new array of the same items. It is what lets
 * a hook take its caller's list of reasons to run again and still give its own effect a list that is
 * written out, which the linter can check: a spread of the caller's array is one it cannot.
 */
export function useChangeCount(deps: readonly unknown[]): number {
  const seen = useRef({ deps, count: 0 });
  if (!sameItems(seen.current.deps, deps)) {
    seen.current = { deps, count: seen.current.count + 1 };
  }
  return seen.current.count;
}

export type Loaded<T> = { status: "loading" } | { status: "error"; error: ApiError } | { status: "ready"; data: T };

/**
 * Loads data when the screen opens and again whenever `deps` change or `reload` is called. A request
 * that is no longer wanted is aborted and its answer ignored, so a slow early answer never replaces a
 * later one.
 */
export function useLoad<T>(
  load: (signal: AbortSignal) => Promise<T>,
  deps: readonly unknown[],
): { state: Loaded<T>; reload: () => void } {
  const [state, setState] = useState<Loaded<T>>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);
  const latest = useLatest(load);
  const changes = useChangeCount(deps);

  useEffect(() => {
    const controller = new AbortController();
    setState({ status: "loading" });
    latest.current(controller.signal).then(
      (data) => {
        if (!controller.signal.aborted) {
          setState({ status: "ready", data });
        }
      },
      (error: unknown) => {
        if (!controller.signal.aborted && !isAbort(error)) {
          setState({ status: "error", error: toApiError(error) });
        }
      },
    );
    return () => controller.abort();
    // `changes` stands for the caller's list; `load` itself is a new function on every render and is
    // read through `latest`.
  }, [changes, attempt, latest]);

  const reload = useCallback(() => setAttempt((count) => count + 1), []);
  return { state, reload };
}

export type PagedList<T> =
  | { status: "loading" }
  | { status: "error"; error: ApiError }
  | { status: "ready"; items: T[]; nextCursor: string | null; loadingMore: boolean; moreError: ApiError | null };

/** A list read page by page with the server's cursor: the first page on open, the next ones on request. */
export function usePagedList<T>(
  loadPage: (cursor: string | null, signal: AbortSignal) => Promise<Page<T>>,
  deps: readonly unknown[],
): { state: PagedList<T>; reload: () => void; loadMore: () => void } {
  const [state, setState] = useState<PagedList<T>>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);
  const latest = useLatest(loadPage);
  const changes = useChangeCount(deps);
  const current = useRef<AbortController | null>(null);
  const loadingMore = useRef(false);

  useEffect(() => {
    const controller = new AbortController();
    current.current = controller;
    loadingMore.current = false;
    setState({ status: "loading" });
    latest.current(null, controller.signal).then(
      (page) => {
        if (!controller.signal.aborted) {
          setState({ status: "ready", items: page.items, nextCursor: page.nextCursor, loadingMore: false, moreError: null });
        }
      },
      (error: unknown) => {
        if (!controller.signal.aborted && !isAbort(error)) {
          setState({ status: "error", error: toApiError(error) });
        }
      },
    );
    return () => controller.abort();
  }, [changes, attempt, latest]);

  const reload = useCallback(() => setAttempt((count) => count + 1), []);

  const loadMore = () => {
    const controller = current.current;
    if (state.status !== "ready" || state.nextCursor === null || loadingMore.current || !controller) {
      return;
    }
    loadingMore.current = true;
    setState({ ...state, loadingMore: true, moreError: null });
    latest.current(state.nextCursor, controller.signal).then(
      (page) => {
        if (!controller.signal.aborted) {
          loadingMore.current = false;
          setState({
            status: "ready",
            items: [...state.items, ...page.items],
            nextCursor: page.nextCursor,
            loadingMore: false,
            moreError: null,
          });
        }
      },
      (error: unknown) => {
        if (!controller.signal.aborted && !isAbort(error)) {
          loadingMore.current = false;
          setState({ ...state, loadingMore: false, moreError: toApiError(error) });
        }
      },
    );
  };

  return { state, reload, loadMore };
}

export type Submission<R> =
  | { status: "idle" }
  | { status: "pending" }
  | { status: "error"; error: ApiError }
  | { status: "done"; result: R };

/**
 * Sends a write exactly once per user action (the API's Idempotency-Key contract).
 *
 * - While a request is in flight every further `submit` is ignored, so a double tap sends one request.
 *   The lock is a ref, not state: two taps in the same frame both see the state from before the first.
 * - The key belongs to the payload. After a failure, submitting the same payload again resends the same
 *   key, so the server answers a request it already applied instead of applying it twice. A changed
 *   payload is a new action and gets a new key, as does anything submitted after a success.
 */
export function useSubmit<P, R>(
  send: (payload: P, idempotencyKey: string) => Promise<R>,
): { state: Submission<R>; submit: (payload: P) => void; reset: () => void } {
  const [state, setState] = useState<Submission<R>>({ status: "idle" });
  const inFlight = useRef(false);
  const action = useRef<{ fingerprint: string; key: string } | null>(null);
  const latest = useLatest(send);

  const submit = (payload: P) => {
    if (inFlight.current) {
      return;
    }
    const fingerprint = JSON.stringify(payload);
    if (action.current?.fingerprint !== fingerprint) {
      action.current = { fingerprint, key: newIdempotencyKey() };
    }
    inFlight.current = true;
    setState({ status: "pending" });
    const key = action.current.key;
    // `send` may throw before it returns a promise; that is a failure like any other.
    new Promise<R>((resolve) => resolve(latest.current(payload, key))).then(
      (result) => {
        inFlight.current = false;
        action.current = null;
        setState({ status: "done", result });
      },
      (error: unknown) => {
        inFlight.current = false;
        setState({ status: "error", error: toApiError(error) });
      },
    );
  };

  const reset = useCallback(() => {
    if (!inFlight.current) {
      setState({ status: "idle" });
    }
  }, []);

  return { state, submit, reset };
}
