// Spawns the Python beside this file and returns its parsed JSON, or
// null on any failure. Split out from extension.mjs so it can be tested
// without the Copilot SDK, which only resolves inside the runtime.

import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const HERE = dirname(fileURLToPath(import.meta.url));

// A session hook that hangs is worse than one that never runs, so every call
// is bounded and every failure resolves to null rather than throwing.
const TIMEOUT_MS = 10_000;
const MAX_OUTPUT_BYTES = 256 * 1024;

export function runScript(script, payload) {
    return new Promise((resolve) => {
        let settled = false;
        const finish = (value) => {
            if (settled) return;
            settled = true;
            resolve(value);
        };

        let child;
        try {
            child = spawn("python3", [join(HERE, script)], {
                stdio: ["pipe", "pipe", "ignore"],
            });
        } catch {
            return finish(null);
        }

        const timer = setTimeout(() => {
            try {
                child.kill("SIGKILL");
            } catch {
                // Already gone. Nothing to do.
            }
            finish(null);
        }, TIMEOUT_MS);

        let out = "";
        let truncated = false;
        child.stdout.on("data", (chunk) => {
            if (truncated) return;
            out += chunk;
            if (out.length > MAX_OUTPUT_BYTES) {
                truncated = true;
                out = "";
                try {
                    child.kill("SIGKILL");
                } catch {
                    // Already gone.
                }
            }
        });

        child.on("error", () => {
            clearTimeout(timer);
            finish(null);
        });

        child.on("close", (code) => {
            clearTimeout(timer);
            if (truncated) return finish(null);
            if (!out.trim()) {
                // Silence and a clean exit is a real answer: the script
                // decided there was nothing to say. Silence after a crash is
                // not, and should not be mistaken for one.
                return finish(code === 0 ? {} : null);
            }
            try {
                finish(JSON.parse(out));
            } catch {
                finish(null);
            }
        });

        try {
            child.stdin.end(JSON.stringify(payload));
        } catch {
            // The close handler still resolves this call.
        }
    });
}
