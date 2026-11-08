import type { Finding, Fix, ImportNeighbors, Repo, ServerConfig } from "./types";

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (typeof body.detail === "string") msg = body.detail;
      else if (Array.isArray(body.detail)) msg = body.detail.map((d: { msg: string }) => d.msg).join("; ");
    } catch {
      /* keep status text */
    }
    throw new Error(msg);
  }
  return res.json() as Promise<T>;
}

export const listRepos = () => fetch("/api/repos").then((r) => json<Repo[]>(r));

export const createRepo = (url: string) =>
  fetch("/api/repos", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ url }),
  }).then((r) => json<{ id: number; status: string }>(r));

const send = <T>(method: string, url: string, body?: unknown) =>
  fetch(url, {
    method,
    headers: body === undefined ? undefined : { "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  }).then((r) => json<T>(r));

export const getConfig = () => fetch("/api/config").then((r) => json<ServerConfig>(r));
export const startAnalysis = (repoId: number) => send<{ run_id: number }>("POST", `/api/repos/${repoId}/analyze`);
export const listFindings = (repoId: number) => fetch(`/api/repos/${repoId}/findings`).then((r) => json<Finding[]>(r));
export const setFindingStatus = (id: number, status: "open" | "confirmed" | "rejected") =>
  send<Finding>("PATCH", `/api/findings/${id}`, { status });
export const startFix = (findingId: number) => send<Fix>("POST", `/api/findings/${findingId}/fix`);
export const listFixes = (findingId: number) => fetch(`/api/findings/${findingId}/fixes`).then((r) => json<Fix[]>(r));
export const openPullRequest = (fixId: number) => send<{ fix_id: number }>("POST", `/api/fixes/${fixId}/pr`);
export const getImports = (repoId: number, path: string) =>
  fetch(`/api/repos/${repoId}/imports?path=${encodeURIComponent(path)}`).then((r) => json<ImportNeighbors>(r));
