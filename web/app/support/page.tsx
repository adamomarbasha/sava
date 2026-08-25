// Support page.
//
// App Store Connect requires a reachable support URL. A page that exists but
// says nothing is worse than none: reviewers check it, and so do users when
// something breaks.
//
// The contact address is a placeholder and must be replaced with a real,
// monitored mailbox before submission.

export const metadata = {
  title: "Support — Sava",
  description: "Help with Sava: saving, processing, accounts and data.",
};

// TODO(before submission): replace with a real monitored address.
const CONTACT = "support@sava.app";

export default function SupportPage() {
  return (
    <main className="mx-auto max-w-2xl px-6 py-20 text-[15px] leading-relaxed">
      <h1 className="text-4xl font-bold tracking-tight">Support</h1>
      <p className="mt-4 opacity-90">
        Something not working, or a question about your account? Email{" "}
        <a className="underline" href={`mailto:${CONTACT}`}>{CONTACT}</a>. Include
        what you were doing and roughly when — that is usually enough to find it
        in the logs.
      </p>

      <Section title="A save is stuck on “processing”">
        <p>
          Saving is instant; understanding it happens in the background and
          usually takes under a minute. Longer videos take longer. If something
          has been processing for more than an hour, tell us the link and we will
          look at the job.
        </p>
      </Section>

      <Section title="A video won’t play">
        <p>
          Sava plays what each platform allows. YouTube and Instagram play
          through their official embedded players. Some posts are restricted by
          their platform or their author and can only be opened in the original
          app — Sava will say so rather than spin.
        </p>
      </Section>

      <Section title="Summaries or search look wrong">
        <p>
          Summaries come from what Sava could read — captions, description, and
          for some videos the audio. When there is little text to work from, the
          result is thin. Open a save and use Reprocess to try again.
        </p>
      </Section>

      <Section title="I’ve hit a limit">
        <p>
          There are daily fair-use limits on saving, asking and reprocessing so
          that one runaway loop cannot exhaust the service. They are far above
          normal use and reset on a rolling 24-hour basis. If you are hitting
          them in ordinary use, we want to know — that is a bug in our numbers.
        </p>
      </Section>

      <Section title="Deleting your account or exporting your data">
        <p>
          Both are in the app: Profile → <b>Export my data</b> or{" "}
          <b>Delete account</b>. Deletion is immediate and cannot be undone. See{" "}
          <a className="underline" href="/privacy">Privacy</a> for exactly what is
          removed.
        </p>
      </Section>

      <Section title="Reporting a security issue">
        <p>
          Email {CONTACT} with “security” in the subject. Please give us a
          reasonable window to fix it before disclosing publicly.
        </p>
      </Section>
    </main>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mt-10">
      <h2 className="text-xl font-semibold tracking-tight">{title}</h2>
      <div className="mt-3 opacity-90">{children}</div>
    </section>
  );
}
