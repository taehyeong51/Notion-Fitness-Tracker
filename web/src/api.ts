export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public detail: unknown,
  ) {
    super(message);
  }
}
export async function api<T>(
  path: string,
  body?: unknown,
  signal?: AbortSignal,
): Promise<T> {
  const response = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    headers:
      body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: "no-store",
    signal,
  });
  const data = await response.json();
  if (!response.ok) {
    const detail = data.detail ?? data;
    throw new ApiError(
      response.status,
      typeof detail === "string"
        ? detail
        : (detail.message ?? data.message ?? "요청을 완료하지 못했습니다"),
      detail,
    );
  }
  return data as T;
}
export function errorMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  if (typeof error === "string") return error;
  if (error && typeof error === "object" && "message" in error)
    return String(error.message);
  return "요청을 완료하지 못했습니다";
}
export async function csv(body: object, kind: "sets" | "sessions") {
  const response = await fetch("/api/export/csv", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...body, kind }),
    cache: "no-store",
  });
  if (!response.ok) throw new Error("CSV를 내보내지 못했습니다");
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `fitness-${kind}-${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
