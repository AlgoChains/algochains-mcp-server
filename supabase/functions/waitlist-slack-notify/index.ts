import { handleWaitlistWebhook } from "../_shared/waitlist_notify.ts";

Deno.serve((request) => handleWaitlistWebhook(request));
