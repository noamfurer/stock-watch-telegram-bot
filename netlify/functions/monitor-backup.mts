import type { Config } from "@netlify/functions";

import { runMonitor } from "./_shared/monitor.js";

export default async (): Promise<Response> => {
  await runMonitor("backup");
  return new Response(null, { status: 204 });
};

export const config: Config = { schedule: "12,27,42,57 * * * *" };
