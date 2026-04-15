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
  rrfDenseWeight: number;
  onRrfDenseWeightChange: (weight: number) => void;
  minRelevance: number;
  onMinRelevanceChange: (value: number) => void;
};

export function SearchSettings({
  searchType,
  onSearchTypeChange,
  rrfDenseWeight,
  onRrfDenseWeightChange,
  minRelevance,
  onMinRelevanceChange,
}: SearchSettingsProps) {
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const showWeightSlider = searchType === "hybrid";

  return (
    <div className="flex flex-col gap-2 min-w-[260px]">
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
              <SelectTrigger className="w-full h-8 text-sm">
                <SelectValue placeholder="Search type" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="hybrid">
                  <span className="flex items-center gap-2">
                    <span>🔍 Hybrid</span>
                    <span className="text-xs text-gray-500">(Best)</span>
                  </span>
                </SelectItem>
                <SelectItem value="semantic">
                  <span className="flex items-center gap-2">
                    <span>💭 Semantic</span>
                    <span className="text-xs text-gray-500">(Meaning)</span>
                  </span>
                </SelectItem>
                <SelectItem value="keyword">
                  <span className="flex items-center gap-2">
                    <span>🔤 Keyword</span>
                    <span className="text-xs text-gray-500">(Exact)</span>
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
        <Label htmlFor="relevance-threshold" className="text-xs text-gray-600 whitespace-nowrap">
          Min Relevance
        </Label>
        <Slider
          id="relevance-threshold"
          value={[minRelevance]}
          onValueChange={(values) => onMinRelevanceChange(values[0])}
          min={0.5}
          max={0.9}
          step={0.05}
          className="flex-1"
        />
        <span className="text-xs text-gray-500 min-w-[35px] text-right">
          {Math.round(minRelevance * 100)}%
        </span>
      </div>

      {/* Advanced Options */}
      <Button
        variant="ghost"
        onClick={() => setAdvancedOpen(!advancedOpen)}
        className="h-7 px-2 text-xs hover:bg-gray-100"
      >
        {advancedOpen ? (
          <ChevronUp className="h-3 w-3 mr-1" />
        ) : (
          <ChevronDown className="h-3 w-3 mr-1" />
        )}
        Advanced
      </Button>

      {advancedOpen && (
        <div className="border-t border-gray-200 pt-2 space-y-3">
          {showWeightSlider && (
            <div className="flex items-center gap-2">
              <Label htmlFor="rrf-weight" className="text-xs text-gray-600 min-w-[50px]">
                Keyword
              </Label>
              <Slider
                id="rrf-weight"
                value={[rrfDenseWeight]}
                onValueChange={(values) => onRrfDenseWeightChange(values[0])}
                min={0}
                max={1}
                step={0.1}
                className="flex-1"
              />
              <Label htmlFor="rrf-weight" className="text-xs text-gray-600 min-w-[50px]">
                Semantic
              </Label>
            </div>
          )}
          <div className="text-xs text-gray-500 px-1">
            <p>✨ Smart Query Rewriting: <span className="text-green-600 font-medium">ON</span></p>
            <p className="text-[10px] mt-1">+15% accuracy, improves retrieval quality</p>
          </div>
        </div>
      )}
    </div>
  );
}
