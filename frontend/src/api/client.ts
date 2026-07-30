type HttpMethod = "GET" | "POST" | "PATCH" | "PUT" | "DELETE";

interface RequestOptions {
  method?: HttpMethod;
  body?: unknown | FormData;
  signal?: AbortSignal;
  headers?: Record<string, string>;
}

interface ApiErrorPayload {
  error?: {
    code?: string;
    message?: string;
    details?: unknown;
  };
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: unknown;

  constructor(
    status: number,
    code: string,
    message: string,
    details: unknown = null,
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

function assertApiPath(path: string): void {
  if (!path.startsWith("/api/") || path.includes("\\") || path.startsWith("//")) {
    throw new Error("API path must be a same-origin /api/ path");
  }
}

function isMutation(method: HttpMethod): boolean {
  return method !== "GET";
}

export class ApiClient {
  private csrfPromise: Promise<string> | null = null;

  constructor(
    private readonly fetcher: typeof fetch = (...args) =>
      globalThis.fetch(...args),
  ) {}

  private async csrf(signal?: AbortSignal): Promise<string> {
    if (this.csrfPromise === null) {
      this.csrfPromise = this.fetcher("/api/security/csrf", {
        method: "GET",
        credentials: "same-origin",
        headers: { Accept: "application/json" },
        signal,
      })
        .then(async (response) => {
          if (!response.ok) {
            throw new ApiError(
              response.status,
              "csrf_unavailable",
              "无法建立本地安全会话",
            );
          }
          const payload = (await response.json()) as { csrf_token?: unknown };
          if (typeof payload.csrf_token !== "string" || !payload.csrf_token) {
            throw new ApiError(
              500,
              "invalid_csrf_response",
              "本地安全会话响应无效",
            );
          }
          return payload.csrf_token;
        })
        .catch((error) => {
          this.csrfPromise = null;
          throw error;
        });
    }
    return this.csrfPromise;
  }

  async request<T>(path: string, options: RequestOptions = {}): Promise<T> {
    assertApiPath(path);
    const method = options.method ?? "GET";
    const headers: Record<string, string> = {
      Accept: "application/json",
      ...options.headers,
    };
    let body: BodyInit | undefined;
    if (options.body instanceof FormData) {
      body = options.body;
    } else if (options.body !== undefined) {
      headers["Content-Type"] = "application/json";
      body = JSON.stringify(options.body);
    }
    if (isMutation(method)) {
      headers["X-CSRF-Token"] = await this.csrf(options.signal);
    }
    const response = await this.fetcher(path, {
      method,
      credentials: "same-origin",
      headers,
      body,
      signal: options.signal,
    });
    if (!response.ok) {
      let payload: ApiErrorPayload = {};
      try {
        payload = (await response.json()) as ApiErrorPayload;
      } catch {
        // The public message below deliberately avoids reflecting raw response text.
      }
      throw new ApiError(
        response.status,
        payload.error?.code ?? "request_failed",
        payload.error?.message ?? "本地请求失败",
        payload.error?.details ?? null,
      );
    }
    if (response.status === 204) {
      return undefined as T;
    }
    const contentType = response.headers.get("content-type") ?? "";
    if (!contentType.includes("application/json")) {
      return (await response.text()) as T;
    }
    return (await response.json()) as T;
  }

  async download(path: string, signal?: AbortSignal): Promise<Blob> {
    assertApiPath(path);
    const response = await this.fetcher(path, {
      method: "GET",
      credentials: "same-origin",
      headers: { Accept: "*/*" },
      signal,
    });
    if (!response.ok) {
      throw new ApiError(response.status, "download_failed", "导出失败");
    }
    return response.blob();
  }

  async requestPage<T>(
    path: string,
    signal?: AbortSignal,
  ): Promise<{ data: T; nextCursor: string | null }> {
    assertApiPath(path);
    const response = await this.fetcher(path, {
      method: "GET",
      credentials: "same-origin",
      headers: { Accept: "application/json" },
      signal,
    });
    if (!response.ok) {
      let payload: ApiErrorPayload = {};
      try {
        payload = (await response.json()) as ApiErrorPayload;
      } catch {
        // Keep the public fallback message below.
      }
      throw new ApiError(
        response.status,
        payload.error?.code ?? "request_failed",
        payload.error?.message ?? "本地请求失败",
        payload.error?.details ?? null,
      );
    }
    return {
      data: (await response.json()) as T,
      nextCursor: response.headers.get("x-next-cursor"),
    };
  }

  async uploadTask<T>(
    file: File,
    metadata: Record<string, unknown>,
    options: {
      signal?: AbortSignal;
      onProgress?: (loaded: number, total: number) => void;
    } = {},
  ): Promise<T> {
    return this.uploadMultipart("/api/tasks", file, metadata, options);
  }

  async uploadTestSample<T>(
    file: File,
    options: {
      signal?: AbortSignal;
      onProgress?: (loaded: number, total: number) => void;
    } = {},
  ): Promise<T> {
    return this.uploadMultipart(
      "/api/test-samples",
      file,
      { kind: "media" },
      options,
    );
  }

  private async uploadMultipart<T>(
    path: string,
    file: File,
    metadata: Record<string, unknown>,
    options: {
      signal?: AbortSignal;
      onProgress?: (loaded: number, total: number) => void;
    },
  ): Promise<T> {
    assertApiPath(path);
    const token = await this.csrf(options.signal);
    const safeName = file.name.normalize("NFKC");
    if (
      !safeName ||
      /[/\\:"\r\n]/u.test(safeName) ||
      [...safeName].some((character) => {
        const code = character.codePointAt(0) ?? 0;
        return code < 32 || code === 127;
      })
    ) {
      throw new ApiError(400, "unsafe_filename", "文件名包含不安全字符");
    }
    const boundary = `vtnote-${crypto.randomUUID()}`;
    const prefix =
      `--${boundary}\r\n` +
      'Content-Disposition: form-data; name="metadata"\r\n' +
      "Content-Type: application/json\r\n\r\n" +
      `${JSON.stringify(metadata)}\r\n` +
      `--${boundary}\r\n` +
      `Content-Disposition: form-data; name="file"; filename="${safeName}"\r\n` +
      `Content-Type: ${file.type || "application/octet-stream"}\r\n\r\n`;
    const body = new Blob([prefix, file, `\r\n--${boundary}--\r\n`]);

    return new Promise<T>((resolve, reject) => {
      const request = new XMLHttpRequest();
      request.open("POST", path);
      request.withCredentials = true;
      request.responseType = "json";
      request.setRequestHeader("Content-Type", `multipart/form-data; boundary=${boundary}`);
      request.setRequestHeader("Accept", "application/json");
      request.setRequestHeader("X-CSRF-Token", token);
      request.upload.addEventListener("progress", (event) => {
        if (event.lengthComputable) {
          options.onProgress?.(event.loaded, event.total);
        }
      });
      const abort = () => request.abort();
      options.signal?.addEventListener("abort", abort, { once: true });
      request.addEventListener("load", () => {
        options.signal?.removeEventListener("abort", abort);
        if (request.status >= 200 && request.status < 300) {
          resolve(request.response as T);
          return;
        }
        const payload = request.response as ApiErrorPayload | null;
        reject(
          new ApiError(
            request.status,
            payload?.error?.code ?? "upload_failed",
            payload?.error?.message ?? "上传失败",
            payload?.error?.details ?? null,
          ),
        );
      });
      request.addEventListener("error", () => {
        options.signal?.removeEventListener("abort", abort);
        reject(new ApiError(0, "network_error", "无法连接本地服务"));
      });
      request.addEventListener("abort", () => {
        options.signal?.removeEventListener("abort", abort);
        reject(new DOMException("Upload aborted", "AbortError"));
      });
      request.send(body);
    });
  }
}

export const api = new ApiClient();

const terminalStatuses = new Set([
  "completed",
  "completed_with_warnings",
  "failed",
  "canceled",
]);

export function isTerminalStatus(status: string): boolean {
  return terminalStatuses.has(status);
}

export function foregroundPollDelay(elapsedMs: number): number {
  if (elapsedMs < 30_000) return 1_000;
  if (elapsedMs < 120_000) return 2_000;
  return 5_000;
}

export function retryPollDelay(failureCount: number): number {
  const sequence = [2_000, 4_000, 8_000, 16_000, 30_000];
  return sequence[Math.min(Math.max(failureCount - 1, 0), sequence.length - 1)];
}
