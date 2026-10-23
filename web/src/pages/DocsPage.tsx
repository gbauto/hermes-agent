import { useLayoutEffect, type ComponentType } from "react";
import {
  BookOpen,
  Bot,
  ExternalLink,
  KeyRound,
  MessageSquare,
  Plug,
  Settings,
  Terminal,
} from "lucide-react";
import { useI18n } from "@/i18n";
import { usePageHeader } from "@/contexts/usePageHeader";
import { cn } from "@/lib/utils";
import { PluginSlot } from "@/plugins";

export const HERMES_DOCS_URL = "https://hermes-agent.nousresearch.com/docs/";

const DS_BUTTON_OUTLINED_LINK_CN = cn(
  "group relative inline-grid grid-cols-[auto_1fr_auto] items-center",
  "px-[.9em_.75em] py-[1.25em] gap-2",
  "leading-0 font-bold tracking-[0.2em] uppercase",
  "text-midground bg-transparent shadow-midground",
  "shadow-[inset_-1px_-1px_0_0_#00000080,inset_1px_1px_0_0_#ffffff80]",
);

type DocLink = {
  title: string;
  description: string;
  href: string;
  icon: ComponentType<{ className?: string }>;
  tint: string;
};

const QUICK_LINKS: DocLink[] = [
  {
    title: "Quickstart",
    description: "Install Hermes, launch the CLI, and run a first task.",
    href: `${HERMES_DOCS_URL}getting-started/quickstart`,
    icon: Terminal,
    tint: "bg-blue-100 text-blue-600",
  },
  {
    title: "Web dashboard",
    description: "Open, configure, and troubleshoot this local dashboard.",
    href: `${HERMES_DOCS_URL}user-guide/features/web-dashboard`,
    icon: BookOpen,
    tint: "bg-emerald-100 text-emerald-600",
  },
  {
    title: "Configuration",
    description: "Set providers, models, tools, and profile-level options.",
    href: `${HERMES_DOCS_URL}user-guide/configuration`,
    icon: Settings,
    tint: "bg-violet-100 text-violet-600",
  },
];

const FEATURE_LINKS: DocLink[] = [
  {
    title: "CLI reference",
    description: "Commands, flags, and dashboard launch options.",
    href: `${HERMES_DOCS_URL}reference/cli-commands`,
    icon: Terminal,
    tint: "bg-slate-100 text-slate-600",
  },
  {
    title: "Keys and providers",
    description: "Model provider setup and API-key configuration.",
    href: `${HERMES_DOCS_URL}user-guide/configuring-models`,
    icon: KeyRound,
    tint: "bg-amber-100 text-amber-600",
  },
  {
    title: "Kanban",
    description: "Coordinate autonomous work with Hermes Kanban.",
    href: `${HERMES_DOCS_URL}user-guide/features/kanban`,
    icon: Bot,
    tint: "bg-cyan-100 text-cyan-600",
  },
  {
    title: "Skills",
    description: "Use reusable procedural context for recurring work.",
    href: `${HERMES_DOCS_URL}user-guide/features/skills`,
    icon: MessageSquare,
    tint: "bg-pink-100 text-pink-600",
  },
  {
    title: "Plugins",
    description: "Extend the dashboard and runtime safely.",
    href: `${HERMES_DOCS_URL}user-guide/features/extending-the-dashboard`,
    icon: Plug,
    tint: "bg-orange-100 text-orange-600",
  },
];

const ON_THIS_PAGE = ["Start here", "Common tasks", "Need more?"];

function SectionTitle({ children }: { children: string }) {
  return (
    <div className="flex items-center gap-4">
      <h2 className="shrink-0 text-[1.35rem] font-semibold tracking-[-0.03em] text-slate-950">
        {children}
      </h2>
      <div className="h-px min-w-0 flex-1 bg-slate-200" />
    </div>
  );
}

function DocLinkCard({ link }: { link: DocLink }) {
  const Icon = link.icon;

  return (
    <a
      href={link.href}
      target="_blank"
      rel="noopener noreferrer"
      className={cn(
        "group block rounded-xl border border-slate-200 bg-white p-6",
        "transition hover:-translate-y-0.5 hover:border-blue-200 hover:shadow-[0_12px_30px_rgba(15,23,42,0.08)]",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-300",
      )}
    >
      <span className={cn("mb-4 inline-grid size-10 place-items-center rounded-full", link.tint)}>
        <Icon className="size-4" />
      </span>
      <span className="flex items-start justify-between gap-3">
        <span className="text-lg font-semibold tracking-[-0.02em] text-slate-950">
          {link.title}
        </span>
        <ExternalLink className="mt-1 size-4 shrink-0 text-slate-400 transition group-hover:text-blue-600" />
      </span>
      <span className="mt-3 block text-base leading-7 text-slate-600">
        {link.description}
      </span>
    </a>
  );
}

export default function DocsPage() {
  const { t } = useI18n();
  const { setEnd } = usePageHeader();

  useLayoutEffect(() => {
    setEnd(
      <a
        href={HERMES_DOCS_URL}
        target="_blank"
        rel="noopener noreferrer"
        className={DS_BUTTON_OUTLINED_LINK_CN}
      >
        <ExternalLink className="size-3.5" />
        {t.app.openDocumentation}
      </a>,
    );
    return () => {
      setEnd(null);
    };
  }, [setEnd, t]);

  return (
    <div className="min-h-0 w-full min-w-0 flex-1 overflow-auto rounded-sm border border-slate-200 bg-white text-slate-900 [color-scheme:light] [font-family:ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,'Segoe_UI',sans-serif]">
      <PluginSlot name="docs:top" />
      <div className="grid min-h-full grid-cols-1 lg:grid-cols-[minmax(0,1fr)_16rem]">
        <main className="mx-auto w-full max-w-5xl px-6 py-12 sm:px-10 lg:px-16 lg:py-16">
          <section className="border-b border-slate-200 pb-14">
            <p className="mb-4 text-xs font-semibold uppercase tracking-[0.24em] text-slate-500">
              Hermes Agent Documentation
            </p>
            <h1 className="max-w-3xl text-5xl font-normal tracking-[-0.06em] text-slate-950 sm:text-6xl">
              Documentation
            </h1>
            <p className="mt-8 max-w-4xl text-xl leading-8 text-slate-600 sm:text-2xl sm:leading-10">
              Use these pointers to jump from the local dashboard to the canonical
              Hermes Agent docs. No generated docs are mirrored here.
            </p>
          </section>

          <section id="start-here" className="pt-10">
            <SectionTitle>Start here</SectionTitle>
            <div className="mt-8 rounded-2xl border border-slate-200 bg-white p-6">
              <div className="grid gap-5 md:grid-cols-3">
                {QUICK_LINKS.map((link) => (
                  <DocLinkCard key={link.href} link={link} />
                ))}
              </div>
            </div>
          </section>

          <section id="common-tasks" className="pt-12">
            <SectionTitle>Common tasks</SectionTitle>
            <div className="mt-8 rounded-2xl border border-slate-200 bg-white p-6">
              <div className="grid gap-5 md:grid-cols-2 xl:grid-cols-3">
                {FEATURE_LINKS.map((link) => (
                  <DocLinkCard key={link.href} link={link} />
                ))}
              </div>
            </div>
          </section>

          <section id="need-more" className="pt-12 pb-16">
            <SectionTitle>Need more?</SectionTitle>
            <div className="mt-8 rounded-2xl border border-slate-200 bg-slate-50 p-6">
              <p className="text-base leading-7 text-slate-600">
                The docs site is the source of truth for setup, configuration,
                dashboard features, and troubleshooting. Open the full docs when
                you need the complete navigation tree.
              </p>
              <a
                href={HERMES_DOCS_URL}
                target="_blank"
                rel="noopener noreferrer"
                className="mt-5 inline-flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-4 py-3 text-sm font-semibold text-slate-950 transition hover:border-blue-200 hover:text-blue-700"
              >
                Open full documentation
                <ExternalLink className="size-4" />
              </a>
            </div>
          </section>
        </main>

        <aside className="hidden border-l border-slate-200 bg-slate-50/80 px-6 py-10 lg:block">
          <div className="sticky top-6 rounded-xl border border-slate-200 bg-white p-5">
            <p className="mb-4 text-sm font-semibold text-slate-950">On this page</p>
            <nav className="space-y-1">
              {ON_THIS_PAGE.map((label) => (
                <a
                  key={label}
                  href={`#${label.toLowerCase().replaceAll(" ", "-").replace("?", "")}`}
                  className="flex items-center gap-2 rounded-md px-3 py-2 text-sm text-slate-600 transition hover:bg-blue-50 hover:text-blue-700"
                >
                  <BookOpen className="size-4 text-slate-400" />
                  {label}
                </a>
              ))}
            </nav>
          </div>
        </aside>
      </div>
      <PluginSlot name="docs:bottom" />
    </div>
  );
}
