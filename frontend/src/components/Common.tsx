import { Layers3 } from "lucide-react";
import { ReactNode } from "react";
import { states } from "../api";

export function Badge({ status }: { status: string }) {
  return (
    <span className={`badge state-${status}`}>
      <i />
      {states[status] || status}
    </span>
  );
}

export function Empty({
  title,
  children,
}: {
  title: string;
  children?: ReactNode;
}) {
  return (
    <div className="empty">
      <Layers3 size={30} />
      <h3>{title}</h3>
      <p>{children}</p>
    </div>
  );
}
