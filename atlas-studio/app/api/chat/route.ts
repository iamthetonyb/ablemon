import { createOpenAI } from '@ai-sdk/openai';
import { streamText, type UIMessage } from 'ai';

// Edge runtime — cheapest execution, no cold start serverless overhead
export const runtime = 'edge';
export const maxDuration = 30;

const openrouter = createOpenAI({
  baseURL: 'https://openrouter.ai/api/v1',
  apiKey: process.env.OPENROUTER_API_KEY,
});

export async function POST(req: Request) {
  const { messages } = await req.json() as { messages: UIMessage[] };

  const coreMessages = messages.map((msg) => ({
    role: msg.role as 'user' | 'assistant',
    content: msg.parts
      ?.filter((p: any) => p.type === 'text')
      .map((p: any) => p.text)
      .join('') || '',
  }));

  const result = streamText({
    // T1: GPT 5.4 Mini — $0.75/$4.50 per M, 100% quality on our benchmark
    model: openrouter('openai/gpt-5.4-mini'),
    messages: coreMessages,
    system: `You are ATLAS, an autonomous AGI system embedded in the ATLAS Studio control plane.
You assist the operator with business strategy, code, deployments, and task execution.
Be direct, concise, no fluff. You have access to the full ATLAS system context.
When asked about system status, reference real data from the dashboard.
Format responses with Markdown when helpful.`,
  });

  return result.toUIMessageStreamResponse();
}
