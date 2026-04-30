const DEFAULT_AGENT_API_URL = "http://localhost:8002";
const DEFAULT_RAG_API_URL = "http://localhost:8001";

function resolveApiOrigin(raw: string | undefined, fallback: string): string {
  const candidate = (raw || "").trim();
  if (!candidate) return fallback;

  try {
    return new URL(candidate).origin;
  } catch {
    return fallback;
  }
}

export function resolveAgentApiUrl(raw: string | undefined): string {
  return resolveApiOrigin(raw, DEFAULT_AGENT_API_URL);
}

export function resolveRagApiUrl(raw: string | undefined): string {
  return resolveApiOrigin(raw, DEFAULT_RAG_API_URL);
}

export { DEFAULT_AGENT_API_URL, DEFAULT_RAG_API_URL };