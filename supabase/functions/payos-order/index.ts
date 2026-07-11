import { handlePayosOrder } from "./handler.ts";

Deno.serve((req) => handlePayosOrder(req));
