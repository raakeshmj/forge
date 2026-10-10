"use client";

export default function PageError({ error }: { error: Error & { digest?: string } }) {
  return (
    <div>
      <h1>Unavailable</h1>
      <p className="notice bad">
        The FORGE API could not serve this page ({error.digest ?? error.message}). Check that the API
        is running and reachable from the UI server.
      </p>
    </div>
  );
}
