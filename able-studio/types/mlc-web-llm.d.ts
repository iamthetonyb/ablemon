/**
 * Ambient module declaration for @mlc-ai/web-llm.
 *
 * WebLLM is an optional runtime dependency — only loaded when
 * NEXT_PUBLIC_ENABLE_WEBGPU=true and the package is installed.
 * This declaration satisfies TypeScript at build time without
 * requiring the ~30MB package in CI or production bundles.
 */
declare module "@mlc-ai/web-llm" {
  export interface InitProgressReport {
    progress: number;
    timeElapsed: number;
    text: string;
  }

  export interface MLCEngineConfig {
    initProgressCallback?: (report: InitProgressReport) => void;
  }

  export interface ChatCompletionChunk {
    choices: Array<{
      delta: { content?: string; role?: string };
      index: number;
      finish_reason: string | null;
    }>;
  }

  export interface ChatCompletionRequest {
    messages: Array<{ role: string; content: string }>;
    stream?: boolean;
    temperature?: number;
    max_tokens?: number;
  }

  export interface MLCEngine {
    chat: {
      completions: {
        create(request: ChatCompletionRequest & { stream: true }): Promise<AsyncIterable<ChatCompletionChunk>>;
        create(request: ChatCompletionRequest): Promise<{ choices: Array<{ message: { content: string } }> }>;
      };
    };
    unload(): Promise<void>;
  }

  export function CreateMLCEngine(
    model: string,
    config?: MLCEngineConfig,
  ): Promise<MLCEngine>;
}
