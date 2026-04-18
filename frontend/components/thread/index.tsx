import { v4 as uuidv4 } from "uuid";
import { ReactNode, useEffect, useRef, useMemo, useCallback } from "react";
import Link from "next/link";
import { motion } from "framer-motion";
import { cn } from "@/lib/utils";
import { useStreamContext } from "@/providers/Stream";
import { useState, FormEvent } from "react";
import { Button } from "../ui/button";
import { Checkpoint, Message } from "@langchain/langgraph-sdk";
import { AssistantMessage, AssistantMessageLoading } from "./messages/ai";
import { HumanMessage } from "./messages/human";
import {
  DO_NOT_RENDER_ID_PREFIX,
  ensureToolCallsHaveResponses,
} from "@/lib/ensure-tool-responses";
import { TooltipIconButton } from "./tooltip-icon-button";
import {
  ArrowDown,
  ChevronDown,
  LoaderCircle,
  PanelRightOpen,
  PanelRightClose,
  SquarePen,
  XIcon,
  Plus as PlusIcon,
  Search,
  Check,
  Activity,
} from "lucide-react";
import { useQueryState, parseAsBoolean } from "nuqs";
import { StickToBottom, useStickToBottomContext } from "use-stick-to-bottom";
import ThreadHistory from "./history";
import { toast } from "sonner";
import { useMediaQuery } from "@/hooks/useMediaQuery";
import { Label } from "../ui/label";
import { Switch } from "../ui/switch";
import { Input } from "../ui/input";
import { Skeleton } from "../ui/skeleton";
import { Separator } from "../ui/separator";
import { useFileUpload } from "@/hooks/use-file-upload";
import { ContentBlocksPreview } from "./ContentBlocksPreview";
import {
  useArtifactOpen,
  ArtifactContent,
  ArtifactTitle,
  useArtifactContext,
} from "./artifact";
import { useThreads } from "@/providers/Thread";
import { SearchSettings } from "./search-settings";
import { resolveAgentApiUrl } from "@/lib/service-urls";
import { getCachedModels, ModelOption, refreshModels } from "@/lib/model-cache";

type ModelPickerProps = {
  models: ModelOption[];
  selectedModelKey: string;
  onSelect: (value: string) => void;
  isLoading: boolean;
  error?: string;
  onRetry: () => void;
};

function formatProvider(provider: string, displayName?: string): string {
  if (displayName) return displayName;
  if (!provider) return "Unknown";
  return provider
    .split(/[-_\s]+/)
    .filter(Boolean)
    .map((part) => part[0].toUpperCase() + part.slice(1))
    .join(" ");
}

function ModelPicker({
  models,
  selectedModelKey,
  onSelect,
  isLoading,
  error,
  onRetry,
}: ModelPickerProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [highlightedIndex, setHighlightedIndex] = useState<number>(-1);
  const rootRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const selected = useMemo(
    () => models.find((m) => `${m.provider}::${m.id}` === selectedModelKey),
    [models, selectedModelKey],
  );

  const filteredModels = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return models;
    return models.filter((model) => {
      const provider = formatProvider(model.provider, model.display_name).toLowerCase();
      return (
        model.id.toLowerCase().includes(q) ||
        provider.includes(q) ||
        model.provider.toLowerCase().includes(q) ||
        (model.display_name?.toLowerCase().includes(q) ?? false)
      );
    });
  }, [models, query]);

  const groupedModels = useMemo(() => {
    const grouped = new Map<string, ModelOption[]>();
    for (const model of filteredModels) {
      const key = model.provider || "unknown";
      const current = grouped.get(key) ?? [];
      grouped.set(key, [...current, model]);
    }

    return [...grouped.entries()].sort(([a], [b]) => {
      const displayNameA = filteredModels.find((m) => m.provider === a)?.display_name || a;
      const displayNameB = filteredModels.find((m) => m.provider === b)?.display_name || b;
      return displayNameA.localeCompare(displayNameB);
    });
  }, [filteredModels]);

  const flatOptions = useMemo(
    () => groupedModels.flatMap(([, options]) => options),
    [groupedModels],
  );

  const optionIndexByKey = useMemo(() => {
    const indexByKey = new Map<string, number>();
    flatOptions.forEach((option, index) => {
      indexByKey.set(`${option.provider}::${option.id}`, index);
    });
    return indexByKey;
  }, [flatOptions]);

  useEffect(() => {
    const onOutsideClick = (event: MouseEvent) => {
      if (!rootRef.current) return;
      if (rootRef.current.contains(event.target as Node)) return;
      setOpen(false);
    };

    const onEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };

    document.addEventListener("mousedown", onOutsideClick);
    document.addEventListener("keydown", onEscape);
    return () => {
      document.removeEventListener("mousedown", onOutsideClick);
      document.removeEventListener("keydown", onEscape);
    };
  }, []);

  useEffect(() => {
    if (!open) {
      setQuery("");
      return;
    }

    const selectedIndex = flatOptions.findIndex(
      (m) => `${m.provider}::${m.id}` === selectedModelKey,
    );
    setHighlightedIndex(selectedIndex >= 0 ? selectedIndex : 0);

    // Move focus into the listbox for immediate arrow-key navigation.
    setTimeout(() => listRef.current?.focus(), 0);
  }, [open, flatOptions, selectedModelKey]);

  const onOpenKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>) => {
    if (
      event.key === "Enter" ||
      event.key === " " ||
      event.key === "ArrowDown"
    ) {
      event.preventDefault();
      setOpen(true);
    }
  };

  const onListKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (!flatOptions.length) return;

    if (event.key === "ArrowDown") {
      event.preventDefault();
      setHighlightedIndex((prev) =>
        prev < flatOptions.length - 1 ? prev + 1 : 0,
      );
      return;
    }

    if (event.key === "ArrowUp") {
      event.preventDefault();
      setHighlightedIndex((prev) =>
        prev <= 0 ? flatOptions.length - 1 : prev - 1,
      );
      return;
    }

    if (event.key === "Enter") {
      event.preventDefault();
      const option = flatOptions[highlightedIndex];
      if (!option) return;
      onSelect(`${option.provider}::${option.id}`);
      setOpen(false);
      return;
    }

    if (event.key === "Escape") {
      event.preventDefault();
      setOpen(false);
    }
  };

  const triggerText = selected?.id ?? "Select a model";
  const triggerProvider = selected ? formatProvider(selected.provider, selected.display_name) : "";

  return (
    <div
      ref={rootRef}
      className="relative"
    >
      <div className="flex items-center gap-2">
        <Label
          htmlFor="model-picker-trigger"
          className="text-sm text-muted"
        >
          Model
        </Label>
        <Button
          id="model-picker-trigger"
          type="button"
          variant="outline"
          onClick={() => setOpen((prev) => !prev)}
          onKeyDown={onOpenKeyDown}
          className="h-9 min-w-[220px] justify-between bg-surface px-3"
          aria-haspopup="listbox"
          aria-expanded={open}
          aria-controls="model-picker-list"
        >
          <span className="flex min-w-0 items-center gap-2">
            <span className="truncate text-sm font-medium">{triggerText}</span>
            {triggerProvider && (
              <span className="rounded-full bg-background px-2 py-0.5 text-[11px] font-medium text-muted">
                {triggerProvider}
              </span>
            )}
          </span>
          <ChevronDown className="size-4 text-muted" />
        </Button>
      </div>

      {open && (
        <div
          className="absolute bottom-full left-0 z-30 mb-2 w-[340px] rounded-xl border border-border bg-surface p-2 shadow-lg"
          onKeyDown={onListKeyDown}
        >
          {models.length > 8 && (
            <div className="relative mb-2">
              <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted" />
              <Input
                autoFocus
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search models or provider"
                className="h-9 pl-9"
              />
            </div>
          )}

          <div
            id="model-picker-list"
            ref={listRef}
            role="listbox"
            tabIndex={0}
            className="max-h-64 overflow-y-auto rounded-md outline-none"
          >
            {isLoading ? (
              <div className="space-y-2 p-2">
                <Skeleton className="h-8 w-full" />
                <Skeleton className="h-8 w-full" />
                <Skeleton className="h-8 w-3/4" />
              </div>
            ) : error ? (
              <div className="flex flex-col items-start gap-2 p-3">
                <p className="text-sm text-muted">{error}</p>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  onClick={onRetry}
                >
                  Retry
                </Button>
              </div>
            ) : flatOptions.length === 0 ? (
              <p className="p-3 text-sm text-muted">
                {query.trim() ? "No matching models." : "No models available."}
              </p>
            ) : (
              groupedModels.map(([provider, options], groupIndex) => (
                <div key={provider}>
                  {groupIndex > 0 && <Separator className="my-1" />}
                  <p className="px-2 py-1 text-[11px] font-semibold tracking-wide text-muted uppercase">
                    {formatProvider(provider, options[0]?.display_name)}
                  </p>
                  {options.map((model) => {
                    const key = `${model.provider}::${model.id}`;
                    const isSelected = selectedModelKey === key;
                    const optionIndex = optionIndexByKey.get(key) ?? -1;

                    return (
                      <button
                        key={key}
                        type="button"
                        role="option"
                        aria-selected={isSelected}
                        onMouseEnter={() => setHighlightedIndex(optionIndex)}
                        onClick={() => {
                          onSelect(key);
                          setOpen(false);
                        }}
                        className={cn(
                          "flex w-full items-center justify-between rounded-md px-2 py-1.5 text-left text-sm",
                          highlightedIndex === optionIndex && "bg-background",
                          isSelected && "bg-background",
                        )}
                      >
                        <span className="truncate font-medium text-text-main">
                          {model.id}
                        </span>
                        {isSelected && <Check className="size-4 text-muted" />}
                      </button>
                    );
                  })}
                </div>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function StickyToBottomContent(props: {
  content: ReactNode;
  footer?: ReactNode;
  className?: string;
  contentClassName?: string;
}) {
  const context = useStickToBottomContext();
  return (
    <div
      ref={context.scrollRef}
      style={{ width: "100%", height: "100%" }}
      className={props.className}
    >
      <div
        ref={context.contentRef}
        className={props.contentClassName}
      >
        {props.content}
      </div>

      {props.footer}
    </div>
  );
}

function ScrollToBottom(props: { className?: string }) {
  const { isAtBottom, scrollToBottom } = useStickToBottomContext();

  if (isAtBottom) return null;
  return (
    <Button
      variant="outline"
      className={props.className}
      onClick={() => scrollToBottom()}
    >
      <ArrowDown className="h-4 w-4" />
      <span>Scroll to bottom</span>
    </Button>
  );
}

export function Thread() {
  const [artifactContext, setArtifactContext] = useArtifactContext();
  const [artifactOpen, closeArtifact] = useArtifactOpen();

  const [threadId, _setThreadId] = useQueryState("threadId");
  const [chatHistoryOpen, setChatHistoryOpen] = useQueryState(
    "chatHistoryOpen",
    parseAsBoolean.withDefault(false),
  );
  // Tool calls always visible - removed hide option
  const [input, setInput] = useState("");
  const {
    contentBlocks,
    setContentBlocks,
    handleFileUpload,
    dropRef,
    removeBlock,
    resetBlocks: _resetBlocks,
    dragOver,
    handlePaste,
  } = useFileUpload();
  const [firstTokenReceived, setFirstTokenReceived] = useState(false);
  const [models, setModels] = useState<ModelOption[]>([]);
  const [selectedModelKey, setSelectedModelKey] = useState<string>("");
  const [modelsLoading, setModelsLoading] = useState(true);
  const [modelsError, setModelsError] = useState<string | undefined>(undefined);
  const [isCreatingThread, setIsCreatingThread] = useState(false);
  const [searchType, setSearchType] = useState<"hybrid" | "semantic" | "keyword">("hybrid");
  const [rrfSparseWeight, setRrfSparseWeight] = useState(0.5);
  const [minRelevance, setMinRelevance] = useState(0.5);
  // Query rewriting always ON - removed toggle
  const isLargeScreen = useMediaQuery("(min-width: 1024px)");

  const agentApiUrl = useMemo(
    () => resolveAgentApiUrl(process.env.NEXT_PUBLIC_AGENT_API_URL),
    [],
  );

  const stream = useStreamContext();
  const messages = stream.messages;
  const isLoading = stream.isLoading;
  const { createThread, getThreads, setThreads } = useThreads();

  const lastError = useRef<string | undefined>(undefined);

  const setThreadId = (id: string | null) => {
    _setThreadId(id);

    // close artifact and reset artifact context
    closeArtifact();
    setArtifactContext({});
  };

  useEffect(() => {
    if (!stream.error) {
      lastError.current = undefined;
      return;
    }
    try {
      const message = (stream.error as any).message;
      if (!message || lastError.current === message) {
        // Message has already been logged. do not modify ref, return early.
        return;
      }

      // Message is defined, and it has not been logged yet. Save it, and send the error
      lastError.current = message;
      toast.error("An error occurred. Please try again.", {
        description: (
          <p>
            <strong>Error:</strong> <code>{message}</code>
          </p>
        ),
        richColors: true,
        closeButton: true,
      });
    } catch {
      // no-op
    }
  }, [stream.error]);

  // TODO: this should be part of the useStream hook
  const prevMessageLength = useRef(0);
  useEffect(() => {
    if (
      messages.length !== prevMessageLength.current &&
      messages?.length &&
      messages[messages.length - 1].type === "ai"
    ) {
      setFirstTokenReceived(true);
    }

    prevMessageLength.current = messages.length;
  }, [messages]);

  const modelsRequestIdRef = useRef(0);
  const defaultModelWarningShownRef = useRef(false);

  const syncModelSelection = useCallback((availableModels: ModelOption[]) => {
    if (!availableModels.length) return;

    const defaultModel = availableModels.find(
      (m: ModelOption) => m.provider === "groq" && m.id === "openai/gpt-oss-120b",
    );
    const groqModels = availableModels.filter((m: ModelOption) => m.provider === "groq");
    const fallbackModel = groqModels[0] || availableModels[0];

    setSelectedModelKey((current) => {
      if (
        current &&
        availableModels.some(
          (model: ModelOption) => `${model.provider}::${model.id}` === current,
        )
      ) {
        return current;
      }
      if (defaultModel) {
        return `${defaultModel.provider}::${defaultModel.id}`;
      }
      return `${fallbackModel.provider}::${fallbackModel.id}`;
    });

    if (
      !defaultModel &&
      groqModels.length > 0 &&
      !defaultModelWarningShownRef.current
    ) {
      defaultModelWarningShownRef.current = true;
      toast.warning(
        `Default model "openai/gpt-oss-120b" not available. Using "${fallbackModel.id}" instead.`,
      );
    }
  }, []);

  const loadModels = useCallback(async (forceRefresh = false) => {
    const requestId = modelsRequestIdRef.current + 1;
    modelsRequestIdRef.current = requestId;

    const cachedModels = !forceRefresh ? getCachedModels() : null;
    if (cachedModels?.length) {
      setModels(cachedModels);
      syncModelSelection(cachedModels);
      setModelsLoading(false);
    } else {
      setModelsLoading(true);
    }

    setModelsError(undefined);

    try {
      const parsed = await refreshModels(agentApiUrl);

      if (modelsRequestIdRef.current !== requestId) return;

      setModels(parsed);
      syncModelSelection(parsed);
    } catch {
      if (modelsRequestIdRef.current !== requestId) return;
      if (!cachedModels?.length) {
        setModelsError("Unable to load models. You can retry.");
      }
    } finally {
      if (modelsRequestIdRef.current === requestId && !cachedModels?.length) {
        setModelsLoading(false);
      }
    }
  }, [agentApiUrl, syncModelSelection]);

  useEffect(() => {
    void loadModels();
    return () => {
      modelsRequestIdRef.current += 1;
    };
  }, [loadModels]);

  const handleNewThread = async () => {
    setIsCreatingThread(true);
    try {
      const newThreadId = await createThread();
      setThreadId(newThreadId);
      setFirstTokenReceived(false);

      await getThreads().then(setThreads);

      toast.success("New chat session started");
    } catch (error) {
      toast.error("Failed to create new chat session", {
        description: error instanceof Error ? error.message : "Please try again",
      });
    } finally {
      setIsCreatingThread(false);
    }
  };

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    if ((input.trim().length === 0 && contentBlocks.length === 0) || isLoading)
      return;
    setFirstTokenReceived(false);

    const newHumanMessage: Message = {
      id: uuidv4(),
      type: "human",
      content: [
        ...(input.trim().length > 0 ? [{ type: "text", text: input }] : []),
        ...contentBlocks,
      ] as Message["content"],
    };

    const toolMessages = ensureToolCallsHaveResponses(stream.messages);

    const context =
      Object.keys(artifactContext).length > 0 ? artifactContext : undefined;

    const selectedModel = models.find(
      (model) => `${model.provider}::${model.id}` === selectedModelKey,
    );
    const routingConfig = selectedModel
      ? {
          model: selectedModel.id,
          provider: selectedModel.provider,
          base_url: selectedModel.base_url,
        }
      : undefined;

stream.submit(
        { messages: [...toolMessages, newHumanMessage], context },
        {
          config: routingConfig
            ? {
                configurable: {
                  ...routingConfig,
                  search_type: searchType,
                  rrf_dense_weight: Math.max(0, Math.min(1, 1 - rrfSparseWeight)),
                  min_relevance_score: minRelevance,
                  enable_query_rewriting: true, // Always ON
                },
              }
            : undefined,
          streamMode: ["values"],
          streamSubgraphs: true,
          streamResumable: true,
          optimisticValues: (prev) => ({
            ...prev,
            context,
            messages: [
              ...(prev.messages ?? []),
              ...toolMessages,
              newHumanMessage,
            ],
          }),
        },
      );

    setInput("");
    setContentBlocks([]);
  };

  const handleRegenerate = (
    parentCheckpoint: Checkpoint | null | undefined,
  ) => {
    // Do this so the loading state is correct
    prevMessageLength.current = prevMessageLength.current - 1;
    setFirstTokenReceived(false);
    stream.submit(undefined, {
      checkpoint: parentCheckpoint,
      streamMode: ["values"],
      streamSubgraphs: true,
      streamResumable: true,
    });
  };

  const chatStarted = !!threadId || !!messages.length;
  const hasNoAIOrToolMessages = !messages.find(
    (m) => m.type === "ai" || m.type === "tool",
  );

  return (
    <div className="flex h-screen w-full overflow-hidden">
      <div className="relative hidden lg:flex">
        <motion.div
          className="absolute z-20 h-full overflow-hidden border-r border-border bg-surface"
          style={{ width: 300 }}
          animate={
            isLargeScreen
              ? { x: chatHistoryOpen ? 0 : -300 }
              : { x: chatHistoryOpen ? 0 : -300 }
          }
          initial={{ x: -300 }}
          transition={
            isLargeScreen
              ? { type: "spring", stiffness: 300, damping: 30 }
              : { duration: 0 }
          }
        >
          <div
            className="relative h-full"
            style={{ width: 300 }}
          >
            <ThreadHistory />
          </div>
        </motion.div>
      </div>

      <div
        className={cn(
          "grid w-full grid-cols-[1fr_0fr] transition-all duration-500",
          artifactOpen && "grid-cols-[3fr_2fr]",
        )}
      >
        <motion.div
          className={cn(
            "relative flex min-w-0 flex-1 flex-col overflow-hidden",
            !chatStarted && "grid-rows-[1fr]",
          )}
          layout={isLargeScreen}
          animate={{
            marginLeft: chatHistoryOpen ? (isLargeScreen ? 300 : 0) : 0,
            width: chatHistoryOpen
              ? isLargeScreen
                ? "calc(100% - 300px)"
                : "100%"
              : "100%",
          }}
          transition={
            isLargeScreen
              ? { type: "spring", stiffness: 300, damping: 30 }
              : { duration: 0 }
          }
        >
          {!chatStarted && (
            <div className="absolute top-0 left-0 z-10 flex w-full items-center justify-between gap-3 p-2 pl-4">
              <div>
                {(!chatHistoryOpen || !isLargeScreen) && (
                  <Button
                    className="hover:bg-surface"
                    variant="ghost"
                    onClick={() => setChatHistoryOpen((p) => !p)}
                  >
                    {chatHistoryOpen ? (
                      <PanelRightOpen className="size-5" />
                    ) : (
                      <PanelRightClose className="size-5" />
                    )}
                  </Button>
                )}
              </div>
            </div>
          )}
          {chatStarted && (
            <div className="relative z-10 flex items-center justify-between gap-3 p-2">
              <div className="relative flex items-center justify-start gap-2">
                <div className="absolute left-0 z-10">
                  {(!chatHistoryOpen || !isLargeScreen) && (
                    <Button
                      className="hover:bg-surface"
                      variant="ghost"
                      onClick={() => setChatHistoryOpen((p) => !p)}
                    >
                      {chatHistoryOpen ? (
                        <PanelRightOpen className="size-5" />
                      ) : (
                        <PanelRightClose className="size-5" />
                      )}
                    </Button>
                  )}
                </div>
                <motion.button
                  className="flex cursor-pointer items-center gap-2"
                  onClick={() => setThreadId(null)}
                  animate={{
                    marginLeft: !chatHistoryOpen ? 48 : 0,
                  }}
                  transition={{
                    type: "spring",
                    stiffness: 300,
                    damping: 30,
                  }}
                >
                  <Activity className="size-8 text-primary" />
                  <span className="text-xl font-semibold tracking-tight">
                    QoSentry Chat
                  </span>
                </motion.button>
              </div>

              <div className="flex items-center gap-4">
<TooltipIconButton
                   size="lg"
                   className="p-4"
                   tooltip="New thread"
                   variant="ghost"
                   onClick={handleNewThread}
                   disabled={isCreatingThread}
                 >
                   {isCreatingThread ? (
                     <LoaderCircle className="size-5 animate-spin" />
                   ) : (
                     <SquarePen className="size-5" />
                   )}
                 </TooltipIconButton>
              </div>

              <div className="from-background to-background/0 absolute inset-x-0 top-full h-5 bg-gradient-to-b" />
            </div>
          )}

          <StickToBottom className="relative flex-1 overflow-hidden">
            <StickyToBottomContent
              className={cn(
                "absolute inset-0 overflow-y-scroll px-4 [&::-webkit-scrollbar]:w-1.5 [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:bg-border [&::-webkit-scrollbar-track]:bg-transparent",
                !chatStarted && "mt-[25vh] flex flex-col items-stretch",
                chatStarted && "grid grid-rows-[1fr_auto]",
              )}
              contentClassName="pt-8 pb-16 max-w-3xl mx-auto flex flex-col gap-4 w-full"
              content={
                <>
                  {messages
                    .filter((m) => !m.id?.startsWith(DO_NOT_RENDER_ID_PREFIX))
                    .map((message, index) =>
                      message.type === "human" ? (
                        <HumanMessage
                          key={message.id || `${message.type}-${index}`}
                          message={message}
                          isLoading={isLoading}
                        />
                      ) : (
                        <AssistantMessage
                          key={message.id || `${message.type}-${index}`}
                          message={message}
                          isLoading={isLoading}
                          handleRegenerate={handleRegenerate}
                        />
                      ),
                    )}
                  {/* Special rendering case where there are no AI/tool messages, but there is an interrupt.
                    We need to render it outside of the messages list, since there are no messages to render */}
                  {hasNoAIOrToolMessages && !!stream.interrupt && (
                    <AssistantMessage
                      key="interrupt-msg"
                      message={undefined}
                      isLoading={isLoading}
                      handleRegenerate={handleRegenerate}
                    />
                  )}
                  {isLoading && !firstTokenReceived && (
                    <AssistantMessageLoading />
                  )}
                </>
              }
footer={
                <div className="sticky bottom-0 flex flex-col items-center gap-6 bg-background px-2 pb-2">
                   {!chatStarted && (
                     <div className="flex items-center gap-3">
                       <Activity className="h-7 w-7 flex-shrink-0 text-primary" />
                       <h1 className="text-4xl font-semibold tracking-tight sm:text-5xl">
                         QoSentry Chat
                       </h1>
                     </div>
                   )}

                   <ScrollToBottom className="animate-in fade-in-0 zoom-in-95 absolute bottom-full left-1/2 mb-4 -translate-x-1/2" />

                  <div
                    ref={dropRef}
                    className={cn(
                      "relative z-10 mx-auto mb-8 w-full max-w-4xl overflow-hidden rounded-3xl border border-border/70 bg-surface/85 shadow-[0_22px_60px_rgba(2,6,23,0.55)] backdrop-blur transition-all",
                      dragOver
                        ? "border-primary border-2 border-dotted"
                        : "",
                    )}
                  >
                    <div className="pointer-events-none absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-primary/70 to-transparent" />
                    <form
                      onSubmit={handleSubmit}
                      className="mx-auto grid w-full max-w-4xl gap-2 px-3 py-3 sm:px-4 sm:py-4"
                    >
                      <ContentBlocksPreview
                        blocks={contentBlocks}
                        onRemove={removeBlock}
                      />
                      <textarea
                        value={input}
                        onChange={(e) => setInput(e.target.value)}
                        onPaste={handlePaste}
                        onKeyDown={(e) => {
                          if (
                            e.key === "Enter" &&
                            !e.shiftKey &&
                            !e.metaKey &&
                            !e.nativeEvent.isComposing
                          ) {
                            e.preventDefault();
                            const el = e.target as HTMLElement | undefined;
                            const form = el?.closest("form");
                            form?.requestSubmit();
                          }
                        }}
                        placeholder="Type your message..."
                        className="min-h-24 w-full resize-none rounded-2xl border border-border bg-background p-3.5 text-[15px] leading-relaxed text-text-main shadow-inner ring-0 outline-none placeholder:text-muted focus:border-primary/60 focus:ring-1 focus:ring-primary/20"
                      />

<div className="flex flex-wrap items-center gap-3 border-t border-border/60 px-1 pt-3">
                          <ModelPicker
                           models={models}
                           selectedModelKey={selectedModelKey}
                           onSelect={setSelectedModelKey}
                           isLoading={modelsLoading}
                           error={modelsError}
                             onRetry={() => loadModels(true)}
                         />
<SearchSettings
                            searchType={searchType}
                            onSearchTypeChange={setSearchType}
                              rrfSparseWeight={rrfSparseWeight}
                              onRrfSparseWeightChange={setRrfSparseWeight}
                            minRelevance={minRelevance}
                            onMinRelevanceChange={setMinRelevance}
                          />
<Label
                           htmlFor="file-input"
                           className="inline-flex h-9 cursor-pointer items-center gap-2 rounded-lg border border-border bg-background/70 px-3 text-xs font-medium text-muted transition-colors hover:text-text-main"
                         >
                           <PlusIcon className="size-4 text-muted" />
                           <span className="text-xs text-muted">
                             Upload PDF or Image
                           </span>
                         </Label>
                        <input
                          id="file-input"
                          type="file"
                          onChange={handleFileUpload}
                          multiple
                          accept="image/jpeg,image/png,image/gif,image/webp,application/pdf"
                          className="hidden"
                        />
                        {stream.isLoading ? (
                          <Button
                            key="stop"
                            onClick={() => stream.stop()}
                            className="ml-auto h-9"
                          >
                            <LoaderCircle className="h-4 w-4 animate-spin" />
                            Cancel
                          </Button>
                        ) : (
                          <Button
                            type="submit"
                            className="ml-auto h-9 bg-gradient-to-r from-primary to-secondary px-5 text-white shadow-md transition-all hover:opacity-95"
                            disabled={
                              isLoading ||
                              (!input.trim() && contentBlocks.length === 0)
                            }
                          >
                            Send
                          </Button>
                        )}
                      </div>
                    </form>
                  </div>
                </div>
              }
            />
          </StickToBottom>
        </motion.div>
        <div className="relative flex flex-col border-l">
          <div className="absolute inset-0 flex min-w-[30vw] flex-col">
            <div className="grid grid-cols-[1fr_auto] border-b p-4">
              <ArtifactTitle className="truncate overflow-hidden" />
              <button
                onClick={closeArtifact}
                className="cursor-pointer"
              >
                <XIcon className="size-5" />
              </button>
            </div>
            <ArtifactContent className="relative flex-grow" />
          </div>
        </div>
      </div>
    </div>
  );
}
