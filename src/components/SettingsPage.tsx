import type { ReactNode } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";

export type SettingsSectionKey =
  | "profile"
  | "model"
  | "vision"
  | "speechInput"
  | "voice"
  | "live2d"
  | "interface"
  | "agent"
  | "memory"
  | "mcp"
  | "skills";

export type SettingsPageSection = {
  key: SettingsSectionKey;
  label: string;
  icon: ReactNode;
  content: ReactNode;
};

type SettingsPageProps = {
  open: boolean;
  activeSection: SettingsSectionKey;
  sections: SettingsPageSection[];
  onSectionChange: (section: SettingsSectionKey) => void;
  onClose: () => void;
};

export function SettingsPage({
  open,
  activeSection,
  sections,
  onSectionChange,
  onClose
}: SettingsPageProps) {
  const active = sections.find((section) => section.key === activeSection) ?? sections[0];

  if (!open) {
    return null;
  }

  return (
    <aside className="settings-page-overlay is-open" role="dialog" aria-modal="true" aria-label="设置">
      <div className="settings-page-shell glass-panel">
        <div className="settings-page-head">
          <div>
            <span className="eyebrow">Control</span>
            <h2>设置</h2>
          </div>
          <button className="icon-button" onClick={onClose} type="button" aria-label="关闭设置">
            <ChevronLeft size={18} />
          </button>
        </div>

        <div className="settings-page-layout">
          <nav className="settings-nav" aria-label="设置条目">
            {sections.map((section) => (
              <button
                className={`settings-nav-item ${section.key === activeSection ? "is-active" : ""}`}
                key={section.key}
                onClick={() => onSectionChange(section.key)}
                type="button"
              >
                <span>
                  {section.icon}
                  {section.label}
                </span>
                <ChevronRight size={17} />
              </button>
            ))}
          </nav>

          <section className="settings-content" aria-label={active?.label ?? "设置内容"}>
            <div className="settings-content-head">
              <div>
                <span className="eyebrow">Settings</span>
                <h3>{active?.label ?? "设置"}</h3>
              </div>
            </div>
            <div className="settings-content-scroll">
              {sections.map((section) => (
                <div
                  className={`settings-section-panel ${section.key === activeSection ? "is-active" : ""}`}
                  key={section.key}
                  aria-hidden={section.key !== activeSection}
                >
                  {section.content}
                </div>
              ))}
            </div>
          </section>
        </div>
      </div>
    </aside>
  );
}
