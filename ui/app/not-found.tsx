import Link from "next/link";

export default function NotFound() {
  return (
    <div className="fault">
      <div className="fault-code mono">404</div>
      <h1>Not found</h1>
      <p className="sub">FORGE has no record of that.</p>
      <p>
        <Link href="/">Back to mission control →</Link>
      </p>
    </div>
  );
}
