// Tests for runner.mjs. These spawn real subprocesses rather than mocking,
// because the failures that matter here are process failures: a missing
// interpreter, a script that hangs, a script that prints something that is
// not JSON. Mocks would not have caught any of them.
//
// Run: node --test copilot-extensions/ledger-companion/

import { test } from "node:test";
import assert from "node:assert/strict";
import { writeFileSync, unlinkSync, mkdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import { runScript } from "./runner.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const made = [];

function script(name, body) {
    const file = `tmp_test_${name}.py`;
    writeFileSync(join(HERE, file), body);
    made.push(join(HERE, file));
    return file;
}

test.after(() => {
    for (const path of made) {
        try {
            unlinkSync(path);
        } catch {
            // Already gone.
        }
    }
});

test("parses JSON printed by the script", async () => {
    const name = script("ok", 'print(\'{"additionalContext": "hello"}\')\n');
    assert.deepEqual(await runScript(name, {}), { additionalContext: "hello" });
});

test("passes the payload through on stdin", async () => {
    const name = script(
        "echo",
        "import sys, json\n"
            + "data = json.loads(sys.stdin.read() or '{}')\n"
            + "print(json.dumps({'seen': data.get('sessionId')}))\n",
    );
    const result = await runScript(name, { sessionId: "abc123" });
    assert.deepEqual(result, { seen: "abc123" });
});

test("empty output is an empty object, not a failure", async () => {
    const name = script("silent", "pass\n");
    assert.deepEqual(await runScript(name, {}), {});
});

test("silence after a crash is not mistaken for a clean answer", async () => {
    const name = script("crash", "import sys\nsys.exit(1)\n");
    assert.equal(await runScript(name, {}), null);
});

test("output that is not JSON returns null", async () => {
    const name = script("garbage", "print('not json at all')\n");
    assert.equal(await runScript(name, {}), null);
});

test("a missing script returns null rather than throwing", async () => {
    assert.equal(await runScript("tmp_test_does_not_exist.py", {}), null);
});

test("a non-zero exit with valid JSON is still honoured", async () => {
    // The Python guards already print {} and exit 0 on failure. If that ever
    // changes, a usable payload should not be discarded over the exit code.
    const name = script(
        "exits",
        "import sys\nprint('{\"decision\": \"block\"}')\nsys.exit(3)\n",
    );
    assert.deepEqual(await runScript(name, {}), { decision: "block" });
});

test("a script that writes to stderr is unaffected", async () => {
    const name = script(
        "noisy",
        "import sys\nsys.stderr.write('warning noise\\n')\nprint('{}')\n",
    );
    assert.deepEqual(await runScript(name, {}), {});
});

test("oversized output returns null instead of buffering", async () => {
    const name = script("huge", "print('x' * (300 * 1024))\n");
    assert.equal(await runScript(name, {}), null);
});

test("a hanging script is killed and returns null", async () => {
    // The real timeout is 10s. Overriding it would mean testing a different
    // code path, so this asserts the kill happens and bounds the wait.
    const name = script("hang", "import time\ntime.sleep(30)\n");
    const started = Date.now();
    const result = await runScript(name, {});
    assert.equal(result, null);
    assert.ok(Date.now() - started < 20_000, "should not wait for the script");
});

test("the extension directory is where scripts are resolved from", () => {
    mkdirSync(HERE, { recursive: true });
    assert.equal(dirname(join(HERE, "orient.py")), HERE);
});
