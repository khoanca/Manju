import { handleLlmCorrect } from "./handler.ts";

Deno.serve((req) => handleLlmCorrect(req));
