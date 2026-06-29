import type { FormEvent } from "react";
import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  ArrowRight,
  BarChart3,
  FileText,
  GitBranch,
  Package,
  RadioTower,
  Send,
  Sparkles,
} from "lucide-react";

const HOME_CARDS = [
  {
    art: "/skill-art/ai.jpg",
    description: "Resume a Hermes session or start a new operator thread.",
    label: "Hermes Chat",
    meta: "Live",
    to: "/chat",
  },
  {
    art: "/skill-art/mlops.jpg",
    description: "Review agent runs, traces, failures, and Supabase telemetry.",
    label: "Observability",
    meta: "Supabase",
    to: "/logs",
  },
  {
    art: "/skill-art/design-system.jpg",
    description: "Browse installed skills, Aura references, and Canopy-backed catalogs.",
    label: "Skills",
    meta: "Catalog",
    to: "/skills",
  },
  {
    art: "/skill-art/marketing.jpg",
    description: "Open report artifacts, client packages, and approved templates.",
    label: "Artifacts",
    meta: "Reports",
    to: "/artifacts",
  },
  {
    art: "/skill-art/motion.jpg",
    description: "Inspect repo activity, commits, and project-level source maps.",
    label: "Repos",
    meta: "Git",
    to: "/repos",
  },
];

const QUICK_PROMPTS = [
  "Summarize the latest ecom telemetry and open risks.",
  "Review recent failed skill runs and tell me what to fix first.",
  "Find the newest report artifacts and recommend the next polish pass.",
];

export default function HomePage() {
  const navigate = useNavigate();
  const [draft, setDraft] = useState("");
  const promptPlaceholder = useMemo(
    () => "Ask Hermes to investigate, build, review, scrape, or summarize...",
    [],
  );

  const submitPrompt = (value: string) => {
    const text = value.trim();
    if (!text) return;
    const qs = new URLSearchParams({ prompt: text, send: "1" });
    navigate(`/chat?${qs.toString()}`);
  };

  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    submitPrompt(draft);
  };

  return (
    <main className="home-chat-page">
      <section className="home-chat-hero" aria-labelledby="home-title">
        <div className="home-chat-kicker">
          <Sparkles className="h-3.5 w-3.5" />
          <span>Home chat gallery</span>
        </div>

        <h1 id="home-title">Create operational momentum with Hermes.</h1>
        <p>
          A GBauto home surface adapted from the approved Aura home template:
          chat first, routes nearby, and the dashboard context one click away.
        </p>

        <form className="home-chat-composer" onSubmit={onSubmit}>
          <textarea
            aria-label="Message Hermes"
            placeholder={promptPlaceholder}
            rows={4}
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
          />
          <div className="home-chat-composer-footer">
            <div className="home-chat-shortcuts">
              {QUICK_PROMPTS.map((prompt) => (
                <button
                  key={prompt}
                  onClick={() => submitPrompt(prompt)}
                  type="button"
                >
                  {prompt}
                </button>
              ))}
            </div>
            <button className="home-chat-submit" disabled={!draft.trim()} type="submit">
              <Send className="h-3.5 w-3.5" />
              Send to Hermes
            </button>
          </div>
        </form>
      </section>

      <section className="home-chat-route-strip" aria-label="Primary routes">
        <Link to="/overview">
          <BarChart3 className="h-4 w-4" />
          <span>Overview</span>
        </Link>
        <Link to="/repos">
          <GitBranch className="h-4 w-4" />
          <span>Repos</span>
        </Link>
        <Link to="/skills">
          <Package className="h-4 w-4" />
          <span>Skills</span>
        </Link>
        <Link to="/langfuse">
          <RadioTower className="h-4 w-4" />
          <span>Langfuse</span>
        </Link>
        <Link to="/artifacts">
          <FileText className="h-4 w-4" />
          <span>Artifacts</span>
        </Link>
      </section>

      <section className="home-chat-gallery" aria-label="Home gallery">
        {HOME_CARDS.map((card) => (
          <Link className="home-chat-card" key={card.to} to={card.to}>
            <img alt="" decoding="async" loading="lazy" src={card.art} />
            <span className="home-chat-card-meta">{card.meta}</span>
            <strong>{card.label}</strong>
            <p>{card.description}</p>
            <span className="home-chat-card-action">
              Open <ArrowRight className="h-3.5 w-3.5" />
            </span>
          </Link>
        ))}
      </section>
    </main>
  );
}
