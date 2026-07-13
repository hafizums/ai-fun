/**
 * Node-based unit tests for app/web/workflow.js (no network).
 * Run: node tests/js/workflow.test.mjs
 */
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "../..");
const code = fs.readFileSync(path.join(root, "app/web/workflow.js"), "utf8");

const sandbox = { console };
sandbox.globalThis = sandbox;
vm.runInNewContext(code, sandbox);
const W = sandbox.AIFunWorkflow;
if (!W) throw new Error("AIFunWorkflow not defined");

function assert(cond, msg) {
  if (!cond) throw new Error(msg || "assertion failed");
}

assert(W.getStatusView("DRAFT").action === "generate-prompts", "draft action");
assert(W.getStatusView("PROMPT_READY").action === "generate-base-image", "prompt ready");
assert(W.getStatusView("CONTROL_VIDEO_READY").action === "assemble-final-video", "assemble");
assert(W.getStatusView("COMPLETED").action === "download-final", "download");
assert(W.isActiveStatus("PROMPT_GENERATING"), "active prompt");
assert(W.isActiveStatus("FINAL_VIDEO_ASSEMBLING"), "active final");
assert(!W.isActiveStatus("PROMPT_READY"), "ready not active");
assert(W.shouldPoll("CHARACTER_EDITING"), "poll editing");
assert(!W.shouldPoll("COMPLETED"), "no poll completed");

assert(
  W.retryForFailedStage("source_video_generation").action === "generate-source-video",
  "retry source"
);
assert(
  W.retryForFailedStage("final_video_assembly").paid === false,
  "final retry unpaid"
);
assert(W.retryForFailedStage("nope") === null, "unknown stage");

assert(W.normalizeProgress(150) === 100, "progress clamp high");
assert(W.normalizeProgress(-1) === 0, "progress clamp low");
assert(W.nextBackoffMs(0) === 2000, "backoff 0");
assert(W.nextBackoffMs(1) === 4000, "backoff 1");
assert(W.nextBackoffMs(5) === 8000, "backoff cap");

assert(W.jobPathFromLocation("/jobs/abc-123") === "abc-123", "job path");
assert(W.jobPathFromLocation("/") === null, "root path");

assert(
  W.artifactFileUrl("j1", "final-video", "t1").endsWith("?v=t1"),
  "cache bust"
);
assert(
  W.formatApiError(409, null).includes("Refreshing"),
  "409 message"
);
assert(W.stepState("prompt", "PROMPT_READY", null) === "Completed", "step done");
assert(W.stepState("base-image", "PROMPT_READY", null) === "Ready", "step ready");
assert(W.stepState("final-video", "DRAFT", null) === "Waiting", "step waiting");

// Ready states must not auto-fire actions (mapping only exposes action for UI click).
for (const status of [
  "PROMPT_READY",
  "BASE_IMAGE_READY",
  "REFERENCE_READY",
  "CHARACTER_EDIT_READY",
  "SOURCE_VIDEO_READY",
  "CONTROL_VIDEO_READY",
]) {
  assert(W.shouldPoll(status) === false, "no poll on " + status);
}

console.log("workflow.test.mjs: ok");
