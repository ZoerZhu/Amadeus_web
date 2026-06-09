import type { ReactNode } from "react";
import { ChevronRight } from "lucide-react";

type ConfigGroupProps = {
  title: string;
  icon: ReactNode;
  open: boolean;
  onToggle: () => void;
  children: ReactNode;
};

export function ConfigGroup({ title, icon, open, onToggle, children }: ConfigGroupProps) {
  return (
    <section className={`config-group ${open ? "is-open" : ""}`}>
      <button className="config-group-head" onClick={onToggle} type="button">
        <span>
          {icon}
          {title}
        </span>
        <ChevronRight size={17} />
      </button>
      <div className="config-group-body">{children}</div>
    </section>
  );
}
