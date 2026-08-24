"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const {
  DashboardContractError,
  assertItemsResponse,
  deriveLane,
  itemToViewModel,
  analysisPackageProjection,
  assertDetailResponse,
  detailEvidence,
  detailSections,
} = require("../static/app.js");

const root = path.resolve(__dirname, "..");
const itemsPayload = JSON.parse(fs.readFileSync(path.join(root, "tests/fixtures/api-items-response-v5.json"), "utf8"));
const detailPayload = JSON.parse(fs.readFileSync(path.join(root, "tests/fixtures/api-item-detail-v2.json"), "utf8"));

assert.doesNotThrow(() => assertItemsResponse(itemsPayload));
assert.equal(itemsPayload.dashboard_request_counter_scope, "dashboard_read_only_request");
const views = itemsPayload.items.map((item) => itemToViewModel(item, Date.parse("2026-08-15T10:20:32.305Z")));
assert.deepEqual(
  Object.fromEntries(views.map((view) => [view.id, view.lane])),
  {
    "MATH-WAIT-001": "waiting",
    "CS408-RUN-001": "processing",
    "EN-COMPLETE-001": "completed",
    "MATH-COMPLETE-002": "completed",
    "CS408-COMPLETE-003": "completed",
    "CS408-FAIL-004": "technical_failure",
  },
);

const needsSol = views.filter((view) => view.source.quality_status === "issues_found");
assert.ok(needsSol.length >= 2);
assert.ok(needsSol.every((view) => view.lane === "completed" && view.quality_label === "等待 Sol 质量复核"));
assert.deepEqual(new Set(views.filter((view) => view.source.execution_status === "succeeded").map((view) => view.subject)), new Set(["math", "cs408", "english"]));

const failed = itemsPayload.items.find((item) => item.execution_status === "failed");
assert.equal(deriveLane(failed), "technical_failure");
for (const mutation of [
  { report_disposition: null },
  { terminal_error_code: null },
  { quality_status: "issues_found" },
]) {
  assert.throws(() => deriveLane({ ...failed, ...mutation }), DashboardContractError);
}
assert.throws(() => deriveLane({ ...failed, report_disposition: "quarantined" }), DashboardContractError);

const english = views.find((view) => view.id === "EN-COMPLETE-001");
const detail = assertDetailResponse(detailPayload, english);
assert.equal(detailPayload.dashboard_request_formal_write_count, 0);
assert.equal(detail.analysis.execution_status, "completed");
assert.equal(detail.critical_review.execution_status, "not_started");
const rail = detailEvidence(detail, english.source);
assert.equal(rail.find((node) => node.key === "luna").state, "complete");
assert.equal(rail.find((node) => node.key === "mcp").state, "unverified");
assert.equal(rail.find((node) => node.key === "review").state, "unverified");
const sections = detailSections(english, detail);
assert.ok(sections.luna_provider.some((line) => line.includes("Provider") && line.includes("未公开")));
assert.ok(sections.mcp.some((line) => line.includes("MCP") && line.includes("不复制 Analysis")));
assert.ok(sections.quality_findings.includes("critical_review.execution_status=not_started"));

const multiAgentPayload = structuredClone(detailPayload);
Object.assign(multiAgentPayload.item, {
  analysis_package_schema_version: "study-intake-analysis-package-v2",
  luna_report_count: 3,
  luna_diagnostic_count: 1,
  terra_final_report_sha256: "8".repeat(64),
  terra_final_schema_version: "terra_final_report_v2",
});
const multiAgentDetail = assertDetailResponse(multiAgentPayload, english);
const multiAgentSections = detailSections(english, multiAgentDetail);
assert.equal(multiAgentDetail.analysis_package_projection.luna_report_count, 3);
assert.ok(multiAgentSections.luna_provider.includes("Luna 独立调查报告：3 份"));
assert.ok(multiAgentSections.luna_provider.includes("Luna 失败诊断：1 份"));
assert.ok(multiAgentSections.report.some((line) => line.includes("Terra 最终报告")));

const tamperedProjection = {
  ...multiAgentPayload.item,
  terra_final_report_sha256: "tampered",
};
assert.equal(analysisPackageProjection(tamperedProjection), null);
const tamperedPayload = structuredClone(multiAgentPayload);
tamperedPayload.item.terra_final_report_sha256 = "tampered";
const degradedDetail = assertDetailResponse(tamperedPayload, english);
assert.equal(degradedDetail.analysis_package_projection, undefined);
assert.ok(!detailSections(english, degradedDetail).summary.some((line) => line.includes("Multi-Agent V2")));

const missingField = { ...itemsPayload.items[0] };
delete missingField.execution_status;
assert.throws(() => deriveLane(missingField), DashboardContractError);

console.log("task_view_model_test: 26 assertions passed");
