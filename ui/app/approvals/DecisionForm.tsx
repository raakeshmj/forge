"use client";

import { useActionState } from "react";

import { decide, type DecisionState } from "./actions";

export function DecisionForm({ approvalId }: { approvalId: string }) {
  const [state, action, pending] = useActionState<DecisionState | null, FormData>(decide, null);
  return (
    <form className="decide" action={action}>
      <input type="hidden" name="approval_id" value={approvalId} />
      <select name="decision" defaultValue="approve" aria-label="Decision">
        <option value="approve">Approve this exact call</option>
        <option value="deny">Deny</option>
      </select>
      <textarea name="reason" placeholder="Reason (recorded in the audit log)" required rows={2} />
      <input name="operator_token" type="password" placeholder="Operator token" autoComplete="off" required />
      <button type="submit" disabled={pending}>
        {pending ? "Recording…" : "Record decision"}
      </button>
      {state ? <div className={`notice ${state.ok ? "ok" : "bad"}`}>{state.message}</div> : null}
    </form>
  );
}
