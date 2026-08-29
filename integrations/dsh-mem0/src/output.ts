/**
 * Hard cap on tool output before it reaches the model context.
 *
 * Same guard the sibling plugins apply (200 lines / 50KB, see
 * integrations/pi-agent-plugin/src/memory/tools.ts): a large recall or a wide
 * result set can otherwise flood the context window in a single tool call.
 */

export const MAX_OUTPUT_LINES = 200;
export const MAX_OUTPUT_BYTES = 50_000;

function utf8Bytes(text: string): number {
  return Buffer.byteLength(text, "utf8");
}

function takeUtf8Bytes(text: string, limit: number): string {
  const chars: string[] = [];
  let used = 0;
  for (const char of text) {
    const size = utf8Bytes(char);
    if (used + size > limit) break;
    chars.push(char);
    used += size;
  }
  return chars.join("");
}

export function truncateOutput(text: string): string {
  const lines = text.split("\n");
  if (lines.length <= MAX_OUTPUT_LINES && utf8Bytes(text) <= MAX_OUTPUT_BYTES) {
    return text;
  }

  const kept = lines.slice(0, MAX_OUTPUT_LINES);
  let result = kept.join("\n");
  const byteCapped = utf8Bytes(result) > MAX_OUTPUT_BYTES;

  const dropped = lines.length - kept.length;
  const reasons: string[] = [];
  if (dropped > 0) reasons.push(`showing ${kept.length} of ${lines.length} lines`);
  if (byteCapped) reasons.push(`cut at ${Math.floor(MAX_OUTPUT_BYTES / 1000)}KB`);
  if (reasons.length > 0) {
    const notice = `\n\n[Output truncated: ${reasons.join(", ")}]`;
    result = takeUtf8Bytes(result, MAX_OUTPUT_BYTES - utf8Bytes(notice)) + notice;
  }
  return result;
}
