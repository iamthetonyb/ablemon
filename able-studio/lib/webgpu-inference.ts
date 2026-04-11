/**
 * D8 — WebGPU Edge Inference
 *
 * Feature-flagged WebGPU inference for running Gemma 4 E4B in-browser.
 * Fallback chain: WebGPU → Ollama → Cloud.
 *
 * Enable: NEXT_PUBLIC_ENABLE_WEBGPU=true
 * Source: gemma-gem + Rivet patterns
 */

// ── Types ────────────────────────────────────────────────────

export interface InferenceResult {
  text: string;
  source: "webgpu" | "ollama" | "cloud";
  tokens: number;
  duration_ms: number;
}

export interface WebGPUCapabilities {
  available: boolean;
  adapter: string | null;
  estimatedVRAM_MB: number;
  reason?: string;
}

interface ToolCall {
  name: string;
  params: Record<string, unknown>;
}

// ── Feature flag ─────────────────────────────────────────────

const WEBGPU_ENABLED =
  typeof process !== "undefined"
    ? process.env.NEXT_PUBLIC_ENABLE_WEBGPU === "true"
    : false;

// ── WebGPU detection ─────────────────────────────────────────

export async function detectWebGPU(): Promise<WebGPUCapabilities> {
  if (!WEBGPU_ENABLED) {
    return { available: false, adapter: null, estimatedVRAM_MB: 0, reason: "Feature flag disabled" };
  }

  if (typeof navigator === "undefined" || !("gpu" in navigator)) {
    return { available: false, adapter: null, estimatedVRAM_MB: 0, reason: "WebGPU API not available" };
  }

  try {
    const gpu = navigator.gpu;
    const adapter = await gpu.requestAdapter({ powerPreference: "high-performance" });

    if (!adapter) {
      return { available: false, adapter: null, estimatedVRAM_MB: 0, reason: "No GPU adapter found" };
    }

    const info = adapter.info;
    // Estimate VRAM from adapter limits (heuristic)
    const maxBufferSize = adapter.limits.maxBufferSize || 0;
    const estimatedVRAM = Math.round(maxBufferSize / (1024 * 1024));

    // Gemma 4 E4B needs ~4.5GB VRAM
    const sufficient = estimatedVRAM >= 4500;

    return {
      available: sufficient,
      adapter: info?.description || info?.vendor || "Unknown GPU",
      estimatedVRAM_MB: estimatedVRAM,
      reason: sufficient ? undefined : `Insufficient VRAM: ${estimatedVRAM}MB < 4500MB required`,
    };
  } catch {
    return { available: false, adapter: null, estimatedVRAM_MB: 0, reason: "WebGPU detection failed" };
  }
}

// ── Tool call parser (gemma-gem pattern) ─────────────────────

/**
 * Parse tool calls from local model output.
 * Format: <|tool_call>call:name{params}<tool_call|>
 */
export function parseToolCalls(text: string): { cleanText: string; toolCalls: ToolCall[] } {
  const toolCalls: ToolCall[] = [];
  const regex = /<\|tool_call>call:(\w+)\{([^}]*)\}<tool_call\|>/g;

  const cleanText = text.replace(regex, (_, name, paramsStr) => {
    try {
      const params = JSON.parse(`{${paramsStr}}`);
      toolCalls.push({ name, params });
    } catch {
      toolCalls.push({ name, params: { raw: paramsStr } });
    }
    return "";
  }).trim();

  return { cleanText, toolCalls };
}

// ── Streaming inference ──────────────────────────────────────

export type StreamCallback = (chunk: string, done: boolean) => void;

/**
 * Run inference through the fallback chain.
 * WebGPU → Ollama → Cloud
 */
export async function infer(
  prompt: string,
  onStream?: StreamCallback,
): Promise<InferenceResult> {
  const start = Date.now();

  // Try WebGPU first
  if (WEBGPU_ENABLED) {
    const caps = await detectWebGPU();
    if (caps.available) {
      try {
        const result = await inferWebGPU(prompt, onStream);
        return { ...result, source: "webgpu", duration_ms: Date.now() - start };
      } catch {
        // Fall through to Ollama
      }
    }
  }

  // Try Ollama
  try {
    const result = await inferOllama(prompt, onStream);
    return { ...result, source: "ollama", duration_ms: Date.now() - start };
  } catch {
    // Fall through to cloud
  }

  // Cloud fallback
  const result = await inferCloud(prompt, onStream);
  return { ...result, source: "cloud", duration_ms: Date.now() - start };
}

// ── WebGPU inference (WebLLM) ────────────────────────────────

async function inferWebGPU(
  prompt: string,
  onStream?: StreamCallback,
): Promise<{ text: string; tokens: number }> {
  // Dynamic import — WebLLM is optional
  const { CreateMLCEngine } = await import("@mlc-ai/web-llm");

  const engine = await CreateMLCEngine("gemma-2-2b-it-q4f16_1-MLC", {
    initProgressCallback: (progress) => {
      if (onStream) onStream(`[Loading model: ${Math.round(progress.progress * 100)}%]\n`, false);
    },
  });

  const response = await engine.chat.completions.create({
    messages: [{ role: "user", content: prompt }],
    stream: true,
  });

  let text = "";
  let tokens = 0;
  for await (const chunk of response) {
    const delta = chunk.choices[0]?.delta?.content || "";
    text += delta;
    tokens++;
    if (onStream) onStream(delta, false);
  }

  if (onStream) onStream("", true);
  return { text, tokens };
}

// ── Ollama inference ─────────────────────────────────────────

async function inferOllama(
  prompt: string,
  onStream?: StreamCallback,
): Promise<{ text: string; tokens: number }> {
  const ollamaUrl = process.env.NEXT_PUBLIC_OLLAMA_URL || "http://localhost:11434";

  const res = await fetch(`${ollamaUrl}/api/generate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      model: "qwen3.5:27b",
      prompt,
      stream: true,
    }),
  });

  if (!res.ok) throw new Error(`Ollama: ${res.status}`);

  const reader = res.body?.getReader();
  if (!reader) throw new Error("No response body");

  let text = "";
  let tokens = 0;
  const decoder = new TextDecoder();

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    const chunk = decoder.decode(value, { stream: true });
    for (const line of chunk.split("\n").filter(Boolean)) {
      try {
        const data = JSON.parse(line);
        if (data.response) {
          text += data.response;
          tokens++;
          if (onStream) onStream(data.response, false);
        }
        if (data.done) {
          tokens = data.eval_count || tokens;
        }
      } catch {
        // Skip malformed lines
      }
    }
  }

  if (onStream) onStream("", true);
  return { text, tokens };
}

// ── Cloud inference (via ABLE API) ───────────────────────────

async function inferCloud(
  prompt: string,
  onStream?: StreamCallback,
): Promise<{ text: string; tokens: number }> {
  const res = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      messages: [{ role: "user", content: prompt }],
    }),
  });

  if (!res.ok) throw new Error(`Cloud: ${res.status}`);

  const data = await res.json();
  const text = data.choices?.[0]?.message?.content || data.text || "";

  if (onStream) {
    onStream(text, true);
  }

  return { text, tokens: data.usage?.total_tokens || 0 };
}
