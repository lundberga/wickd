/**
 * Wickd + OpenAI SDK — Budget-limited agent (TypeScript).
 *
 * This example shows how Wickd automatically intercepts OpenAI API calls
 * and enforces a budget cap. No changes to your OpenAI code needed.
 *
 * Requirements:
 *     npm install wickd openai
 *     export OPENAI_API_KEY=sk-...
 *
 * Run:
 *     npx tsx main.ts
 */

import { agent, Budget, BudgetExceeded, notify } from "wickd";
import OpenAI from "openai";

const researchAgent = agent({
  name: "research_agent",
  fn: async (task: string) => {
    const client = new OpenAI();

    // Step 1: Get an overview
    const overview = await client.chat.completions.create({
      model: "gpt-4o-mini",
      messages: [
        { role: "system", content: "You are a concise research assistant." },
        { role: "user", content: `Give a brief overview of: ${task}` },
      ],
    });
    console.log(`  Overview: ${overview.choices[0].message.content?.slice(0, 100)}...`);

    // Step 2: Get key points
    const details = await client.chat.completions.create({
      model: "gpt-4o-mini",
      messages: [
        { role: "system", content: "You are a concise research assistant." },
        { role: "user", content: `List 3 key points about: ${task}` },
      ],
    });
    console.log(`  Details: ${details.choices[0].message.content?.slice(0, 100)}...`);

    return {
      overview: overview.choices[0].message.content,
      details: details.choices[0].message.content,
    };
  },
  budget: new Budget({ perRun: 0.5, daily: 5.0 }),
  onBudgetKill: notify.console(),
});

async function main() {
  try {
    const result = await researchAgent.run("the history of the internet");
    console.log("\nAgent completed successfully.");
  } catch (e) {
    if (e instanceof BudgetExceeded) {
      console.log(`\nBudget exceeded: ${e.message}`);
    } else {
      throw e;
    }
  }

  console.log("Run 'wickd traces' to see the full cost breakdown.");
}

main();
