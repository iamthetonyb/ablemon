/**
 * Promptfoo output transform: strip <think>...</think> tokens from model output.
 *
 * Models like Nemotron, Qwen, DeepSeek emit reasoning tokens that contaminate
 * the output and cause eval failures unrelated to actual quality.
 *
 * Usage in provider config:
 *   transform: file://transforms/strip_thinking.js
 */
module.exports = function (output) {
  if (typeof output !== 'string') return output;

  // Strip closed <think>...</think> blocks (including multiline)
  let cleaned = output.replace(/<think>[\s\S]*?<\/think>\s*/gi, '');

  // Strip "Thinking: ..." preamble (Nemotron pattern)
  cleaned = cleaned.replace(/^(?:Thinking:?\s*|<think>)[\s\S]*?\n\n/i, '');

  // Strip unclosed <think> at start
  cleaned = cleaned.replace(/^<think>\s*/i, '');

  return cleaned.trim();
};
