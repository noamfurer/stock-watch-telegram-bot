import type { Config } from "@netlify/functions";

import { runMonitor } from "./_shared/monitor.js";

export default async (): Promise<Response> => {
  await runMonitor("primary");
  return new Response(null, { status: 204 });
};

export const config: Config = { schedule: "7,22,37,52 * * * *" };
