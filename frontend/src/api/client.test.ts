import { describe, expect, it, vi } from "vitest";
import { ApiClient } from "./client";

describe("ApiClient", () => {
  it("acquires CSRF once and sends same-origin credentials on mutations", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ csrf_token: "token-1" }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ id: "task-1" }), {
          status: 201,
          headers: { "content-type": "application/json" },
        }),
      );
    const client = new ApiClient(fetcher);

    await client.request("/api/tasks", {
      method: "POST",
      body: { sources: [] },
    });

    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(fetcher.mock.calls[0][1]).toMatchObject({
      credentials: "same-origin",
    });
    expect(fetcher.mock.calls[1][1]).toMatchObject({
      credentials: "same-origin",
      method: "POST",
      headers: expect.objectContaining({ "X-CSRF-Token": "token-1" }),
    });
  });

  it("maps API errors and never replays a forbidden mutation", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ csrf_token: "token-1" }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            error: { code: "csrf_failed", message: "CSRF validation failed" },
          }),
          { status: 403, headers: { "content-type": "application/json" } },
        ),
      );
    const client = new ApiClient(fetcher);

    await expect(
      client.request("/api/tasks", { method: "POST", body: {} }),
    ).rejects.toMatchObject({
      status: 403,
      code: "csrf_failed",
    });
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
});
