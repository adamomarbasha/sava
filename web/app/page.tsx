import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = {
  title: "Sava — the library for everything you save",
  description:
    "Sava turns the videos and posts you save into a library you can actually search, understand, and ask questions of.",
};

/* ────────────────────────────────────────────────────────────────────────────
   Sava — marketing site.

   The visual brief in one line: ink black, citron, electric blue, set in heavy
   tightly-tracked type with a great deal of air around it.

   Three rules this page holds to, because they are what separate a real brand
   page from a template:

     1. **Citron is rationed.** It appears on the primary call to action, one
        word of the headline, and the section markers. Nowhere else. A page that
        uses its signature colour six times has no signature colour.
     2. **No decoration that is not information.** No mesh gradients, no floating
        3D shapes, no glass panels, no glow. Depth is three flat surfaces and a
        hairline; that is the same rule the app follows.
     3. **The product is the artwork.** The preview section shows the real
        interface rather than an abstract illustration standing in for it.
   ──────────────────────────────────────────────────────────────────────────── */

const SOURCES = ["YouTube", "TikTok", "Instagram", "Articles", "Screenshots"];

const STEPS = [
  {
    n: "01",
    title: "Save it in one press",
    body: "Bind Sava to the Action Button. Whatever is on screen — a Reel, a Short, a TikTok — is captured and filed without leaving the app you are in.",
  },
  {
    n: "02",
    title: "Sava reads it for you",
    body: "Transcript, captions, on-screen text and imagery are pulled apart and understood. You get a summary and the few points that actually mattered.",
  },
  {
    n: "03",
    title: "Find it by meaning",
    body: "Search for the idea, not the caption. “that pasta thing with the vodka sauce” finds the video even when those words were never written down.",
  },
  {
    n: "04",
    title: "Ask your library",
    body: "Ask across everything you have saved, or inside one collection. Answers cite the media they came from, so you can go straight to the source.",
  },
];

export default function Landing() {
  return (
    <main className="min-h-screen bg-[var(--sava-ink)] text-[var(--sava-text)] antialiased">
      <Nav />
      <Hero />
      <Sources />
      <How />
      <Preview />
      <Collections />
      <Privacy />
      <CTA />
      <Footer />
    </main>
  );
}

/* ── Navigation ─────────────────────────────────────────────────────────── */

function Nav() {
  return (
    <header className="sticky top-0 z-30 border-b border-[var(--sava-hairline)] bg-[var(--sava-ink)]/85 backdrop-blur-[6px]">
      <div className="mx-auto flex h-16 max-w-6xl items-center justify-between px-6">
        {/* The same lockup the app signs itself with: mark tile plus wordmark,
            identical composition to the home-screen icon. */}
        <Link href="/" className="flex items-center gap-2.5" aria-label="Sava — home">
          <img
            src="/sava-tile.png"
            alt=""
            width={28}
            height={28}
            className="rounded-[7px]"
          />
          <span className="text-[19px] font-extrabold tracking-[-0.055em] text-[var(--sava-text)]">
            Sava
          </span>
        </Link>
        <nav className="hidden items-center gap-8 md:flex">
          {[
            ["How it works", "#how"],
            ["Collections", "#collections"],
            ["Privacy", "#privacy"],
          ].map(([label, href]) => (
            <a
              key={href}
              href={href}
              className="text-[14px] text-[var(--sava-text-secondary)] transition-colors duration-200 hover:text-[var(--sava-text)]"
            >
              {label}
            </a>
          ))}
        </nav>
        <div className="flex items-center gap-3">
          <Link
            href="/auth/login"
            className="hidden text-[14px] text-[var(--sava-text-secondary)] transition-colors duration-200 hover:text-[var(--sava-text)] sm:block"
          >
            Sign in
          </Link>
          <Link
            href="/auth/register"
            className="rounded-[var(--sava-r-pill)] bg-[var(--sava-citron)] px-4 py-2 text-[14px] font-semibold text-[var(--sava-on-citron)] transition-opacity duration-200 hover:opacity-90"
          >
            Get Sava
          </Link>
        </div>
      </div>
    </header>
  );
}

/* ── Hero ───────────────────────────────────────────────────────────────── */

function Hero() {
  return (
    <section className="mx-auto max-w-6xl px-6 pb-[var(--sava-s-8)] pt-[var(--sava-s-8)]">
      <SectionMark>Personal media library</SectionMark>

      {/* The one oversized statement on the page. Tracking is tight enough that
          the line reads as a single object rather than as words in a row. */}
      <h1 className="mt-6 max-w-[15ch] text-[clamp(2.75rem,9vw,6.5rem)] font-extrabold leading-[0.94] tracking-[-0.05em]">
        You save it.
        <br />
        Sava{" "}
        <span className="text-[var(--sava-citron)]">remembers</span> it.
      </h1>

      <p className="mt-8 max-w-[52ch] text-[clamp(1.05rem,2vw,1.3rem)] leading-[1.6] text-[var(--sava-text-secondary)]">
        The videos you keep meaning to come back to are scattered across four
        apps and impossible to search. Sava puts them in one place, reads them,
        and lets you ask.
      </p>

      <div className="mt-10 flex flex-wrap items-center gap-4">
        <Link
          href="/auth/register"
          className="rounded-[var(--sava-r-pill)] bg-[var(--sava-citron)] px-7 py-4 text-[16px] font-semibold text-[var(--sava-on-citron)] transition-opacity duration-200 hover:opacity-90"
        >
          Start your library
        </Link>
        <a
          href="#how"
          className="rounded-[var(--sava-r-pill)] border border-[var(--sava-hairline)] px-7 py-4 text-[16px] font-semibold text-[var(--sava-text)] transition-colors duration-200 hover:border-[var(--sava-text-tertiary)]"
        >
          See how it works
        </a>
      </div>
    </section>
  );
}

/* ── Sources ────────────────────────────────────────────────────────────── */

function Sources() {
  return (
    <section className="border-y border-[var(--sava-hairline)]">
      <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-10 gap-y-4 px-6 py-6">
        <span className="text-[12px] font-bold uppercase tracking-[0.12em] text-[var(--sava-text-tertiary)]">
          Saves from
        </span>
        {SOURCES.map((s) => (
          <span key={s} className="text-[15px] text-[var(--sava-text-secondary)]">
            {s}
          </span>
        ))}
      </div>
    </section>
  );
}

/* ── How it works ───────────────────────────────────────────────────────── */

function How() {
  return (
    <section id="how" className="mx-auto max-w-6xl px-6 py-[var(--sava-s-8)]">
      <SectionMark>How it works</SectionMark>
      <h2 className="mt-6 max-w-[20ch] text-[clamp(2rem,5vw,3.5rem)] font-extrabold leading-[1.02] tracking-[-0.04em]">
        Four steps, and you never think about three of them.
      </h2>

      {/* Deliberately asymmetric: a numbered editorial list rather than a row of
          equal feature cards, which is the shape every SaaS page already has. */}
      <ol className="mt-16 grid gap-px overflow-hidden rounded-[var(--sava-r-card)] border border-[var(--sava-hairline)] bg-[var(--sava-hairline)] md:grid-cols-2">
        {STEPS.map((step) => (
          <li key={step.n} className="bg-[var(--sava-ink)] p-8 md:p-10">
            <span className="text-[13px] font-bold tracking-[0.1em] text-[var(--sava-citron)]">
              {step.n}
            </span>
            <h3 className="mt-5 text-[22px] font-bold tracking-[-0.02em]">
              {step.title}
            </h3>
            <p className="mt-3 max-w-[42ch] text-[15.5px] leading-[1.65] text-[var(--sava-text-secondary)]">
              {step.body}
            </p>
          </li>
        ))}
      </ol>
    </section>
  );
}

/* ── Product preview ────────────────────────────────────────────────────── */

function Preview() {
  return (
    <section className="border-t border-[var(--sava-hairline)] bg-[var(--sava-surface)]">
      <div className="mx-auto max-w-6xl px-6 py-[var(--sava-s-8)]">
        <div className="grid items-center gap-16 md:grid-cols-[1.1fr_0.9fr]">
          <div>
            <SectionMark>Ask</SectionMark>
            <h2 className="mt-6 max-w-[16ch] text-[clamp(2rem,5vw,3.5rem)] font-extrabold leading-[1.02] tracking-[-0.04em]">
              Answers, in Sava&rsquo;s own voice.
            </h2>
            <p className="mt-6 max-w-[46ch] text-[17px] leading-[1.65] text-[var(--sava-text-secondary)]">
              Ask across your whole library or inside a single collection. Sava
              answers in plain language, cites the clips it used, and keeps the
              thread — so &ldquo;why?&rdquo; and &ldquo;explain that simpler&rdquo; work the way
              you would expect.
            </p>
            <ul className="mt-8 space-y-3">
              {[
                "Which of these recipes is highest protein?",
                "What did these videos say about Japan?",
                "Compare this to the other one he mentioned.",
              ].map((q) => (
                <li
                  key={q}
                  className="rounded-[var(--sava-r-control)] border border-[var(--sava-hairline)] px-4 py-3 font-[var(--sava-font-serif)] text-[16px] italic text-[var(--sava-text-secondary)]"
                >
                  {q}
                </li>
              ))}
            </ul>
          </div>

          {/* A restrained device frame. Not a photorealistic mockup, not a
              floating perspective render — a plain bezel so the interface
              inside is the thing being looked at. */}
          <PhoneFrame />
        </div>
      </div>
    </section>
  );
}

function PhoneFrame() {
  return (
    <div className="mx-auto w-full max-w-[300px]">
      <div className="rounded-[42px] border border-[var(--sava-hairline)] bg-[var(--sava-ink)] p-3 shadow-[0_40px_80px_-40px_rgba(0,0,0,0.9)]">
        <div className="aspect-[9/19.5] overflow-hidden rounded-[32px] bg-[var(--sava-ink)] p-5">
          <p className="text-[11px] font-bold uppercase tracking-[0.12em] text-[var(--sava-text-tertiary)]">
            Ask · Library
          </p>

          <div className="mt-5 ml-auto w-fit max-w-[85%] rounded-[16px] rounded-br-[4px] bg-[var(--sava-blue)] px-3.5 py-2.5 text-[13px] leading-snug text-white">
            What did I save about sourdough?
          </div>

          <p className="mt-5 font-[var(--sava-font-serif)] text-[14px] leading-[1.55] text-[var(--sava-text)]">
            Three things, and they disagree about hydration. The clearest is
            Claire&rsquo;s — she keeps the starter at 100% and stretches every 30
            minutes for two hours.
          </p>

          <div className="mt-5 flex gap-2">
            <div className="h-16 w-12 rounded-[6px] bg-[var(--sava-fill)]" />
            <div className="h-16 w-12 rounded-[6px] bg-[var(--sava-fill)]" />
            <div className="h-16 w-12 rounded-[6px] bg-[var(--sava-fill)]" />
          </div>

          <div className="mt-6 flex items-center gap-2 rounded-[var(--sava-r-pill)] border border-[var(--sava-hairline)] px-3.5 py-2.5">
            <span className="text-[12.5px] text-[var(--sava-text-tertiary)]">
              Ask a follow-up
            </span>
            <span className="ml-auto h-5 w-5 rounded-full bg-[var(--sava-citron)]" />
          </div>
        </div>
      </div>
    </div>
  );
}

/* ── Collections ────────────────────────────────────────────────────────── */

function Collections() {
  return (
    <section id="collections" className="mx-auto max-w-6xl px-6 py-[var(--sava-s-8)]">
      <SectionMark>Collections</SectionMark>
      <div className="mt-6 grid gap-10 md:grid-cols-[0.9fr_1.1fr] md:items-end">
        <h2 className="max-w-[18ch] text-[clamp(2rem,5vw,3.5rem)] font-extrabold leading-[1.02] tracking-[-0.04em]">
          It organises itself, and you can overrule it.
        </h2>
        <p className="max-w-[46ch] text-[17px] leading-[1.65] text-[var(--sava-text-secondary)]">
          Sava notices what you actually save — a creator you keep returning to,
          a subject you have twelve clips about — and gathers them without being
          asked. Every collection gets a cover chosen to look like what it
          contains. If you disagree, change it; Sava will not change it back.
        </p>
      </div>

      <div className="mt-14 grid grid-cols-2 gap-4 md:grid-cols-4">
        {[
          ["Attack on Titan", "12 items"],
          ["Kai Cenat", "9 items"],
          ["Air Fryer Recipes", "23 items"],
          ["Tokyo", "7 items"],
        ].map(([name, count], i) => (
          <div key={name}>
            <div
              className="aspect-[4/3] rounded-[var(--sava-r-media)] border border-[var(--sava-hairline)]"
              style={{
                background:
                  i === 1
                    ? "var(--sava-fill)"
                    : i === 2
                    ? "color-mix(in srgb, var(--sava-coral) 18%, var(--sava-fill))"
                    : i === 3
                    ? "color-mix(in srgb, var(--sava-blue) 22%, var(--sava-fill))"
                    : "color-mix(in srgb, var(--sava-citron) 12%, var(--sava-fill))",
              }}
            />
            <p className="mt-3 text-[14.5px] font-semibold">{name}</p>
            <p className="mt-0.5 text-[12.5px] text-[var(--sava-text-tertiary)]">
              {count}
            </p>
          </div>
        ))}
      </div>
    </section>
  );
}

/* ── Privacy ────────────────────────────────────────────────────────────── */

function Privacy() {
  return (
    <section
      id="privacy"
      className="border-y border-[var(--sava-hairline)] bg-[var(--sava-surface)]"
    >
      <div className="mx-auto max-w-6xl px-6 py-[var(--sava-s-8)]">
        <SectionMark>Privacy</SectionMark>
        <h2 className="mt-6 max-w-[22ch] text-[clamp(1.75rem,4vw,2.75rem)] font-extrabold leading-[1.05] tracking-[-0.035em]">
          Your library is yours.
        </h2>
        <div className="mt-10 grid gap-8 md:grid-cols-3">
          {[
            [
              "Private by default",
              "Nothing you save is public, shared, or shown to anyone else. There is no feed and no social graph.",
            ],
            [
              "Processed once, shared never",
              "Sava understands a piece of content once and reuses that work. Your library is a set of private references to it.",
            ],
            [
              "Yours to take",
              "Export or delete everything at any time. Deleting means deleted, not hidden.",
            ],
          ].map(([title, body]) => (
            <div key={title}>
              <h3 className="text-[17px] font-bold tracking-[-0.015em]">
                {title}
              </h3>
              <p className="mt-2.5 max-w-[38ch] text-[15px] leading-[1.6] text-[var(--sava-text-secondary)]">
                {body}
              </p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

/* ── Closing CTA ────────────────────────────────────────────────────────── */

function CTA() {
  return (
    <section className="mx-auto max-w-6xl px-6 py-[var(--sava-s-8)] text-center">
      <h2 className="mx-auto max-w-[16ch] text-[clamp(2.25rem,7vw,5rem)] font-extrabold leading-[0.98] tracking-[-0.045em]">
        Stop losing what you meant to keep.
      </h2>
      <div className="mt-10 flex justify-center">
        <Link
          href="/auth/register"
          className="rounded-[var(--sava-r-pill)] bg-[var(--sava-citron)] px-8 py-4 text-[16px] font-semibold text-[var(--sava-on-citron)] transition-opacity duration-200 hover:opacity-90"
        >
          Start your library
        </Link>
      </div>
      <p className="mt-5 text-[13.5px] text-[var(--sava-text-tertiary)]">
        Free while in beta. iPhone.
      </p>
    </section>
  );
}

/* ── Footer ─────────────────────────────────────────────────────────────── */

function Footer() {
  return (
    <footer className="border-t border-[var(--sava-hairline)]">
      <div className="mx-auto flex max-w-6xl flex-col gap-6 px-6 py-10 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-2.5">
          <img src="/sava-tile.png" alt="" width={24} height={24} className="rounded-[6px]" />
          <span className="text-[17px] font-extrabold tracking-[-0.05em]">Sava</span>
        </div>
        <nav className="flex flex-wrap gap-x-8 gap-y-3">
          {["Privacy", "Terms", "Support"].map((l) => (
            <a
              key={l}
              href="#"
              className="text-[13.5px] text-[var(--sava-text-tertiary)] transition-colors duration-200 hover:text-[var(--sava-text)]"
            >
              {l}
            </a>
          ))}
        </nav>
        <span className="text-[13px] text-[var(--sava-text-tertiary)]">
          © {new Date().getFullYear()} Sava
        </span>
      </div>
    </footer>
  );
}

/* ── Shared ─────────────────────────────────────────────────────────────── */

/** The one recurring ornament: a citron rule and a small wide-tracked label.
 *  It is the only thing that repeats, which is what makes the page feel set
 *  rather than assembled. */
function SectionMark({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-3">
      <span className="h-[2px] w-7 bg-[var(--sava-citron)]" />
      <span className="text-[11.5px] font-bold uppercase tracking-[0.14em] text-[var(--sava-text-tertiary)]">
        {children}
      </span>
    </div>
  );
}
