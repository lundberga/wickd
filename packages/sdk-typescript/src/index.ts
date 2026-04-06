export { agent, WickdAgent } from "./agent.js";
export type { AgentOptions } from "./agent.js";

export { Budget, BudgetTracker, BudgetExceeded, WickdPatchError } from "./budget.js";
export { Trace, TraceStore, TraceEvent, CloudTraceSync } from "./trace.js";

export {
  approvalGate,
  ApprovalDenied,
  terminalApprovalHandler,
  autoApproveHandler,
  autoDenyHandler,
  webhookApprovalHandler,
} from "./approvals.js";
export type { ApprovalHandler, ApprovalContext } from "./approvals.js";

export {
  patchOpenAI,
  patchAnthropic,
  patchGoogle,
  patchAll,
  patchStatus,
  verifyPatches,
  healthStatus,
} from "./interceptor.js";

export * as notify from "./notify.js";
export { slackInteractive } from "./notify.js";

export { calculateCost, getPrice, MODEL_PRICES, FALLBACK_PRICE } from "wickd-core";
export type {
  BudgetConfig,
  BudgetSummary,
  ModelPrice,
  Trace as TraceType,
  TraceEvent as TraceEventType,
  TraceStatus,
  NotificationEvent,
} from "wickd-core";
