import { createOpenAI } from '@ai-sdk/openai';
import { streamText } from 'ai';

export const maxDuration = 30; // Allow long-running streaming up to 30 seconds

// Configure OpenRouter as the unified Vercel AI Gateway provider
const openrouter = createOpenAI({
  baseURL: 'https://openrouter.ai/api/v1',
  apiKey: process.env.OPENROUTER_API_KEY,
});

export async function POST(req: Request) {
  try {
    const { messages } = await req.json();

    const result = streamText({
      // T1 model — GPT 5.4 Nano (80% quality, $0.20/$1.25 per M, 700ms avg)
      // Sonnet 4.6 was here before — leaked $3/$15 per M on every chat message
      model: openrouter('openai/gpt-5.4-nano'),
      messages,
      system: `You are ATLAS, the AGI Mission Control intelligence. 
You live entirely inside this Next.js 16 command center. 
Your goal is to assist the user instantly with their business, code, and tasks from this real-time interface.
Always be concise, powerful, and act as a dynamic working memory layer.`,
    });

    // Handle older AI SDK type compatibilities during preview phase
    return (result as any).toDataStreamResponse ? (result as any).toDataStreamResponse() : result.toTextStreamResponse();
  } catch (error: any) {
    console.error("Chat API Error:", error);
    return new Response(JSON.stringify({ error: "Failed to connect to LLM." }), { status: 500 });
  }
}
