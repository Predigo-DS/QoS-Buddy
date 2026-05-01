"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { ArrowLeft, Database, LoaderCircle, RefreshCw, Search, Trash2, ChevronDown, ChevronUp, Filter, FileJson, Upload, CheckCircle2, XCircle } from "lucide-react";
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
  content_type?: string;
  avg_quality?: number;
};

type RetrieveChunk = {
  text: string;
  score: number;
  metadata?: Record<string, unknown>;
  rerank_score?: number | null;
  is_reranked?: boolean;
};

type SearchType = "hybrid" | "semantic" | "keyword";

type BatchIngestResult = {
  total_documents: number;
  ingested_documents: number;
  total_chunks: number;
  sources: Record<string, number>;
  errors: Array<{ index: number; error?: string; source?: string }>;
  document_details?: Array<{
    index: number;
    source: string;
    title: string;
    status: string;
    chunks_created?: number;
    chunks_filtered?: number;
    chunk_previews?: string[];
    reformulated?: boolean;
    error?: string;
  }>;
};

const ACCEPTED_EXTENSIONS = [".pdf", ".txt", ".md", ".json"];

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

  // JSON upload state
  const [jsonFile, setJsonFile] = useState<File | null>(null);
  const [jsonPreview, setJsonPreview] = useState<{
    count: number;
    sample?: Record<string, unknown>;
    sampleMetadata?: Record<string, unknown>;
  } | null>(null);
  const [jsonUploading, setJsonUploading] = useState(false);
  const [jsonProgress, setJsonProgress] = useState<{
    current: number;
    total: number;
    results?: BatchIngestResult;
    status: "processing" | "complete" | "error";
  } | null>(null);

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
  const [filterContentType, setFilterContentType] = useState<string>("");
  const [filterVendor, setFilterVendor] = useState<string>("");
  const [filterQualityScore, setFilterQualityScore] = useState<string>("");
  const [filterStatus, setFilterStatus] = useState<string>("");

  const [availableFilters, setAvailableFilters] = useState<{
    contentTypes: string[];
    vendors: string[];
  }>({ contentTypes: [], vendors: [] });

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

      // Extract available filter values from documents
      const contentTypes = new Set<string>();
      const vendors = new Set<string>();
      for (const doc of data) {
        const meta = (doc as any).metadata;
        if (meta?.content_type) contentTypes.add(meta.content_type);
        if (meta?.vendor) vendors.add(meta.vendor);
      }
      setAvailableFilters({
        contentTypes: Array.from(contentTypes).sort(),
        vendors: Array.from(vendors).sort(),
      });
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

  // JSON file preview
  const handleJsonFileSelect = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const files = event.target.files;
    if (!files || files.length === 0) return;

    const file = files[0];
    if (!file.name.toLowerCase().endsWith(".json")) {
      toast.error("Not a JSON file.", { description: "Please select a .json file." });
      return;
    }

    try {
      const text = await file.text();
      const parsed = JSON.parse(text);

      if (!Array.isArray(parsed)) {
        toast.error("Invalid JSON format.", { description: "Expected a JSON array of documents." });
        return;
      }

      const count = parsed.length;
      let sample: Record<string, unknown> | undefined;
      let sampleMetadata: Record<string, unknown> | undefined;

      if (count > 0) {
        sample = parsed[0] as Record<string, unknown>;
        sampleMetadata = (sample?.metadata || {}) as Record<string, unknown>;
      }

      setJsonFile(file);
      setJsonPreview({ count, sample, sampleMetadata });
      toast.success(`${count} document(s) found in file.`);
    } catch {
      toast.error("Invalid JSON.", { description: "Could not parse the file as JSON." });
    }
  };

  const ingestJsonFile = async () => {
    if (!jsonFile || jsonUploading) return;

    setJsonUploading(true);
    setJsonProgress({ current: 0, total: 0, status: "processing" });

    try {
      const text = await jsonFile.text();
      const documents = JSON.parse(text);

      if (!Array.isArray(documents)) {
        throw new Error("Expected a JSON array of documents.");
      }

      setJsonProgress({ current: 0, total: documents.length, status: "processing" });

      const response = await fetch(`${ragApiUrl}/ingest/batch`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ documents }),
      });

      if (!response.ok) {
        const detail = await readErrorMessage(response);
        throw new Error(`Batch ingestion failed: ${detail}`);
      }

      const result: BatchIngestResult = await response.json();

      setJsonProgress({
        current: result.ingested_documents,
        total: result.total_documents,
        results: result,
        status: result.errors.length > 0 ? "error" : "complete",
      });

      if (result.errors.length > 0) {
        toast.warning(`Ingested ${result.ingested_documents}/${result.total_documents} documents (${result.errors.length} errors).`, {
          description: `Generated ${result.total_chunks} chunks. Check details below.`,
        });
      } else {
        toast.success(`Ingested ${result.ingested_documents} documents → ${result.total_chunks} chunks.`);
      }

      await loadDocuments();
    } catch (error) {
      const message = error instanceof Error ? error.message : "Batch ingestion failed.";
      setJsonProgress({ current: 0, total: jsonPreview?.count || 0, status: "error" });
      toast.error("Batch ingestion failed.", { description: message });
    } finally {
      setJsonUploading(false);
    }
  };

  const onFileChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files ?? []);
    const acceptedFiles = files.filter((file) => {
      const lower = file.name.toLowerCase();
      return ACCEPTED_EXTENSIONS.some((ext) => lower.endsWith(ext));
    });

    const rejected = files.filter((file) => !acceptedFiles.includes(file));
    if (rejected.length > 0) {
      toast.error("Unsupported file type.", {
        description: "Only .pdf, .txt, .md, and .json files are supported.",
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
        description: "Choose one or more .pdf, .txt, .md, or .json files before uploading.",
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
      const body: Record<string, unknown> = {
        query: query.trim(),
        top_k: Math.max(1, Number(topK) || 5),
        search_type: searchType,
      };

      if (searchType === "hybrid") {
        body.rrf_dense_weight = denseWeight;
      }
      if (filterDataCategory) body.tenant_id = filterDataCategory;
      if (filterTenantId) body.tenant_id = filterTenantId;
     if (filterTenantId) body.tenant_id = filterTenantId;
      if (filterContentType && filterContentType !== "all") body.content_type = filterContentType;
      if (filterVendor && filterVendor !== "all") body.vendor = filterVendor;
      if (filterQualityScore && filterQualityScore !== "all") body.min_quality_score = Number(filterQualityScore);
      if (filterStatus && filterStatus !== "all") body.status = filterStatus;

      const response = await fetch(`${ragApiUrl}/retrieve`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
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
      const baseBody: Record<string, unknown> = {
        query: query.trim(),
        top_k: Math.max(1, Number(topK) || 5),
      };
      if (filterContentType && filterContentType !== "all") baseBody.content_type = filterContentType;
      if (filterVendor && filterVendor !== "all") baseBody.vendor = filterVendor;
      if (filterQualityScore && filterQualityScore !== "all") baseBody.min_quality_score = Number(filterQualityScore);
      if (filterStatus && filterStatus !== "all") baseBody.status = filterStatus;

      const [hybridResp, semanticResp] = await Promise.all([
        fetch(`${ragApiUrl}/retrieve`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ...baseBody, search_type: "hybrid", rrf_dense_weight: denseWeight }),
        }),
        fetch(`${ragApiUrl}/retrieve`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ...baseBody, search_type: "semantic" }),
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
              Upload files or JSON documents, manage indexed documents, and test retrieval quality.
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
              <CardDescription>Supported: PDF, text files, and JSON document arrays.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              {/* Standard file upload */}
              <div className="grid gap-2">
                <Label htmlFor="rag-file-upload">Files (.pdf, .txt, .md)</Label>
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
                disabled={uploading || selectedFiles.length === 0}
              >
                {uploading ? (
                  <LoaderCircle className="size-4 animate-spin" />
                ) : (
                  <Upload className="size-4" />
                )}
                {uploading ? "Uploading..." : "Upload Files"}
              </Button>

              <Separator />

              {/* JSON upload */}
              <div className="grid gap-2">
                <Label htmlFor="json-file-upload">
                  <span className="flex items-center gap-1">
                    <FileJson className="size-3" />
                    JSON Documents (.json)
                  </span>
                </Label>
                <Input
                  id="json-file-upload"
                  type="file"
                  accept=".json,application/json"
                  onChange={handleJsonFileSelect}
                  disabled={jsonUploading}
                />
                <p className="text-xs text-muted">
                  Upload a JSON array of documents (e.g., from the preparer pipeline).
                </p>
              </div>

              {jsonPreview && (
                <div className="rounded-lg border border-border bg-surface p-3">
                  <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">
                    Preview
                  </p>
                  <div className="space-y-2">
                    <div className="flex items-center justify-between text-sm">
                      <span className="text-text-main">{jsonFile?.name}</span>
                      <span className="text-muted">{jsonPreview.count} document(s)</span>
                    </div>
                    {jsonPreview.sampleMetadata && Object.keys(jsonPreview.sampleMetadata).length > 0 && (
                      <div className="rounded bg-background p-2 text-xs">
                        <p className="mb-1 font-medium text-muted">Sample metadata fields:</p>
                        <div className="space-y-0.5">
                          {Object.entries(jsonPreview.sampleMetadata).slice(0, 5).map(([key, value]) => (
                            <div key={key} className="flex justify-between">
                              <span className="text-muted">{key}:</span>
                              <span className="text-text-main truncate ml-4 max-w-[200px]">
                                {Array.isArray(value) ? `[${value.length}]` : String(value).slice(0, 60)}
                              </span>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                </div>
              )}

              {jsonProgress && (
                <div className="rounded-lg border border-border bg-surface p-3">
                  <div className="mb-2 flex items-center justify-between">
                    <span className="text-sm font-medium">
                      {jsonProgress.status === "processing" && "Ingesting..."}
                      {jsonProgress.status === "complete" && "Ingestion complete"}
                      {jsonProgress.status === "error" && "Ingestion failed"}
                    </span>
                    <span className="text-xs text-muted">
                      {jsonProgress.current} / {jsonProgress.total}
                    </span>
                  </div>

                  {jsonProgress.status === "processing" && (
                    <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
                      <div
                        className="h-full bg-primary transition-all"
                        style={{ width: `${jsonProgress.total > 0 ? (jsonProgress.current / jsonProgress.total) * 100 : 0}%` }}
                      />
                    </div>
                  )}

                  {jsonProgress.results && jsonProgress.status !== "processing" && (
                    <div className="mt-3 space-y-2">
                      <div className="flex items-center gap-2 text-sm">
                        <CheckCircle2 className="size-4 text-green-500" />
                        <span>{jsonProgress.results.ingested_documents} ingested</span>
                      </div>
                      <div className="flex items-center gap-2 text-sm">
                        <CheckCircle2 className="size-4 text-blue-500" />
                        <span>{jsonProgress.results.total_chunks} chunks</span>
                      </div>
                      {jsonProgress.results.errors.length > 0 && (
                        <div className="flex items-center gap-2 text-sm">
                          <XCircle className="size-4 text-red-500" />
                          <span>{jsonProgress.results.errors.length} errors</span>
                        </div>
                      )}

                      {jsonProgress.results.errors.length > 0 && (
                         <details className="mt-2">
                           <summary className="cursor-pointer text-xs text-muted hover:text-text-main">
                             Show errors
                           </summary>
                           <ul className="mt-1 space-y-1 text-xs text-red-400">
                             {jsonProgress.results.errors.slice(0, 10).map((err, i) => (
                               <li key={i}>
                                 Doc #{err.index}: {err.error}
                                 {err.source && ` (${err.source})`}
                               </li>
                             ))}
                           </ul>
                         </details>
                       )}

                       {jsonProgress.results.document_details && jsonProgress.results.document_details.length > 0 && (
                         <details className="mt-3">
                           <summary className="cursor-pointer text-xs text-muted hover:text-text-main">
                             Show ingestion details ({jsonProgress.results.document_details.length} documents)
                           </summary>
                           <div className="mt-2 max-h-60 overflow-y-auto space-y-1 text-xs border border-border rounded p-2">
                             {jsonProgress.results.document_details.map((detail) => (
                               <div key={detail.index} className={`p-2 rounded border ${detail.status === 'error' ? 'border-red-500/30 bg-red-500/5' : 'border-border/50 bg-background/50'}`}>
                                 <div className="flex items-center justify-between">
                                   <span className="font-medium truncate max-w-[200px]">{detail.title || `Doc #${detail.index}`}</span>
                                   <div className="flex items-center gap-2 flex-shrink-0">
                                     {detail.reformulated !== undefined && (
                                       <span className={detail.reformulated ? 'text-green-400' : 'text-orange-400'}>
                                         {detail.reformulated ? '✓' : '⚠'}
                                       </span>
                                     )}
                                     {detail.chunks_created !== undefined && (
                                       <span className="text-muted">{detail.chunks_created} chunks{detail.chunks_filtered > 0 ? `, ${detail.chunks_filtered} filtered` : ''}</span>
                                     )}
                                   </div>
                                 </div>
                                 {detail.chunk_previews?.length > 0 && (
                                   <p className="mt-1 text-muted line-clamp-2">{detail.chunk_previews[0]}</p>
                                 )}
                                 {detail.error && (
                                   <p className="mt-1 text-red-400">{detail.error}</p>
                                 )}
                               </div>
                             ))}
                           </div>
                           <div className="mt-2">
                             <button
                               onClick={() => {
                                 const blob = new Blob([JSON.stringify(jsonProgress.results.document_details, null, 2)], { type: 'application/json' });
                                 const url = URL.createObjectURL(blob);
                                 const a = document.createElement('a');
                                 a.href = url;
                                 a.download = 'ingestion-log.json';
                                 a.click();
                                 URL.revokeObjectURL(url);
                               }}
                               className="text-xs text-muted hover:text-text-main underline"
                             >
                               Download ingestion log (JSON)
                             </button>
                           </div>
                         </details>
                       )}
                     </div>
                   )}
                </div>
              )}

              <Button
                type="button"
                onClick={ingestJsonFile}
                disabled={jsonUploading || !jsonFile}
                className="w-full"
              >
                {jsonUploading ? (
                  <LoaderCircle className="size-4 animate-spin" />
                ) : (
                  <FileJson className="size-4" />
                )}
                {jsonUploading ? "Ingesting..." : "Ingest JSON Documents"}
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
                    <Label htmlFor="filter-tenant" className="text-xs">Tenant ID</Label>
                    <Input
                      id="filter-tenant"
                      value={filterTenantId}
                      onChange={(e) => setFilterTenantId(e.target.value)}
                      placeholder="e.g., user_1"
                      className="text-xs"
                    />
                  </div>
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
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <Label htmlFor="filter-content-type" className="text-xs">Content Type</Label>
                    <Select value={filterContentType} onValueChange={setFilterContentType}>
                      <SelectTrigger id="filter-content-type">
                        <SelectValue placeholder="All types" />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="all">All types</SelectItem>
                        {availableFilters.contentTypes.map((ct) => (
                          <SelectItem key={ct} value={ct}>{ct}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <div>
                    <Label htmlFor="filter-vendor" className="text-xs">Vendor</Label>
                    <Select value={filterVendor} onValueChange={setFilterVendor}>
                      <SelectTrigger id="filter-vendor">
                        <SelectValue placeholder="All vendors" />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="all">All vendors</SelectItem>
                        {availableFilters.vendors.map((v) => (
                          <SelectItem key={v} value={v}>{v}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <Label htmlFor="filter-quality" className="text-xs">Min Quality Score</Label>
                    <Select value={filterQualityScore} onValueChange={setFilterQualityScore}>
                      <SelectTrigger id="filter-quality">
                        <SelectValue placeholder="No filter" />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="all">No filter</SelectItem>
                        {[1,2,3,4,5,6,7,8,9,10].map((s) => (
                          <SelectItem key={s} value={String(s)}>{s}+</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <div>
                    <Label htmlFor="filter-status" className="text-xs">Status</Label>
                    <Select value={filterStatus} onValueChange={setFilterStatus}>
                      <SelectTrigger id="filter-status">
                        <SelectValue placeholder="All" />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="all">All</SelectItem>
                        <SelectItem value="verified">Verified</SelectItem>
                        <SelectItem value="needs_review">Needs Review</SelectItem>
                      </SelectContent>
                    </Select>
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
                      <th className="py-2 pr-3">Content Type</th>
                      <th className="py-2 pr-3">Avg Quality</th>
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
                          {(doc as any).content_type || "-"}
                        </td>
                        <td className="py-3 pr-3 text-muted">
                          {(doc as any).avg_quality != null ? (doc as any).avg_quality.toFixed(1) : "-"}
                        </td>
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
