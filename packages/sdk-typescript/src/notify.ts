import type { NotificationEvent } from "@wickdone/core";

export type NotifyHandler = (event: NotificationEvent) => void | Promise<void>;

/** Logs notifications to stderr. Good for local dev. */
export function consoleNotifier(prefix = "[wickd]"): NotifyHandler {
  return (event) => {
    if (event.type === "budget_kill") {
      globalThis.console.log(
        `${prefix} BUDGET KILLED — $${(event.spend ?? 0).toFixed(4)} spent. Trigger: ${event.trigger ?? "unknown"}`,
      );
    } else if (event.type === "run_complete") {
      const s = event.summary;
      globalThis.console.log(
        `${prefix} Run complete — $${(s?.runSpend ?? 0).toFixed(4)} spent, ${s?.callCount ?? 0} LLM calls`,
      );
    } else {
      globalThis.console.log(`${prefix}`, event);
    }
  };
}

// Keep the old name as an alias so `notify.console()` still works.
export { consoleNotifier as console };

/** Sends notifications to Slack via incoming webhook. */
export function slack(webhookUrl: string): NotifyHandler {
  return async (event) => {
    let text: string;

    if (event.type === "budget_kill") {
      text = `:rotating_light: *Wickd Budget Kill*\nAgent spent *$${(event.spend ?? 0).toFixed(4)}* and hit the \`${event.trigger ?? "unknown"}\` cap.\nTrace: \`${event.traceId.slice(0, 8)}\``;
    } else if (event.type === "run_complete") {
      const s = event.summary;
      text = `:white_check_mark: *Agent Run Complete*\nCost: *$${(s?.runSpend ?? 0).toFixed(4)}* | Calls: ${s?.callCount ?? 0} | Status: ${event.status ?? "unknown"}`;
    } else {
      text = `Wickd event: ${JSON.stringify(event)}`;
    }

    if (!webhookUrl.startsWith("http")) return;

    try {
      await fetch(webhookUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
    } catch {
      // Network failures in notification handlers are non-fatal
    }
  };
}

/** Sends Slack Block Kit messages with interactive approve/deny buttons. */
export function slackInteractive(webhookUrl: string): NotifyHandler {
  return async (event) => {
    let body: string;

    if (event.type === "approval_request") {
      const traceId = event.traceId ?? "unknown";
      const ev = event as unknown as Record<string, unknown>;
      const actionName = (ev.name as string) ?? "unknown";
      const agentName = event.agentName ?? "unknown";
      const argsPreview = (ev.argsPreview as string) ?? "";
      const timestamp = (ev.timestamp as string) ?? "";

      const actionValue = JSON.stringify({
        trace_id: traceId,
        approval_name: actionName,
      });

      const blocks = [
        {
          type: "header",
          text: {
            type: "plain_text",
            text: "\u{1F512} Approval Required: " + actionName,
            emoji: true,
          },
        },
        {
          type: "context",
          elements: [
            {
              type: "mrkdwn",
              text: `*Agent:* \`${agentName}\` | *Trace:* \`${traceId.slice(0, 8)}\` | *Time:* ${timestamp || "now"}`,
            },
          ],
        },
        {
          type: "section",
          text: {
            type: "mrkdwn",
            text:
              `The agent wants to execute *\`${actionName}\`*.` +
              (argsPreview ? `\n\`\`\`${argsPreview.slice(0, 500)}\`\`\`` : ""),
          },
        },
        { type: "divider" },
        {
          type: "actions",
          elements: [
            {
              type: "button",
              text: { type: "plain_text", text: "\u2705 Approve", emoji: true },
              style: "primary",
              action_id: "wickd_approve",
              value: actionValue,
            },
            {
              type: "button",
              text: { type: "plain_text", text: "\u274C Deny", emoji: true },
              style: "danger",
              action_id: "wickd_deny",
              value: actionValue,
            },
          ],
        },
      ];

      body = JSON.stringify({ blocks });
    } else if (event.type === "budget_kill") {
      body = JSON.stringify({
        text: `:rotating_light: *Wickd Budget Kill*\nAgent spent *$${(event.spend ?? 0).toFixed(4)}* and hit the \`${event.trigger ?? "unknown"}\` cap.\nTrace: \`${event.traceId.slice(0, 8)}\``,
      });
    } else if (event.type === "run_complete") {
      const s = event.summary;
      body = JSON.stringify({
        text: `:white_check_mark: *Agent Run Complete*\nCost: *$${(s?.runSpend ?? 0).toFixed(4)}* | Calls: ${s?.callCount ?? 0} | Status: ${event.status ?? "unknown"}`,
      });
    } else {
      body = JSON.stringify({ text: `Wickd event: ${JSON.stringify(event)}` });
    }

    if (!webhookUrl.startsWith("http")) return;

    try {
      await fetch(webhookUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body,
      });
    } catch {
      // Network failures in notification handlers are non-fatal
    }
  };
}

/** Sends notifications as JSON POST to any URL. */
export function webhook(url: string, headers?: Record<string, string>): NotifyHandler {
  return async (event) => {
    try {
      await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...headers },
        body: JSON.stringify(event),
      });
    } catch {
      // Network failures in notification handlers are non-fatal
    }
  };
}
