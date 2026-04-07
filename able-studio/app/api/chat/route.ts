import { createOpenAI } from '@ai-sdk/openai';
import { streamText, createTextStreamResponse, type UIMessage } from 'ai';

// Node runtime so we can proxy the gateway SSE stream
export const maxDuration = 60;

const CONTROL_BASE_URL =
  process.env.ABLE_CONTROL_API_BASE ||
  process.env.ABLE_GATEWAY_URL ||
  'http://127.0.0.1:8080';

const openrouter = createOpenAI({
  baseURL: 'https://openrouter.ai/api/v1',
  apiKey: process.env.OPENROUTER_API_KEY,
});

export async function POST(req: Request) {
  const { messages } = await req.json() as { messages: UIMessage[] };

  // Extract the last user message to send to the gateway
  const lastMessage = messages
    .filter((m) => m.role === 'user')
    .slice(-1)[0];

  const lastContent = lastMessage?.parts
    ?.filter((p: any) => p.type === 'text')
    .map((p: any) => p.text as string)
    .join('') || '';

  // Try routing through the ABLE gateway (full pipeline: TrustGate → enricher → logging)
  if (lastContent && process.env.ABLE_GATEWAY_URL) {
    try {
      const gatewayResp = await fetch(`${CONTROL_BASE_URL}/api/chat`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(process.env.ABLE_SERVICE_TOKEN
            ? { 'x-able-service-token': process.env.ABLE_SERVICE_TOKEN }
            : {}),
        },
        body: JSON.stringify({
          message: lastContent,
          channel: 'studio',
          session_id: 'studio',
        }),
        // @ts-ignore — Node fetch supports signal
        signal: AbortSignal.timeout(55_000),
      });

      if (gatewayResp.ok && gatewayResp.body) {
        // Bridge gateway SSE chunks → Vercel AI SDK text stream
        const textStream = new ReadableStream<string>({
          async start(controller) {
            const reader = gatewayResp.body!.getReader();
            const decoder = new TextDecoder();
            let buffer = '';
            try {
              while (true) {
                const { done, value } = await reader.read();
                if (done) break;
                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split('\n');
                buffer = lines.pop() ?? '';
                for (const line of lines) {
                  if (!line.startsWith('data: ')) continue;
                  try {
                    const event = JSON.parse(line.slice(6));
                    if (event.type === 'chunk' && typeof event.text === 'string') {
                      controller.enqueue(event.text);
                    }
                  } catch {
                    // Malformed SSE event — ignore
                  }
                }
              }
            } catch {
              // Stream error — close cleanly
            } finally {
              reader.releaseLock();
              controller.close();
            }
          },
        });
        return createTextStreamResponse({ textStream });
      }
    } catch {
      // Gateway unreachable — fall through to OpenRouter
    }
  }

  // Fallback: direct OpenRouter (no gateway logging/enrichment)
  const coreMessages = messages.map((msg) => ({
    role: msg.role as 'user' | 'assistant',
    content: msg.parts
      ?.filter((p: any) => p.type === 'text')
      .map((p: any) => p.text as string)
      .join('') || '',
  }));

  const result = streamText({
    model: openrouter('openai/gpt-5.4-mini'),
    messages: coreMessages,
    system: `You are Able, the operator-facing voice of ABLE (Autonomous Business & Learning Engine) embedded in the ABLE Studio control plane.
Your spoken name is Able.
You assist the operator with business strategy, code, deployments, and task execution.
Be direct, concise, calm, and lightly warm. No fluff.
You have access to the full ABLE system context.
When asked about system status, reference real data from the dashboard.
Format responses with Markdown when helpful.`,
  });

  return result.toUIMessageStreamResponse();
}
