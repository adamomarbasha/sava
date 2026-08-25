// Privacy policy.
//
// App Store Connect will not accept a submission without a reachable privacy
// policy URL, and the App Privacy "nutrition label" must agree with what this
// page says.
//
// Every statement here was derived from the code, not from a template: the data
// map comes from the columns in api/models.py, the third-party flow from
// api/ai/gemini.py, and the deletion semantics from api/services/account.py.
//
// NOT LEGAL ADVICE. This is an accurate factual description written by an
// engineer. Have a lawyer review it before launch, particularly the GDPR and
// CCPA sections.

export const metadata = {
  title: "Privacy — Sava",
  description: "What Sava stores, what it sends onward, and how to delete it.",
};

const UPDATED = "24 August 2026";

export default function PrivacyPage() {
  return (
    <main className="mx-auto max-w-2xl px-6 py-20 text-[15px] leading-relaxed">
      <h1 className="text-4xl font-bold tracking-tight">Privacy</h1>
      <p className="mt-2 text-sm opacity-60">Last updated {UPDATED}</p>

      <p className="mt-8">
        Sava is a personal library. You save links; Sava reads what it can about
        them so you can search and ask questions later. This page says exactly
        what that involves.
      </p>

      <Section title="What Sava stores">
        <ul className="list-disc space-y-2 pl-5">
          <li><b>Your email address and a password hash.</b> The password itself is never stored — only a bcrypt hash, which cannot be reversed.</li>
          <li><b>The links you save</b>, plus the title, creator, thumbnail and duration Sava can determine about them.</li>
          <li><b>What Sava derived</b>: transcripts, summaries, key points and numerical embeddings used for search.</li>
          <li><b>Your collections</b>, including any you named yourself.</li>
          <li><b>Your conversations</b> with Ask, so you can return to them.</li>
          <li><b>Usage records</b> — which operations ran and what they cost — used to enforce fair-use limits.</li>
        </ul>
      </Section>

      <Section title="What Sava does not do">
        <ul className="list-disc space-y-2 pl-5">
          <li>No advertising, and no advertising SDKs.</li>
          <li>No tracking across other apps or websites.</li>
          <li>No selling or sharing your data with data brokers.</li>
          <li>No location, contacts, health data or device identifiers.</li>
          <li>No third-party analytics SDK in the app.</li>
        </ul>
      </Section>

      <Section title="Who else sees your data">
        <p>
          <b>Google (Gemini).</b> To summarise a save and make it searchable,
          its text — the transcript, description and title — is sent to Google&rsquo;s
          Gemini API. This is the one place your saved content leaves Sava&rsquo;s
          own systems. It is not used to train Google&rsquo;s models under the paid
          API terms.
        </p>
        <p className="mt-4">
          <b>The platforms you save from.</b> Fetching a thumbnail or playing a
          video contacts that platform&rsquo;s servers, exactly as opening the link
          would.
        </p>
        <p className="mt-4">
          <b>Our infrastructure providers.</b> A hosting provider, a managed
          PostgreSQL database and an S3-compatible object store hold this data on
          our behalf. They do not use it for anything else.
        </p>
        <p className="mt-4">
          <b>Error reporting.</b> If a crash report is enabled, it carries the
          error and the code path — not your saved content or your questions.
        </p>
      </Section>

      <Section title="Deleting your account">
        <p>
          Settings → Profile → <b>Delete account</b>. It is immediate and
          irreversible: your saves, collections, conversations, usage records and
          login are removed.
        </p>
        <p className="mt-4">
          One thing worth being precise about. When two people save the same
          video, Sava analyses it once and shares the result — that is why it is
          fast and affordable. Deleting your account removes <i>your</i> link to
          that video and everything private to you. The shared analysis is
          deleted too, unless somebody else still has that video saved, in which
          case it stays in <i>their</i> library. Nothing of yours is kept.
        </p>
      </Section>

      <Section title="Getting your data out">
        <p>
          Settings → Profile → <b>Export my data</b> produces a JSON file
          containing your account details, saves, collections and conversations.
        </p>
      </Section>

      <Section title="Your rights">
        <p>
          If you are in the EU/EEA or UK, you have the right to access, correct,
          export and erase your data, and to object to processing. If you are in
          California, you have the right to know what is collected, to delete it,
          and not to be discriminated against for asking. Export and deletion are
          both available in the app; for anything else, contact us.
        </p>
      </Section>

      <Section title="Children">
        <p>
          Sava is not directed at children under 13, and we do not knowingly
          collect their data.
        </p>
      </Section>

      <Section title="Contact">
        <p>
          Questions about any of this: <a className="underline" href="/support">Support</a>.
        </p>
      </Section>
    </main>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mt-10">
      <h2 className="text-xl font-semibold tracking-tight">{title}</h2>
      <div className="mt-3 space-y-2 opacity-90">{children}</div>
    </section>
  );
}
