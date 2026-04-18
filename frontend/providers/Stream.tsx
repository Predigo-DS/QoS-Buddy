import React, {
  createContext,
  useContext,
  ReactNode,
  useState,
  useMemo,
  useEffect,
  useCallback,
} from "react";
import { useQueryState } from "nuqs";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Activity, ArrowRight } from "lucide-react";
import { useThreads } from "./Thread";
import { Message } from "@langchain/langgraph-sdk";
import { UIMessage } from "@langchain/langgraph-sdk/react-ui";
import { resolveAgentApiUrl } from "@/lib/service-urls";

type ValuesState = {
  messages: Message[];
  ui?: UIMessage[];
};

type PersistedMessage = {
  role: "user" | "assistant" | "system";
  content: string;
};

type BackendChatMessage = {
  role: "user" | "assistant";
  content: string;
};

type StreamSubmitInput = {
  messages?: Message[];
  context?: Record<string, unknown>;
};

type StreamSubmitOptions = {
    config?: {
      configurable?: {
        model?: string;
        provider?: string;
        base_url?: string;
        search_type?: "hybrid" | "semantic" | "keyword";
        rrf_dense_weight?: number;
        min_relevance_score?: number;
        enable_query_rewriting?: boolean;
      };
    };
    command?: unknown;
    checkpoint?: unknown;
    streamMode?: string[];
    streamSubgraphs?: boolean;
    streamResumable?: boolean;
    optimisticValues?: (prev: ValuesState) => ValuesState;
  };

type StreamContextType = {
  messages: Message[];
  values: ValuesState;
  isLoading: boolean;
  error: Error | undefined;
  interrupt: unknown;
  submit: (input?: StreamSubmitInput, options?: StreamSubmitOptions) => Promise<void>;
  stop: () => void;
  setBranch: (_branch: string) => void;
  getMessagesMetadata: (_message: Message) => {
    firstSeenState?: { parent_checkpoint?: unknown; values?: ValuesState };
    branch?: string;
    branchOptions?: string[];
  } | undefined;
};

const StreamContext = createContext<StreamContextType | undefined>(undefined);

function getTextFromMessage(msg: Message): string {
  if (typeof msg.content === "string") return msg.content;
  if (Array.isArray(msg.content)) {
    return msg.content
      .map((block: any) => {
        if (block?.type === "text") return block?.text ?? "";
        if (typeof block === "string") return block;
        return "";
      })
      .join(" ")
      .trim();
  }
  return "";
}

function createMessage(type: "human" | "ai", content: string): Message {
    return {
      id: `${type}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      type,
      content,
      metadata: {},
    } as any;
  }

function toUiMessage(persisted: PersistedMessage, index: number): Message {
  return {
    id: `persisted-${index}-${Math.random().toString(36).slice(2, 8)}`,
    type: persisted.role === "assistant" ? "ai" : "human",
    content: persisted.content,
  } as Message;
}

function toBackendMessages(messages: Message[]): BackendChatMessage[] {
  const converted: BackendChatMessage[] = [];
  for (const message of messages) {
    if (message.type !== "human" && message.type !== "ai") continue;
    const content = getTextFromMessage(message);
    if (!content) continue;

    converted.push({
      role: message.type === "human" ? "user" : "assistant",
      content,
    });
  }
  return converted;
}

const DEFAULT_API_URL = "http://localhost:8002";
const DEFAULT_ASSISTANT_ID = "agent";

export const StreamProvider: React.FC<{ children: ReactNode }> = ({
  children,
}) => {
  const envApiUrl: string | undefined = process.env.NEXT_PUBLIC_API_URL;
  const envAssistantId: string | undefined =
    process.env.NEXT_PUBLIC_ASSISTANT_ID;

  const [apiUrl, setApiUrl] = useQueryState("apiUrl", {
    defaultValue: envApiUrl || "",
  });
  const [assistantId, setAssistantId] = useQueryState("assistantId", {
    defaultValue: envAssistantId || "",
  });
  const [threadId, setThreadId] = useQueryState("threadId");

  const finalApiUrl = apiUrl || envApiUrl || DEFAULT_API_URL;
  const finalAssistantId = assistantId || envAssistantId || DEFAULT_ASSISTANT_ID;
  const agentApiUrl = resolveAgentApiUrl(process.env.NEXT_PUBLIC_AGENT_API_URL);

  const [messages, setMessages] = useState<Message[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<Error | undefined>(undefined);
  const [interrupt] = useState<unknown>(undefined);
  const [abortController, setAbortController] = useState<AbortController | null>(
    null,
  );

  const { setThreads } = useThreads();

  useEffect(() => {
    let cancelled = false;

    const loadThread = async () => {
      if (!threadId) {
        setMessages([]);
        return;
      }

      try {
        const res = await fetch(`${agentApiUrl}/threads/${encodeURIComponent(threadId)}`);
        if (!res.ok) {
          if (!cancelled) setMessages([]);
          return;
        }

        const payload = await res.json();
        const persistedMessages = Array.isArray(payload?.messages)
          ? (payload.messages as PersistedMessage[])
          : [];

        if (!cancelled) {
          setMessages(
            persistedMessages.map((msg, index) => toUiMessage(msg, index)),
          );
        }
      } catch {
        if (!cancelled) {
          setMessages([]);
        }
      }
    };

    loadThread();
    return () => {
      cancelled = true;
    };
  }, [threadId, agentApiUrl]);

  const submit = useCallback(async (
    input?: StreamSubmitInput,
    options?: StreamSubmitOptions,
  ): Promise<void> => {
    if (options?.command) {
      // Interrupt/action workflows are not wired for the custom backend mode.
      return;
    }

    const sourceMessages = input?.messages ?? [];
    const lastMessage = sourceMessages[sourceMessages.length - 1];
    const userText =
      (lastMessage && getTextFromMessage(lastMessage)) ||
      getTextFromMessage(messages[messages.length - 1] as Message);

    if (!userText) return;

    const nextThreadId = threadId || crypto.randomUUID();
    const cfg = options?.config?.configurable;
    const historyForBackend = [
      ...toBackendMessages(messages),
      { role: "user" as const, content: userText },
    ];

    setError(undefined);
    setIsLoading(true);

    const appendedHuman = createMessage("human", userText);
    setMessages((prev) => [...prev, appendedHuman]);

    const controller = new AbortController();
    setAbortController(controller);

    try {
      const res = await fetch(`${agentApiUrl}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        signal: controller.signal,
        body: JSON.stringify({
          thread_id: nextThreadId,
          message: userText,
          messages: historyForBackend,
          model: cfg?.model,
          provider: cfg?.provider,
          base_url: cfg?.base_url,
          search_type: cfg?.search_type,
          rrf_dense_weight: cfg?.rrf_dense_weight,
          min_relevance_score: cfg?.min_relevance_score,
          enable_query_rewriting: cfg?.enable_query_rewriting,
        }),
      });

      if (!res.ok) {
        const text = await res.text();
        throw new Error(`HTTP ${res.status}: ${text}`);
      }

      const payload = await res.json();
      const answer = payload?.response ?? "";
      const sources = payload?.sources ?? [];
      const searchType = payload?.search_type ?? "hybrid";
      const rewrittenQueries = payload?.rewritten_queries ?? null;

      const aiMessage = createMessage("ai", answer);
      (aiMessage as any).metadata = { 
        sources, 
        search_type: searchType,
        rewritten_queries: rewrittenQueries 
      };
      const threadMessages = [...messages, appendedHuman, aiMessage];

      setMessages((prev) => [...prev, aiMessage]);
      setThreadId(payload?.thread_id || nextThreadId);

      setThreads((prev: any[]) => {
        const item = {
          thread_id: payload?.thread_id || nextThreadId,
          values: { messages: threadMessages },
        };
        const filtered = prev.filter((t) => t.thread_id !== item.thread_id);
        return [item as any, ...filtered].slice(0, 100);
      });
    } catch (e: any) {
      if (e?.name !== "AbortError") {
        setError(e instanceof Error ? e : new Error(String(e)));
      }
    } finally {
      setIsLoading(false);
      setAbortController(null);
    }
  }, [agentApiUrl, messages, setThreadId, setThreads, threadId]);

  const stop = useCallback(() => {
    abortController?.abort();
  }, [abortController]);

  const value = useMemo<StreamContextType>(
    () => ({
      messages,
      values: { messages },
      isLoading,
      error,
      interrupt,
      submit,
      stop,
      setBranch: () => {},
      getMessagesMetadata: () => undefined,
    }),
    [messages, isLoading, error, interrupt, submit, stop],
  );

  if (!finalApiUrl || !finalAssistantId) {
    return (
      <div className="flex min-h-screen w-full items-center justify-center p-4">
        <div className="animate-in fade-in-0 zoom-in-95 bg-background flex max-w-3xl flex-col rounded-lg border shadow-lg">
          <div className="mt-14 flex flex-col gap-2 border-b p-6">
            <div className="flex flex-col items-start gap-2">
              <Activity className="h-7 w-7 text-primary" />
              <h1 className="text-xl font-semibold tracking-tight">
                QoSentry Chat
              </h1>
            </div>
            <p className="text-muted-foreground">
              Enter your custom agent API URL and assistant ID.
            </p>
          </div>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              const form = e.target as HTMLFormElement;
              const formData = new FormData(form);
              setApiUrl((formData.get("apiUrl") as string) || DEFAULT_API_URL);
              setAssistantId(
                (formData.get("assistantId") as string) || DEFAULT_ASSISTANT_ID,
              );
            }}
            className="bg-muted/50 flex flex-col gap-6 p-6"
          >
            <div className="flex flex-col gap-2">
              <Label htmlFor="apiUrl">
                API URL<span className="text-rose-500">*</span>
              </Label>
              <Input
                id="apiUrl"
                name="apiUrl"
                className="bg-background"
                defaultValue={apiUrl || DEFAULT_API_URL}
                required
              />
            </div>

            <div className="flex flex-col gap-2">
              <Label htmlFor="assistantId">
                Assistant / Graph ID<span className="text-rose-500">*</span>
              </Label>
              <Input
                id="assistantId"
                name="assistantId"
                className="bg-background"
                defaultValue={assistantId || DEFAULT_ASSISTANT_ID}
                required
              />
            </div>

            <div className="mt-2 flex justify-end">
              <Button type="submit" size="lg">
                Continue
                <ArrowRight className="size-5" />
              </Button>
            </div>
          </form>
        </div>
      </div>
    );
  }

  return (
    <StreamContext.Provider value={value}>{children}</StreamContext.Provider>
  );
};

export const useStreamContext = (): StreamContextType => {
  const context = useContext(StreamContext);
  if (context === undefined) {
    throw new Error("useStreamContext must be used within a StreamProvider");
  }
  return context;
};

export default StreamContext;
