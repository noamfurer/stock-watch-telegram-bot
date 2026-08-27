import assert from "node:assert/strict";
import test from "node:test";

import {
  isMonitoringWindow,
  israelTime,
  israelTimestamp,
} from "../netlify/functions/_shared/time.ts";

test("uses Israel daylight-saving time in summer", () => {
  const local = israelTime(new Date("2026-08-27T13:07:00Z"));
  assert.equal(local.localDate, "2026-08-27");
  assert.equal(local.hour, 16);
  assert.equal(local.minute, 7);
  assert.equal(israelTimestamp(new Date("2026-08-27T13:07:00Z")), "2026-08-27T16:07:00[Asia/Jerusalem]");
});

test("uses Israel standard time in winter", () => {
  const local = israelTime(new Date("2026-12-10T08:07:00Z"));
  assert.equal(local.hour, 10);
  assert.equal(local.minute, 7);
  assert.equal(isMonitoringWindow(new Date("2026-12-10T08:07:00Z")), true);
});

test("runs only Monday-Friday from 10:00 until 18:00 Israel time", () => {
  assert.equal(isMonitoringWindow(new Date("2026-08-27T06:59:00Z")), false);
  assert.equal(isMonitoringWindow(new Date("2026-08-27T07:00:00Z")), true);
  assert.equal(isMonitoringWindow(new Date("2026-08-27T14:59:00Z")), true);
  assert.equal(isMonitoringWindow(new Date("2026-08-27T15:00:00Z")), false);
  assert.equal(isMonitoringWindow(new Date("2026-08-29T09:00:00Z")), false);
});
