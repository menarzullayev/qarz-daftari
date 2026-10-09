// @vitest-environment jsdom
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { deferred } from "../testing/fakeServer";
import { ApiError, NO_ANSWER } from "./api";
import { useChangeCount, useLatest, useLoad, usePagedList, useSubmit } from "./hooks";

afterEach(cleanup);

type Payload = { amount: number };

/** A `send` whose answers the test controls, recording the key each call carried. */
function recorder() {
  const calls: { payload: Payload; key: string }[] = [];
  const answers: Array<ReturnType<typeof deferred<string>>> = [];
  const failures: Array<(error: unknown) => void> = [];
  const send = (payload: Payload, key: string) => {
    calls.push({ payload, key });
    const answer = deferred<string>();
    answers.push(answer);
    return new Promise<string>((resolve, reject) => {
      failures.push(reject);
      void answer.promise.then(resolve);
    });
  };
  return { calls, send, succeed: (index: number) => answers[index]?.resolve("saved"), fail: (index: number) => failures[index]?.(new ApiError(0, "NETWORK", null)) };
}

describe("useSubmit", () => {
  it("sends one request for a double tap", async () => {
    const server = recorder();
    const { result } = renderHook(() => useSubmit(server.send));
    act(() => {
      // Two taps in the same frame: neither has seen the "pending" state yet.
      result.current.submit({ amount: 45000 });
      result.current.submit({ amount: 45000 });
    });
    act(() => result.current.submit({ amount: 45000 }));
    expect(result.current.state.status).toBe("pending");
    expect(server.calls).toHaveLength(1);

    await act(async () => server.succeed(0));
    expect(result.current.state).toEqual({ status: "done", result: "saved" });
    expect(server.calls).toHaveLength(1);
  });

  it("resends the same key when the same action is retried after a failure", async () => {
    const server = recorder();
    const { result } = renderHook(() => useSubmit(server.send));
    act(() => result.current.submit({ amount: 45000 }));
    await act(async () => server.fail(0));
    expect(result.current.state.status).toBe("error");

    act(() => result.current.submit({ amount: 45000 }));
    await act(async () => server.fail(1));
    act(() => result.current.submit({ amount: 45000 }));

    expect(server.calls).toHaveLength(3);
    expect(server.calls[0]?.key).toMatch(/^[A-Za-z0-9_-]{8,128}$/);
    expect(server.calls[1]?.key).toBe(server.calls[0]?.key);
    expect(server.calls[2]?.key).toBe(server.calls[0]?.key);
  });

  it("uses a new key when the payload changed, because that is a different action", async () => {
    const server = recorder();
    const { result } = renderHook(() => useSubmit(server.send));
    act(() => result.current.submit({ amount: 45000 }));
    await act(async () => server.fail(0));
    act(() => result.current.submit({ amount: 54000 }));
    expect(server.calls[1]?.key).not.toBe(server.calls[0]?.key);
  });

  it("uses a new key after a success, so the same sale can be recorded again on purpose", async () => {
    const server = recorder();
    const { result } = renderHook(() => useSubmit(server.send));
    act(() => result.current.submit({ amount: 45000 }));
    await act(async () => server.succeed(0));
    act(() => result.current.reset());
    expect(result.current.state.status).toBe("idle");
    act(() => result.current.submit({ amount: 45000 }));
    expect(server.calls).toHaveLength(2);
    expect(server.calls[1]?.key).not.toBe(server.calls[0]?.key);
  });

  it("reports a send that throws before it starts as a failure, and unlocks", async () => {
    let attempts = 0;
    const { result } = renderHook(() =>
      useSubmit((payload: Payload) => {
        attempts += 1;
        throw new RangeError(`not whole: ${payload.amount}`);
      }),
    );
    await act(async () => result.current.submit({ amount: 0.5 }));
    expect(result.current.state.status).toBe("error");
    await act(async () => result.current.submit({ amount: 0.5 }));
    expect(attempts).toBe(2);
  });
});

describe("useLoad", () => {
  it("goes from loading to ready, and loads again on reload", async () => {
    let calls = 0;
    const { result } = renderHook(() => useLoad(async () => (calls += 1), []));
    expect(result.current.state).toEqual({ status: "loading" });
    await waitFor(() => expect(result.current.state).toEqual({ status: "ready", data: 1 }));
    act(() => result.current.reload());
    await waitFor(() => expect(result.current.state).toEqual({ status: "ready", data: 2 }));
  });

  it("reports a failure with its code", async () => {
    const { result } = renderHook(() =>
      useLoad(async () => {
        throw new ApiError(403, "SHOP_SUSPENDED", "Do'kon to'xtatilgan.");
      }, []),
    );
    await waitFor(() => expect(result.current.state.status).toBe("error"));
    expect(result.current.state).toMatchObject({ error: { code: "SHOP_SUSPENDED" } });
  });

  it("ignores a slow answer to a request that was replaced", async () => {
    const slow = deferred<string>();
    const { result, rerender } = renderHook(
      ({ query }: { query: string }) => useLoad(() => (query === "a" ? slow.promise : Promise.resolve("answer for b")), [query]),
      { initialProps: { query: "a" } },
    );
    rerender({ query: "b" });
    await waitFor(() => expect(result.current.state).toEqual({ status: "ready", data: "answer for b" }));
    await act(async () => slow.resolve("answer for a"));
    expect(result.current.state).toEqual({ status: "ready", data: "answer for b" });
  });
});

describe("usePagedList", () => {
  it("appends the next page using the server's cursor and stops when there is none", async () => {
    const cursors: Array<string | null> = [];
    const { result } = renderHook(() =>
      usePagedList(async (cursor) => {
        cursors.push(cursor);
        return cursor === null ? { items: ["a", "b"], nextCursor: "after-b" } : { items: ["c"], nextCursor: null };
      }, []),
    );
    await waitFor(() => expect(result.current.state.status).toBe("ready"));
    expect(result.current.state).toMatchObject({ items: ["a", "b"], nextCursor: "after-b" });

    act(() => {
      result.current.loadMore();
      result.current.loadMore(); // a second tap while the page is loading
    });
    await waitFor(() => expect(result.current.state).toMatchObject({ items: ["a", "b", "c"], nextCursor: null }));
    act(() => result.current.loadMore());
    expect(cursors).toEqual([null, "after-b"]);
  });

  it("keeps the rows it has when the next page fails", async () => {
    const { result } = renderHook(() =>
      usePagedList(async (cursor) => {
        if (cursor !== null) {
          throw new ApiError(0, "NETWORK", null);
        }
        return { items: ["a"], nextCursor: "after-a" };
      }, []),
    );
    await waitFor(() => expect(result.current.state.status).toBe("ready"));
    act(() => result.current.loadMore());
    await waitFor(() => expect(result.current.state).toMatchObject({ moreError: { code: "NETWORK" } }));
    expect(result.current.state).toMatchObject({ items: ["a"], nextCursor: "after-a", loadingMore: false });
  });
});

describe("the caller's list of reasons to load again", () => {
  it("does not load again when a render passes a new array of the same items", async () => {
    let calls = 0;
    const shop = { id: "a" };
    const { result, rerender } = renderHook(({ day }: { day: string }) => useLoad(async () => (calls += 1), [shop, day]), {
      initialProps: { day: "2026-10-06" },
    });
    await waitFor(() => expect(result.current.state.status).toBe("ready"));
    rerender({ day: "2026-10-06" });
    rerender({ day: "2026-10-06" });
    expect(calls).toBe(1);
    expect(result.current.state).toEqual({ status: "ready", data: 1 });
  });

  it("loads again when one item changes, and once for it", async () => {
    let calls = 0;
    const { result, rerender } = renderHook(({ day }: { day: string }) => useLoad(async () => (calls += 1), [day]), {
      initialProps: { day: "2026-10-06" },
    });
    await waitFor(() => expect(result.current.state.status).toBe("ready"));
    rerender({ day: "2026-10-07" });
    await waitFor(() => expect(result.current.state).toEqual({ status: "ready", data: 2 }));
    rerender({ day: "2026-10-07" });
    expect(calls).toBe(2);
  });

  it("does the same for a list read page by page", async () => {
    const asked: string[] = [];
    const { result, rerender } = renderHook(
      ({ query }: { query: string }) =>
        usePagedList(async () => {
          asked.push(query);
          return { items: [query], nextCursor: null };
        }, [query]),
      { initialProps: { query: "ali" } },
    );
    await waitFor(() => expect(result.current.state.status).toBe("ready"));
    rerender({ query: "ali" });
    expect(asked).toEqual(["ali"]);
    rerender({ query: "vali" });
    await waitFor(() => expect(result.current.state).toMatchObject({ items: ["vali"] }));
    expect(asked).toEqual(["ali", "vali"]);
  });

  it("counts a change, compares as React does, and counts a list that grew", () => {
    const { result, rerender } = renderHook(({ deps }: { deps: readonly unknown[] }) => useChangeCount(deps), {
      initialProps: { deps: [1, "a", Number.NaN] as readonly unknown[] },
    });
    expect(result.current).toBe(0);
    // NaN is NaN and a new array of the same items is the same list.
    rerender({ deps: [1, "a", Number.NaN] });
    expect(result.current).toBe(0);
    rerender({ deps: [1, "b", Number.NaN] });
    expect(result.current).toBe(1);
    rerender({ deps: [1, "b", Number.NaN, undefined] });
    expect(result.current).toBe(2);
    // An object that looks the same is another object.
    rerender({ deps: [{}] });
    rerender({ deps: [{}] });
    expect(result.current).toBe(4);
  });

  it("gives a handler the value of the latest render", () => {
    const { result, rerender } = renderHook(({ value }: { value: string }) => useLatest(value), { initialProps: { value: "a" } });
    const box = result.current;
    rerender({ value: "b" });
    expect(result.current).toBe(box);
    expect(box.current).toBe("b");
  });
});

describe("a write the page stopped waiting for", () => {
  it("is sent again with the same key, and with a new one once the payload changed", async () => {
    const keys: string[] = [];
    let attempt = 0;
    const { result } = renderHook(() =>
      useSubmit(async (_payload: Payload, key: string) => {
        keys.push(key);
        attempt += 1;
        if (attempt === 1) {
          // What `call` rejects with when its limit passes for a write.
          throw new ApiError(0, NO_ANSWER, null);
        }
        return "saved";
      }),
    );
    act(() => result.current.submit({ amount: 45000 }));
    await waitFor(() => expect(result.current.state).toMatchObject({ status: "error", error: { code: NO_ANSWER } }));
    act(() => result.current.submit({ amount: 45000 }));
    await waitFor(() => expect(result.current.state).toEqual({ status: "done", result: "saved" }));
    expect(keys[1]).toBe(keys[0]);

    act(() => result.current.submit({ amount: 54000 }));
    await waitFor(() => expect(keys).toHaveLength(3));
    expect(keys[2]).not.toBe(keys[0]);
  });
});
