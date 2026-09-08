// Extension: ledger-companion
//
// Two session hooks that keep the where-were-we ledger useful:
//   onSessionStart  reads the previous ledger for this directory and injects
//                   an orientation card, so context survives a lost session.
//   onAgentStop     compares what the ledger claims against what the session
//                   store holds, and asks for an update once drift is real.
//
// Both delegate to the Python beside this file. That is deliberate: the logic
// has one home and one test suite, and this file stays small enough to audit.
//
// This lives as an extension rather than a JSON command hook because command
// hooks are only read from <git root>/.github/hooks. That would mean shipping
// personal tooling inside every work repo. Extensions load from the user
// directory in every session, in every repo, committing nothing.

import { joinSession } from "@github/copilot-sdk/extension";
import { runScript } from "./runner.mjs";

await joinSession({
    hooks: {
        onSessionStart: async (input) => {
            const result = await runScript("orient.py", {
                sessionId: input?.sessionId ?? "",
                cwd: input?.workingDirectory ?? process.cwd(),
                source: input?.source ?? "startup",
            });
            const context = result?.additionalContext;
            if (typeof context === "string" && context.trim()) {
                return { additionalContext: context };
            }
        },

        onAgentStop: async (input) => {
            // Re-entry after a previous block. Speaking again here would talk
            // over the agent's own attempt to comply.
            if (input?.stopHookActive) return;

            const result = await runScript("nudge.py", {
                sessionId: input?.sessionId ?? "",
                stopHookActive: false,
            });
            const reason = result?.reason;
            if (result?.decision === "block" && typeof reason === "string" && reason.trim()) {
                return { decision: "block", reason };
            }
        },
    },
});
