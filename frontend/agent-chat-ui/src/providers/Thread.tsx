import { v4 as uuidv4 } from "uuid";
import { Thread } from "@langchain/langgraph-sdk";
import {
  createContext,
  useContext,
  ReactNode,
  useState,
  Dispatch,
  SetStateAction,
} from "react";

interface ThreadContextType {
  getThreads: () => Promise<Thread[]>;
  createThread: () => Promise<string>;
  threads: Thread[];
  setThreads: Dispatch<SetStateAction<Thread[]>>;
  threadsLoading: boolean;
  setThreadsLoading: Dispatch<SetStateAction<boolean>>;
}

const ThreadContext = createContext<ThreadContextType | undefined>(undefined);

export function ThreadProvider({ children }: { children: ReactNode }) {
  const [threads, setThreads] = useState<Thread[]>([]);
  const [threadsLoading, setThreadsLoading] = useState(false);
  const agentApiUrl = process.env.NEXT_PUBLIC_AGENT_API_URL || "http://localhost:8002";

  const getThreads = async (): Promise<Thread[]> => {
    try {
      const res = await fetch(`${agentApiUrl}/threads`);
      if (!res.ok) {
        return [];
      }

      const payload = await res.json();
      const data = Array.isArray(payload?.data) ? payload.data : [];
      return data as Thread[];
    } catch {
      return [];
    }
  };

  const createThread = async (): Promise<string> => {
    try {
      const res = await fetch(`${agentApiUrl}/threads`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
      });

      if (!res.ok) {
        console.warn("Backend thread creation failed, using client-side UUID");
        return uuidv4();
      }

      const data = await res.json();
      return data.thread_id;
    } catch (error) {
      console.error("Failed to create thread:", error);
      return uuidv4();
    }
  };

  const value: ThreadContextType = {
    getThreads,
    createThread,
    threads,
    setThreads,
    threadsLoading,
    setThreadsLoading,
  };

  return (
    <ThreadContext.Provider value={value}>{children}</ThreadContext.Provider>
  );
}

export function useThreads() {
  const context = useContext(ThreadContext);
  if (context === undefined) {
    throw new Error("useThreads must be used within a ThreadProvider");
  }
  return context;
}
