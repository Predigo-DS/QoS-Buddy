export type ModelOption = {
  id: string;
  provider: string;
  base_url: string;
  display_name?: string;
  description?: string;
};

type ModelCacheEntry = {
  models: ModelOption[];
  expiresAt: number;
};

const MODEL_CACHE_KEY = "agent-models-cache-v1";
const DEFAULT_MODEL_CACHE_TTL_MS = 5 * 60 * 1000;

let inMemoryCache: ModelCacheEntry | null = null;
let inFlightRefresh: Promise<ModelOption[]> | null = null;
let inFlightRefreshUrl: string | null = null;

function getModelCacheTtlMs(): number {
  const raw = process.env.NEXT_PUBLIC_MODEL_CACHE_TTL_MS;
  const parsed = Number(raw);
  if (Number.isFinite(parsed) && parsed > 0) {
    return Math.floor(parsed);
  }
  return DEFAULT_MODEL_CACHE_TTL_MS;
}

function parseModels(payload: unknown): ModelOption[] {
  if (!payload || typeof payload !== "object") return [];
  const data = (payload as { data?: unknown }).data;
  if (!Array.isArray(data)) return [];

  return data
    .filter((item): item is Record<string, unknown> => {
      return (
        !!item &&
        typeof item === "object" &&
        typeof (item as Record<string, unknown>).id === "string"
      );
    })
    .map((item) => ({
      id: String(item.id),
      provider: typeof item.provider === "string" ? item.provider : "unknown",
      base_url: typeof item.base_url === "string" ? item.base_url : "",
      display_name:
        typeof item.display_name === "string" ? item.display_name : undefined,
      description:
        typeof item.description === "string" ? item.description : undefined,
    }));
}

function canUseStorage(): boolean {
  return typeof window !== "undefined" && !!window.localStorage;
}

function readStorageCache(): ModelCacheEntry | null {
  if (!canUseStorage()) return null;
  try {
    const raw = window.localStorage.getItem(MODEL_CACHE_KEY);
    if (!raw) return null;

    const parsed = JSON.parse(raw) as Partial<ModelCacheEntry>;
    if (!Array.isArray(parsed.models) || typeof parsed.expiresAt !== "number") {
      return null;
    }

    const normalized: ModelCacheEntry = {
      models: parsed.models.filter(
        (m): m is ModelOption =>
          !!m &&
          typeof m === "object" &&
          typeof (m as ModelOption).id === "string" &&
          typeof (m as ModelOption).provider === "string" &&
          typeof (m as ModelOption).base_url === "string",
      ),
      expiresAt: parsed.expiresAt,
    };

    if (!normalized.models.length) return null;
    return normalized;
  } catch {
    return null;
  }
}

function writeStorageCache(entry: ModelCacheEntry): void {
  if (!canUseStorage()) return;
  try {
    window.localStorage.setItem(MODEL_CACHE_KEY, JSON.stringify(entry));
  } catch {
    // Ignore storage failures.
  }
}

function setCache(models: ModelOption[]): void {
  const entry: ModelCacheEntry = {
    models,
    expiresAt: Date.now() + getModelCacheTtlMs(),
  };
  inMemoryCache = entry;
  writeStorageCache(entry);
}

export function clearModelCache(): void {
  inMemoryCache = null;
  if (!canUseStorage()) return;
  try {
    window.localStorage.removeItem(MODEL_CACHE_KEY);
  } catch {
    // Ignore storage failures.
  }
}

export function getCachedModels(): ModelOption[] | null {
  if (inMemoryCache && inMemoryCache.expiresAt > Date.now()) {
    return inMemoryCache.models;
  }

  const fromStorage = readStorageCache();
  if (!fromStorage) {
    return null;
  }

  if (fromStorage.expiresAt <= Date.now()) {
    clearModelCache();
    return null;
  }

  inMemoryCache = fromStorage;
  return fromStorage.models;
}

async function fetchModelsFromApi(agentApiUrl: string): Promise<ModelOption[]> {
  const resp = await fetch(`${agentApiUrl}/models`);
  if (!resp.ok) {
    throw new Error(`Failed to load models (${resp.status})`);
  }
  const payload = await resp.json();
  const parsed = parseModels(payload);
  if (!parsed.length) {
    throw new Error("No models were returned by the backend.");
  }
  return parsed;
}

export async function refreshModels(agentApiUrl: string): Promise<ModelOption[]> {
  if (inFlightRefresh && inFlightRefreshUrl === agentApiUrl) {
    return inFlightRefresh;
  }

  inFlightRefreshUrl = agentApiUrl;
  inFlightRefresh = fetchModelsFromApi(agentApiUrl)
    .then((models) => {
      setCache(models);
      return models;
    })
    .finally(() => {
      inFlightRefresh = null;
      inFlightRefreshUrl = null;
    });

  return inFlightRefresh;
}

export async function prefetchModels(agentApiUrl: string): Promise<ModelOption[]> {
  const cached = getCachedModels();
  if (cached?.length) return cached;
  return refreshModels(agentApiUrl);
}