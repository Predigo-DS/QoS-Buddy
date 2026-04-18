import { useState } from "react";
import { Card, CardContent } from "../ui/card";
import { Button } from "../ui/button";
import { Badge } from "../ui/badge";
import { Separator } from "../ui/separator";
import { ChevronDown, ChevronUp, FileText, Search, Sparkles } from "lucide-react";
import { cn } from "@/lib/utils";

type Source = {
  text: string;
  score: number;
  metadata: {
    source?: string;
    ingested_at?: string;
    [key: string]: any;
  };
};

type SourceDisplayProps = {
  sources: Source[];
  searchType?: string;
  rewrittenQueries?: string[] | null;
  originalQuery?: string;
};

function getRelevanceColor(score: number): string {
  if (score >= 0.8) return "bg-accent";
  if (score >= 0.6) return "bg-primary";
  if (score >= 0.4) return "bg-orange-400";
  return "bg-muted";
}

function getRelevanceLabel(score: number): string {
  if (score >= 0.8) return "High";
  if (score >= 0.6) return "Medium";
  return "Low";
}

function groupSourcesByDocument(sources: Source[]): Map<string, Source[]> {
  const grouped = new Map<string, Source[]>();
  
  for (const source of sources) {
    const docName = source.metadata?.source || "Unknown Document";
    if (!grouped.has(docName)) {
      grouped.set(docName, []);
    }
    grouped.get(docName)!.push(source);
  }
  
  return grouped;
}

export function SourceDisplay({ sources, searchType, rewrittenQueries, originalQuery }: SourceDisplayProps) {
  const [expanded, setExpanded] = useState(false);
  const [expandedSources, setExpandedSources] = useState<Set<string>>(new Set());
  
  const groupedSources = groupSourcesByDocument(sources);
  const totalSources = sources.length;
  const uniqueDocuments = groupedSources.size;

  const toggleExpand = (sourceKey: string) => {
    const newExpanded = new Set(expandedSources);
    if (newExpanded.has(sourceKey)) {
      newExpanded.delete(sourceKey);
    } else {
      newExpanded.add(sourceKey);
    }
    setExpandedSources(newExpanded);
  };

  const searchTypeLabel = searchType === "hybrid" 
    ? "Hybrid Search" 
    : searchType === "semantic"
    ? "Semantic Search"
    : "Keyword Search";

  return (
    <Card className="mt-3 border-border bg-surface">
      <CardContent className="p-0">
        <Button
          variant="ghost"
          onClick={() => setExpanded(!expanded)}
          className="w-full justify-between px-4 py-3 hover:bg-surface"
        >
          <div className="flex items-center gap-2">
            <FileText className="h-4 w-4 text-primary" />
            <span className="font-medium text-text-main">
              Sources ({totalSources} chunks from {uniqueDocuments} document{uniqueDocuments !== 1 ? "s" : ""})
            </span>
          </div>
          <div className="flex items-center gap-2">
            <Badge variant="secondary" className="text-xs">
              {searchTypeLabel}
            </Badge>
            {expanded ? (
              <ChevronUp className="h-4 w-4 text-primary" />
            ) : (
              <ChevronDown className="h-4 w-4 text-primary" />
            )}
          </div>
        </Button>

        {expanded && (
          <div className="border-t border-border">
            {/* Show rewritten queries if available */}
            {rewrittenQueries && rewrittenQueries.length > 0 && (
              <div className="border-b border-border px-4 py-3 bg-background">
                <div className="flex items-start gap-2 text-sm">
                  <Sparkles className="h-4 w-4 text-primary mt-0.5" />
                  <div className="flex-1">
                    <p className="font-medium text-text-main mb-1">Query optimized for better results:</p>
                    <div className="space-y-1">
                      {originalQuery && (
                        <p className="text-xs text-muted italic">Original: "{originalQuery}"</p>
                      )}
                      {rewrittenQueries.map((rq, idx) => (
                        <p key={idx} className="text-xs text-primary">
                          → "{rq}"
                        </p>
                      ))}
                    </div>
                  </div>
                </div>
              </div>
            )}
            
            {Array.from(groupedSources.entries()).map(([docName, docSources], idx) => (
              <div key={docName}>
                {idx > 0 && <Separator className="my-3" />}
                
                <div className="px-4 py-2">
                  <h4 className="font-semibold text-sm text-text-main flex items-center gap-2">
                    <FileText className="h-3 w-3 text-primary" />
                    {docName}
                    <Badge variant="outline" className="ml-auto text-xs">
                      {docSources.length} chunk{docSources.length !== 1 ? "s" : ""}
                    </Badge>
                  </h4>
                </div>

                <div className="px-4 pb-3 space-y-3">
                  {docSources.map((source, sourceIdx) => {
                    const key = `${docName}-${sourceIdx}`;
                    const isExpanded = expandedSources.has(key);
                    const rawPercentage = source.score * 100;
                    const percentage = isNaN(rawPercentage) ? 0 : Math.round(rawPercentage);
                    const relevanceColor = getRelevanceColor(source.score);
                    const relevanceLabel = getRelevanceLabel(source.score);

                    return (
                      <div
                        key={key}
                        className="border border-border rounded-lg overflow-hidden bg-background"
                      >
                        <Button
                          variant="ghost"
                          onClick={() => toggleExpand(key)}
                          className="w-full justify-between px-3 py-2.5 text-left hover:bg-background"
                        >
                          <div className="flex items-center gap-2 flex-1">
                            <div className="flex items-center gap-2">
                              <div
                                className={cn(
                                  "h-2 w-2 rounded-full",
                                  relevanceColor
                                )}
                                title={`${percentage}% relevance`}
                              />
                              <span className="text-sm font-medium text-text-main">
                                Chunk #{sourceIdx + 1}
                              </span>
                            </div>
                            <span className="text-xs text-muted truncate max-w-[200px]">
                              {source.text}
                            </span>
                          </div>
                          <div className="flex items-center gap-2">
                            <Badge
                              className={cn(
                                "text-xs",
                                percentage >= 80 && "bg-accent/10 text-accent hover:bg-accent/10",
                                percentage >= 60 && percentage < 80 && "bg-primary/10 text-primary hover:bg-primary/10",
                                percentage < 60 && "bg-orange-400/10 text-orange-400 hover:bg-orange-400/10"
                              )}
                            >
                              {percentage}%
                            </Badge>
                            {isExpanded ? (
                              <ChevronUp className="h-3 w-3 text-muted" />
                            ) : (
                              <ChevronDown className="h-3 w-3 text-muted" />
                            )}
                          </div>
                        </Button>

                        {isExpanded && (
                          <div className="border-t border-border px-3 py-3 bg-background">
                            <div className="flex items-center gap-2 mb-2">
                              <Search className="h-3 w-3 text-muted" />
                              <span className="text-xs text-muted">
                                Relevance: {percentage}% ({relevanceLabel})
                              </span>
                              {source.metadata.ingested_at && (
                                <span className="text-xs text-muted">
                                  {"\u2022"} Added: {new Date(source.metadata.ingested_at).toLocaleDateString()}
                                </span>
                              )}
                            </div>
                            <p className="text-sm text-text-main leading-relaxed whitespace-pre-wrap">
                              {source.text}
                            </p>
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
