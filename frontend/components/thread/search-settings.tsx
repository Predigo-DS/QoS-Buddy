import { useState } from "react";
import { Label } from "../ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "../ui/select";
import { Slider } from "../ui/slider";
import { Tooltip, TooltipContent, TooltipTrigger, TooltipProvider } from "../ui/tooltip";
import { ChevronDown, ChevronUp } from "lucide-react";
import { Button } from "../ui/button";

type SearchSettingsProps = {
  searchType: "hybrid" | "semantic" | "keyword";
  onSearchTypeChange: (type: "hybrid" | "semantic" | "keyword") => void;
  rrfSparseWeight: number;
  onRrfSparseWeightChange: (weight: number) => void;
  minRelevance: number;
  onMinRelevanceChange: (value: number) => void;
};

export function SearchSettings({
  searchType,
  onSearchTypeChange,
  rrfSparseWeight,
  onRrfSparseWeightChange,
  minRelevance,
  onMinRelevanceChange,
}: SearchSettingsProps) {
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const showWeightSlider = searchType === "hybrid";
  const searchTypeLabel =
    searchType === "hybrid"
      ? "Hybrid"
      : searchType === "semantic"
        ? "Semantic"
        : "Keyword";

  return (
    <div className="flex min-w-[280px] flex-col gap-2 rounded-xl border border-border/70 bg-background/60 px-3 py-2">
      {/* Search Type */}
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger asChild>
            <Select
              value={searchType}
              onValueChange={(value) =>
                onSearchTypeChange(value as "hybrid" | "semantic" | "keyword")
              }
            >
              <SelectTrigger className="h-9 w-full border-border bg-surface text-sm">
                <SelectValue placeholder="Search type" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="hybrid">
                  <span className="flex items-center gap-2">
                    <span>Hybrid</span>
                    <span className="text-xs text-muted">(Best)</span>
                  </span>
                </SelectItem>
                <SelectItem value="semantic">
                  <span className="flex items-center gap-2">
                    <span>Semantic</span>
                    <span className="text-xs text-muted">(Meaning)</span>
                  </span>
                </SelectItem>
                <SelectItem value="keyword">
                  <span className="flex items-center gap-2">
                    <span>Keyword</span>
                    <span className="text-xs text-muted">(Exact)</span>
                  </span>
                </SelectItem>
              </SelectContent>
            </Select>
          </TooltipTrigger>
          <TooltipContent side="top">
            <p className="max-w-[200px] text-xs">
              {searchType === "hybrid" && "Combines semantic + keyword for best results"}
              {searchType === "semantic" && "Search by meaning"}
              {searchType === "keyword" && "Exact keyword matching"}
            </p>
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>

      {/* Min Relevance */}
      <div className="flex items-center gap-2">
        <Label htmlFor="relevance-threshold" className="whitespace-nowrap text-[11px] font-semibold tracking-wide text-muted uppercase">
          Min Relevance
        </Label>
        <Slider
          id="relevance-threshold"
          value={[minRelevance]}
          onValueChange={(values) => onMinRelevanceChange(values[0])}
          min={0.3}
          max={0.8}
          step={0.05}
          className="flex-1"
        />
        <span className="min-w-[35px] text-right text-xs text-muted">
          {Math.round(minRelevance * 100)}%
        </span>
      </div>

      {/* Advanced Options */}
      <Button
        variant="ghost"
        onClick={() => setAdvancedOpen(!advancedOpen)}
        className="h-8 justify-start px-2 text-xs text-muted hover:bg-surface hover:text-text-main"
      >
        {advancedOpen ? (
          <ChevronUp className="h-3 w-3 mr-1" />
        ) : (
          <ChevronDown className="h-3 w-3 mr-1" />
        )}
        {searchTypeLabel} settings
      </Button>

      {advancedOpen && (
        <div className="space-y-3 border-t border-border/70 pt-2">
          {showWeightSlider && (
            <div className="space-y-1">
              <div className="flex items-center gap-2">
                <Label htmlFor="rrf-weight" className="min-w-[45px] text-xs text-muted">
                  Dense
                </Label>
                <Slider
                  id="rrf-weight"
                  value={[rrfSparseWeight]}
                  onValueChange={(values) => onRrfSparseWeightChange(values[0])}
                  min={0}
                  max={1}
                  step={0.1}
                  className="flex-1"
                />
                <Label htmlFor="rrf-weight" className="min-w-[45px] text-xs text-muted">
                  Sparse
                </Label>
              </div>
              <p className="px-1 text-[11px] text-muted">
                Dense {(1 - rrfSparseWeight).toFixed(1)} | Sparse {rrfSparseWeight.toFixed(1)}
              </p>
            </div>
          )}
          <div className="px-1 text-xs text-muted">
            <p>
              Smart Query Rewriting: <span className="font-medium text-accent">ON</span>
            </p>
            <p className="mt-1 text-[10px]">+15% accuracy, improves retrieval quality</p>
          </div>
        </div>
      )}
    </div>
  );
}
