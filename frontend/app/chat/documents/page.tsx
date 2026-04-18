"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { ArrowLeft, Database, LoaderCircle, RefreshCw, Search, Trash2, Upload, ChevronDown, ChevronUp, Filter } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { Toaster } from "@/components/ui/sonner";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { DEFAULT_RAG_API_URL, resolveRagApiUrl } from "@/lib/service-urls";

type RagDocument = {
  document_id: string;
  source: string;
  chunk_count: number;
  last_updated?: string | null;
};

type RetrieveChunk = {
  text: string;
  score: number;
  metadata?: Record<string, unknown>;
};

type SearchType = "hybrid" | "semantic" | "keyword";

const ACCEPTED_EXTENSIONS = [".pdf", ".txt", ".md"];

async function readErrorMessage(response: Response): Promise<string> {
  const text = await response.text();
  if (!text) return `HTTP ${response.status}`;

  try {
    const parsed = JSON.parse(text);
    if (typeof parsed?.detail === "string") return parsed.detail;
    return text;
  } catch {
    return text;
  }
}

export default function DocumentsPage(): React.ReactNode {
  const ragApiUrl = useMemo(
    () => resolveRagApiUrl(process.env.NEXT_PUBLIC_RAG_API_URL),
    [],
  );
  const configuredRagApiUrl = process.env.NEXT_PUBLIC_RAG_API_URL || "";
  const usingFallbackRagUrl =
    configuredRagApiUrl.trim().length > 0 &&
    resolveRagApiUrl(configuredRagApiUrl) !== configuredRagApiUrl.trim();

  const [selectedFiles, setSelectedFiles] = useState<File[]>([]);
  const [uploading, setUploading] = useState(false);

  const [documents, setDocuments] = useState<RagDocument[]>([]);
  const [documentsLoading, setDocumentsLoading] = useState(true);
  const [documentsError, setDocumentsError] = useState<string | null>(null);
  const [totalChunks, setTotalChunks] = useState(0);
  const [deletingSource, setDeletingSource] = useState<string | null>(null);
  const [clearingAll, setClearingAll] = useState(false);

  // Search parameters
  const [query, setQuery] = useState("");
  const [topK, setTopK] = useState("5");
  const [searchType, setSearchType] = useState<SearchType>("hybrid");
  const [rrfSparseWeight, setRrfSparseWeight] = useState("0.3");
  const [retrieveLoading, setRetrieveLoading] = useState(false);
  const [retrieveResults, setRetrieveResults] = useState<RetrieveChunk[]>([]);
  const [expandedChunks, setExpandedChunks] = useState<Set<string>>(new Set());

  // Metadata filters
  const [filterDataCategory, setFilterDataCategory] = useState<string>("");
  const [filterTenantId, setFilterTenantId] = useState<string>("");

  // Comparison mode
  const [comparisonMode, setComparisonMode] = useState(false);
  const [comparisonResults, setComparisonResults] = useState<{
    hybrid: RetrieveChunk[];
    semantic: RetrieveChunk[];
  } | null>(null);
  const [comparisonLoading, setComparisonLoading] = useState(false);

  const sortedDocuments = useMemo(
    () => [...documents].sort((a, b) => (b.last_updated || "").localeCompare(a.last_updated || "")),
    [documents],
  );

  const sparseWeight = Math.max(0, Math.min(1, Number.parseFloat(rrfSparseWeight) || 0));
  const denseWeight = Math.max(0, Math.min(1, 1 - sparseWeight));

  const loadDocuments = useCallback(async () => {
    setDocumentsLoading(true);
    setDocumentsError(null);

    try {
      const response = await fetch(`${ragApiUrl}/documents`);
      if (!response.ok) {
        throw new Error(await readErrorMessage(response));
      }

      const payload = await response.json();
      const data = Array.isArray(payload?.data) ? payload.data : [];
      setDocuments(data);
      setTotalChunks(Number(payload?.total_chunks ?? 0));
    } catch (error) {
      const message = error instanceof Error ? error.message : "Failed to load documents.";
      setDocumentsError(message);
    } finally {
      setDocumentsLoading(false);
    }
  }, [ragApiUrl]);

  useEffect(() => {
    loadDocuments();
  }, [loadDocuments]);

  const onFileChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files ?? []);
    const acceptedFiles = files.filter((file) => {
      const lower = file.name.toLowerCase();
      return ACCEPTED_EXTENSIONS.some((ext) => lower.endsWith(ext));
    });

    const rejected = files.filter((file) => !acceptedFiles.includes(file));
    if (rejected.length > 0) {
      toast.error("Unsupported file type.", {
        description: "Only .pdf, .txt, and .md files are supported in this page.",
      });
    }

    setSelectedFiles(acceptedFiles);

    if (acceptedFiles.length > 0) {
      toast.success(`${acceptedFiles.length} file(s) selected.`);
    }
  };

  const uploadFiles = async () => {
    if (uploading) return;

    if (selectedFiles.length === 0) {
      toast.error("No files selected.", {
        description: "Choose one or more .pdf, .txt, or .md files before uploading.",
      });
      return;
    }

    setUploading(true);
    let successCount = 0;

    try {
      for (const file of selectedFiles) {
        const formData = new FormData();
        formData.append("file", file);

        const response = await fetch(`${ragApiUrl}/ingest/file`, {
          method: "POST",
          body: formData,
        });

        if (!response.ok) {
          const detail = await readErrorMessage(response);
          throw new Error(`Failed to upload ${file.name}: ${detail}`);
        }

        successCount += 1;
      }

      toast.success(`Uploaded ${successCount} file(s) to RAG.`);
      setSelectedFiles([]);
      await loadDocuments();
    } catch (error) {
      const message = error instanceof Error ? error.message : "Upload failed.";
      toast.error("Upload failed.", { description: message });
    } finally {
      setUploading(false);
    }
  };

  const deleteDocument = async (source: string) => {
    if (deletingSource) return;
    const confirmed = window.confirm(`Delete all chunks for '${source}'?`);
    if (!confirmed) return;

    setDeletingSource(source);
    try {
      const response = await fetch(`${ragApiUrl}/documents/${encodeURIComponent(source)}`, {
        method: "DELETE",
      });

      if (!response.ok) {
        throw new Error(await readErrorMessage(response));
      }

      toast.success(`Deleted '${source}'.`);
      await loadDocuments();
    } catch (error) {
      const message = error instanceof Error ? error.message : "Delete failed.";
      toast.error("Delete failed.", { description: message });
    } finally {
      setDeletingSource(null);
    }
  };

  const clearCollection = async () => {
    if (clearingAll) return;
    const confirmed = window.confirm("Delete all documents from the RAG collection?");
    if (!confirmed) return;

    setClearingAll(true);
    try {
      const response = await fetch(`${ragApiUrl}/collection`, {
        method: "DELETE",
      });

      if (!response.ok) {
        throw new Error(await readErrorMessage(response));
      }

      setRetrieveResults([]);
      setComparisonResults(null);
      toast.success("Collection reset successfully.");
      await loadDocuments();
    } catch (error) {
      const message = error instanceof Error ? error.message : "Collection reset failed.";
      toast.error("Collection reset failed.", { description: message });
    } finally {
      setClearingAll(false);
    }
  };

  const runRetrieveTest = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!query.trim() || retrieveLoading) return;

    setRetrieveLoading(true);
    setComparisonResults(null);
    try {
      const response = await fetch(`${ragApiUrl}/retrieve`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          query: query.trim(),
          top_k: Math.max(1, Number(topK) || 5),
          search_type: searchType,
          rrf_dense_weight: searchType === "hybrid" ? denseWeight : undefined,
          data_category: filterDataCategory || undefined,
          tenant_id: filterTenantId || undefined,
        }),
      });

      if (!response.ok) {
        throw new Error(await readErrorMessage(response));
      }

      const payload = await response.json();
      const chunks = Array.isArray(payload?.chunks) ? payload.chunks : [];
      setRetrieveResults(chunks);
      setComparisonResults(null);
      toast.success(`Retrieved ${chunks.length} chunk(s) using ${searchType} search.`);
    } catch (error) {
      const message = error instanceof Error ? error.message : "Retrieve failed.";
      toast.error("Retrieve failed.", { description: message });
    } finally {
      setRetrieveLoading(false);
    }
  };

  const runComparisonTest = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!query.trim() || comparisonLoading) return;

    setComparisonLoading(true);
    setRetrieveResults([]);
    try {
      const [hybridResp, semanticResp] = await Promise.all([
        fetch(`${ragApiUrl}/retrieve`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            query: query.trim(),
            top_k: Math.max(1, Number(topK) || 5),
            search_type: "hybrid",
            rrf_dense_weight: denseWeight,
          }),
        }),
        fetch(`${ragApiUrl}/retrieve`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            query: query.trim(),
            top_k: Math.max(1, Number(topK) || 5),
            search_type: "semantic",
          }),
        }),
      ]);

      if (!hybridResp.ok) {
        throw new Error(await readErrorMessage(hybridResp));
      }
      if (!semanticResp.ok) {
        throw new Error(await readErrorMessage(semanticResp));
      }

      const [hybridData, semanticData] = await Promise.all([hybridResp.json(), semanticResp.json()]);
      const hybridChunks = Array.isArray(hybridData?.chunks) ? hybridData.chunks : [];
      const semanticChunks = Array.isArray(semanticData?.chunks) ? semanticData.chunks : [];

      setComparisonResults({ hybrid: hybridChunks, semantic: semanticChunks });
      setRetrieveResults([]);
      toast.success(`Comparison complete: ${hybridChunks.length} hybrid, ${semanticChunks.length} semantic results.`);
    } catch (error) {
      const message = error instanceof Error ? error.message : "Comparison failed.";
      toast.error("Comparison failed.", { description: message });
    } finally {
      setComparisonLoading(false);
    }
  };

  const toggleChunkExpansion = (key: string) => {
    setExpandedChunks((prev) => {
      const newSet = new Set(prev);
      if (newSet.has(key)) {
        newSet.delete(key);
      } else {
        newSet.add(key);
      }
      return newSet;
    });
  };

  const ChunkCard: React.FC<{ chunk: RetrieveChunk; index: number; prefix?: string }> = ({ chunk, index, prefix }) => {
    const chunkKey = prefix ? `${prefix}-${index}` : `result-${index}`;
    const isExpanded = expandedChunks.has(chunkKey);
    
    return (
      <div className="rounded-lg border border-border bg-surface p-4">
        <div className="mb-2 flex items-center justify-between text-xs text-muted">
          <span className="font-medium">{String(chunk.metadata?.source || "unknown")}</span>
          <span>score: {Number(chunk.score).toFixed(3)}</span>
        </div>
        <p className="mb-3 line-clamp-3 text-sm text-text-main">{chunk.text}</p>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={() => toggleChunkExpansion(chunkKey)}
          className="h-8 text-xs"
        >
          {isExpanded ? (
            <>
              <ChevronUp className="mr-1 size-3" /> Hide Metadata
            </>
          ) : (
            <>
              <ChevronDown className="mr-1 size-3" /> Show Metadata
            </>
          )}
        </Button>
        {isExpanded && (
          <div className="mt-3 space-y-1 rounded bg-background p-3 text-xs">
            {Object.entries(chunk.metadata || {}).map(([key, value]) => (
              <div key={key} className="flex justify-between border-b py-1 last:border-0">
                <span className="font-medium text-muted">{key}:</span>
                <span className="text-text-main">{String(value)}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    );
  };

  return (
    <div className="min-h-screen bg-background p-6 lg:p-10">
      <Toaster />
      <div className="mx-auto flex w-full max-w-6xl flex-col gap-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="space-y-1">
            <div className="flex items-center gap-2 text-muted">
              <Database className="size-5" />
              <span className="text-sm font-medium">RAG Collection Manager</span>
            </div>
            <h1 className="text-3xl font-semibold tracking-tight text-text-main">Documents</h1>
            <p className="text-sm text-muted">
              Upload files, manage indexed documents, and test retrieval quality.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <Button
              type="button"
              variant="outline"
              onClick={loadDocuments}
              disabled={documentsLoading}
            >
              <RefreshCw className={`size-4 ${documentsLoading ? "animate-spin" : ""}`} />
              Refresh
            </Button>
            <Button
              type="button"
              variant="outline"
              asChild
            >
              <Link href="/chat">
                <ArrowLeft className="size-4" />
                Back to Chat
              </Link>
            </Button>
          </div>
        </div>

        <div className="grid gap-4 md:grid-cols-3">
          <Card>
            <CardHeader>
              <CardDescription>Total Documents</CardDescription>
              <CardTitle className="text-3xl">{documents.length}</CardTitle>
            </CardHeader>
          </Card>
          <Card>
            <CardHeader>
              <CardDescription>Total Chunks</CardDescription>
              <CardTitle className="text-3xl">{totalChunks}</CardTitle>
            </CardHeader>
          </Card>
          <Card>
            <CardHeader>
              <CardDescription>RAG API URL</CardDescription>
              <CardTitle className="truncate text-base">{ragApiUrl}</CardTitle>
            </CardHeader>
          </Card>
        </div>

        {usingFallbackRagUrl && (
          <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-sm text-amber-400">
            NEXT_PUBLIC_RAG_API_URL is invalid (<strong>{configuredRagApiUrl}</strong>). Using fallback <strong>{DEFAULT_RAG_API_URL}</strong>.
          </div>
        )}

        <div className="grid gap-6 lg:grid-cols-[1.1fr_1fr]">
          <Card>
            <CardHeader>
              <CardTitle>Upload to RAG</CardTitle>
              <CardDescription>Supported: PDF and UTF-8 text files.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid gap-2">
                <Label htmlFor="rag-file-upload">Files</Label>
                <Input
                  id="rag-file-upload"
                  type="file"
                  multiple
                  accept=".pdf,.txt,.md,text/plain,application/pdf"
                  onChange={onFileChange}
                />
                <p className="text-xs text-muted">
                  Choose one or more files, then click Upload Files.
                </p>
              </div>

              {selectedFiles.length > 0 && (
                <div className="rounded-lg border border-border bg-surface p-3">
                  <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">
                    Ready to upload
                  </p>
                  <ul className="space-y-1 text-sm text-text-main">
                    {selectedFiles.map((file) => (
                      <li key={`${file.name}-${file.size}`}>{file.name}</li>
                    ))}
                  </ul>
                </div>
              )}

              <Button
                type="button"
                onClick={uploadFiles}
                disabled={uploading}
              >
                {uploading ? (
                  <LoaderCircle className="size-4 animate-spin" />
                ) : (
                  <Upload className="size-4" />
                )}
                {uploading ? "Uploading..." : "Upload Files"}
              </Button>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Retrieval Test</CardTitle>
              <CardDescription>Run a query to validate indexed context.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid gap-2">
                <Label htmlFor="search-type">Search Type</Label>
                <Select value={searchType} onValueChange={(v: SearchType) => setSearchType(v)}>
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="hybrid">
                      Hybrid (Dense + Sparse)
                    </SelectItem>
                    <SelectItem value="semantic">
                      Semantic (Dense only)
                    </SelectItem>
                    <SelectItem value="keyword">
                      Keyword (Sparse only)
                    </SelectItem>
                  </SelectContent>
                </Select>
              </div>

              {searchType === "hybrid" && (
                <div className="grid gap-2 rounded-lg border border-border bg-surface p-3">
                  <Label className="text-xs font-semibold uppercase tracking-wide text-muted">
                    RRF Weights (Hybrid Search)
                  </Label>
                  <div className="flex items-center gap-3">
                    <div className="flex-1">
                      <div className="flex justify-between text-xs">
                        <span>Dense: {denseWeight.toFixed(2)}</span>
                        <span>Sparse: {sparseWeight.toFixed(2)}</span>
                      </div>
                      <input
                        type="range"
                        min="0"
                        max="1"
                        step="0.1"
                        value={rrfSparseWeight}
                        onChange={(e) => setRrfSparseWeight(e.target.value)}
                        className="mt-2 w-full"
                      />
                    </div>
                  </div>
                </div>
              )}

              <div className="grid gap-2">
                <Label htmlFor="retrieve-query">Query</Label>
                <Input
                  id="retrieve-query"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="Ask about your uploaded documents..."
                />
              </div>
              <div className="grid gap-2">
                <Label htmlFor="retrieve-topk">Top K</Label>
                <Input
                  id="retrieve-topk"
                  value={topK}
                  onChange={(e) => setTopK(e.target.value)}
                  inputMode="numeric"
                />
              </div>

              <Separator />

              <div className="grid gap-2 rounded-lg border border-border bg-surface p-3">
                <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-muted">
                  <Filter className="size-3" />
                  <span>Metadata Filters</span>
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <Label htmlFor="filter-category" className="text-xs">Data Category</Label>
                    <Input
                      id="filter-category"
                      value={filterDataCategory}
                      onChange={(e) => setFilterDataCategory(e.target.value)}
                      placeholder="e.g., technical"
                      className="text-xs"
                    />
                  </div>
                  <div>
                    <Label htmlFor="filter-tenant" className="text-xs">Tenant ID</Label>
                    <Input
                      id="filter-tenant"
                      value={filterTenantId}
                      onChange={(e) => setFilterTenantId(e.target.value)}
                      placeholder="e.g., user_1"
                      className="text-xs"
                    />
                  </div>
                </div>
              </div>

              <div className="flex gap-2">
                <Button
                  type="button"
                  onClick={runRetrieveTest}
                  disabled={retrieveLoading || !query.trim()}
                  className="flex-1"
                >
                  {retrieveLoading ? (
                    <LoaderCircle className="size-4 animate-spin" />
                  ) : (
                    <Search className="size-4" />
                  )}
                  {retrieveLoading ? "Searching..." : "Test Retrieval"}
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => setComparisonMode(!comparisonMode)}
                  className={comparisonMode ? "bg-surface text-text-main" : ""}
                >
                  Compare
                </Button>
              </div>

              {comparisonMode && (
                <Button
                  type="button"
                  onClick={runComparisonTest}
                  disabled={comparisonLoading || !query.trim()}
                  className="w-full"
                >
                  {comparisonLoading ? (
                    <LoaderCircle className="size-4 animate-spin" />
                  ) : (
                    <Search className="size-4" />
                  )}
                  Compare Hybrid vs Semantic
                </Button>
              )}
            </CardContent>
          </Card>
        </div>

        {(retrieveResults.length > 0 || comparisonResults) && (
          <Card>
            <CardHeader>
              <CardTitle>
                {comparisonResults ? "Search Type Comparison" : `Retrieval Results (${searchType})`}
              </CardTitle>
              <CardDescription>
                {comparisonResults
                  ? "Side-by-side comparison of hybrid and semantic search results"
                  : `Found ${retrieveResults.length} chunk(s) using ${searchType} search`}
              </CardDescription>
            </CardHeader>
            <CardContent>
              {comparisonResults ? (
                <div className="grid gap-6 lg:grid-cols-2">
                  <div>
                    <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-primary">
                      <span className="size-3 rounded-full bg-primary" />
                      Hybrid Search Results ({comparisonResults.hybrid.length})
                    </h3>
                    <div className="space-y-3">
                      {comparisonResults.hybrid.map((chunk, index) => (
                        <ChunkCard key={index} chunk={chunk} index={index} prefix="hybrid" />
                      ))}
                    </div>
                  </div>
                  <div>
                    <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-accent">
                      <span className="size-3 rounded-full bg-accent" />
                      Semantic Search Results ({comparisonResults.semantic.length})
                    </h3>
                    <div className="space-y-3">
                      {comparisonResults.semantic.map((chunk, index) => (
                        <ChunkCard key={index} chunk={chunk} index={index} prefix="semantic" />
                      ))}
                    </div>
                  </div>
                </div>
              ) : (
                <div className="space-y-3">
                  {retrieveResults.map((chunk, index) => (
                    <ChunkCard key={index} chunk={chunk} index={index} />
                  ))}
                </div>
              )}
            </CardContent>
          </Card>
        )}

        <Card>
          <CardHeader>
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <CardTitle>Indexed Documents</CardTitle>
                <CardDescription>
                  Delete individual sources or reset the entire collection.
                </CardDescription>
              </div>
              <Button
                type="button"
                variant="destructive"
                onClick={clearCollection}
                disabled={clearingAll || documents.length === 0}
              >
                {clearingAll ? (
                  <LoaderCircle className="size-4 animate-spin" />
                ) : (
                  <Trash2 className="size-4" />
                )}
                {clearingAll ? "Deleting..." : "Delete All"}
              </Button>
            </div>
          </CardHeader>
          <CardContent>
            {documentsLoading ? (
              <p className="text-sm text-muted">Loading documents...</p>
            ) : documentsError ? (
              <div className="flex items-center justify-between gap-3 rounded-lg border border-danger/40 bg-danger/10 p-3">
                <p className="text-sm text-rose-700">{documentsError}</p>
                <Button
                  type="button"
                  variant="outline"
                  onClick={loadDocuments}
                >
                  Retry
                </Button>
              </div>
            ) : sortedDocuments.length === 0 ? (
              <p className="text-sm text-muted">No documents indexed yet.</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[640px] text-sm">
                  <thead>
                    <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-muted">
                      <th className="py-2 pr-3">Source</th>
                      <th className="py-2 pr-3">Chunks</th>
                      <th className="py-2 pr-3">Last Updated</th>
                      <th className="py-2">Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {sortedDocuments.map((doc) => (
                      <tr
                        key={doc.document_id}
                        className="border-b border-border"
                      >
                        <td className="py-3 pr-3 font-medium text-text-main">{doc.source}</td>
                        <td className="py-3 pr-3 text-muted">{doc.chunk_count}</td>
                        <td className="py-3 pr-3 text-muted">
                          {doc.last_updated
                            ? new Date(doc.last_updated).toLocaleString()
                            : "-"}
                        </td>
                        <td className="py-3">
                          <Button
                            type="button"
                            size="sm"
                            variant="outline"
                            onClick={() => deleteDocument(doc.source)}
                            disabled={deletingSource === doc.source}
                          >
                            {deletingSource === doc.source ? (
                              <LoaderCircle className="size-4 animate-spin" />
                            ) : (
                              <Trash2 className="size-4" />
                            )}
                            Delete
                          </Button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
