/**
 * Incremental parser for Server-Sent Events (WHATWG HTML, §9.2).
 *
 * Call {@link SseParser.push} with each decoded string chunk as it arrives
 * from the network; it returns any complete events that the chunk finished.
 * Partial events stay buffered until the next push. CRLF, CR, and LF line
 * endings are all normalised to LF before parsing.
 *
 * Comments (lines starting with `:`), fields without a colon, and lines
 * with unknown field names are ignored, matching the spec.
 */
export interface SseEvent {
  readonly data: string;
  readonly event?: string;
  readonly id?: string;
}

const EVENT_BOUNDARY = "\n\n";

export class SseParser {
  private buffer = "";

  push(chunk: string): readonly SseEvent[] {
    // Normalise line endings so we only scan for \n\n downstream.
    this.buffer += chunk.replace(/\r\n?/g, "\n");
    const events: SseEvent[] = [];

    let boundary = this.buffer.indexOf(EVENT_BOUNDARY);
    while (boundary !== -1) {
      const raw = this.buffer.slice(0, boundary);
      this.buffer = this.buffer.slice(boundary + EVENT_BOUNDARY.length);
      const parsed = parseEvent(raw);
      if (parsed !== null) events.push(parsed);
      boundary = this.buffer.indexOf(EVENT_BOUNDARY);
    }

    return events;
  }

  /**
   * Flush any trailing event that was not terminated by `\n\n`.
   * Some servers end the stream without a final blank line; call this
   * once the upstream closes to surface any final event.
   */
  flush(): readonly SseEvent[] {
    if (this.buffer.length === 0) return [];
    const parsed = parseEvent(this.buffer);
    this.buffer = "";
    return parsed === null ? [] : [parsed];
  }
}

function parseEvent(raw: string): SseEvent | null {
  const dataLines: string[] = [];
  let event: string | undefined;
  let id: string | undefined;

  for (const line of raw.split("\n")) {
    if (line.length === 0) continue;
    if (line.startsWith(":")) continue;

    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);

    if (field === "data") dataLines.push(value);
    else if (field === "event") event = value;
    else if (field === "id") id = value;
    // Other field names (retry, etc.) are ignored by design.
  }

  if (dataLines.length === 0) return null;
  const data = dataLines.join("\n");
  return {
    data,
    ...(event !== undefined ? { event } : {}),
    ...(id !== undefined ? { id } : {}),
  };
}
