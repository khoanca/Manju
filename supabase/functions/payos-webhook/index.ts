import { handlePayosWebhook } from "./handler.ts";

Deno.serve((req) => handlePayosWebhook(req));
