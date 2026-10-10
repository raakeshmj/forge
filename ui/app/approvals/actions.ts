"use server";

import { revalidatePath } from "next/cache";

import { apiPost } from "@/lib/api";

export interface DecisionState {
  ok: boolean;
  message: string;
}

/**
 * Forward an operator's decision with the operator's own token (ADR 0010, ADR 0012).
 * The token is used for this one request and never stored, logged, or rendered.
 */
export async function decide(_previous: DecisionState | null, form: FormData): Promise<DecisionState> {
  const id = String(form.get("approval_id") ?? "");
  const decision = String(form.get("decision") ?? "");
  const reason = String(form.get("reason") ?? "").trim();
  const token = String(form.get("operator_token") ?? "");
  if (!/^[0-9a-f-]{36}$/.test(id) || !["approve", "deny"].includes(decision)) {
    return { ok: false, message: "Malformed decision." };
  }
  if (!reason || !token) {
    return { ok: false, message: "A reason and your operator token are required." };
  }
  const { status, detail } = await apiPost(`/approvals/${id}/decision`, { decision, reason }, token);
  revalidatePath("/approvals");
  if (status === 200) return { ok: true, message: `Recorded: ${decision}.` };
  if (status === 401) return { ok: false, message: "That operator token was not accepted." };
  return { ok: false, message: `Not recorded (${status}): ${detail}` };
}
