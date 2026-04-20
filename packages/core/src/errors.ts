export class WickdError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "WickdError";
  }
}

export class RunNotFoundError extends WickdError {
  constructor(public readonly runId: string) {
    super(`Run not found: ${runId}`);
    this.name = "RunNotFoundError";
  }
}

export class SpanNotFoundError extends WickdError {
  constructor(public readonly spanId: string) {
    super(`Span not found: ${spanId}`);
    this.name = "SpanNotFoundError";
  }
}

export class InvalidStateError extends WickdError {
  constructor(message: string) {
    super(message);
    this.name = "InvalidStateError";
  }
}
