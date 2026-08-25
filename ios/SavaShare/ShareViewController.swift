import UIKit
import Social
import UniformTypeIdentifiers

/// The share sheet entry point.
///
/// Sava's premise is "save the thing you are looking at". Before this existed
/// that meant leaving TikTok, copying the link, opening Sava, tapping add and
/// pasting — six steps, five of them after the moment of intent has passed.
/// This is two taps.
///
/// The design constraint that shapes everything here: **an extension has
/// seconds, not minutes.** iOS gives it a small memory budget and terminates it
/// shortly after `completeRequest`. So this does the durable thing first — write
/// the URL to the shared queue — and only then attempts the network with a short
/// budget. If the upload succeeds, good; if it does not, the app picks it up on
/// next launch. Either way the user is already back in TikTok.
///
/// Deliberately not a `SLComposeServiceViewController` subclass with a text box:
/// there is nothing to compose. The fastest correct share sheet is one that
/// dismisses itself.
final class ShareViewController: UIViewController {

    /// How long to wait for the network before giving up and letting the app
    /// finish the job. Short on purpose — the extension is not where a slow
    /// upload should be waited on.
    private let networkBudget: TimeInterval = 4

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = .clear
        Task { await handleShare() }
    }

    private func handleShare() async {
        guard let url = await extractURL() else {
            await finish(message: "Nothing to save")
            return
        }

        // 1. Durable first. This is the part that must not fail.
        let pending = PendingSave(url: url.absoluteString)
        let queued = PendingSaveQueue.append(pending)

        // 2. Optimistic confirmation. The user is told it is saved because it
        //    is — it is in the queue, and the queue is drained by the app.
        await showConfirmation()

        // 3. Best-effort upload inside the budget.
        if queued {
            await withTimeout(networkBudget) {
                await self.uploadNow(pending)
            }
        }

        await finish(message: nil)
    }

    /// Pull a URL out of whatever the host app handed us.
    ///
    /// Shares arrive in inconsistent shapes: Safari sends a `public.url`,
    /// several apps send only `public.plain-text` containing a URL, and some
    /// send both. Checking text as a fallback is what makes this work from apps
    /// that do not advertise a URL type.
    private func extractURL() async -> URL? {
        guard let items = extensionContext?.inputItems as? [NSExtensionItem] else {
            return nil
        }
        for item in items {
            for provider in item.attachments ?? [] {
                if provider.hasItemConformingToTypeIdentifier(UTType.url.identifier),
                   let url = try? await provider.loadItem(
                    forTypeIdentifier: UTType.url.identifier) as? URL {
                    return url
                }
            }
        }
        // Fallback: a plain-text attachment that happens to contain a link.
        for item in items {
            for provider in item.attachments ?? [] {
                guard provider.hasItemConformingToTypeIdentifier(
                    UTType.plainText.identifier) else { continue }
                let text = try? await provider.loadItem(
                    forTypeIdentifier: UTType.plainText.identifier) as? String
                if let text, let found = firstURL(in: text) { return found }
            }
        }
        return nil
    }

    private func firstURL(in text: String) -> URL? {
        let detector = try? NSDataDetector(types: NSTextCheckingResult.CheckingType.link.rawValue)
        let range = NSRange(text.startIndex..., in: text)
        return detector?.firstMatch(in: text, range: range)?.url
    }

    /// Post the save directly, reusing the app's own networking configuration.
    private func uploadNow(_ pending: PendingSave) async {
        guard let token = KeychainStore().read(.authToken) else { return }
        // The origin the *app* resolved, published to the shared container on
        // its last launch. The extension has its own bundle and cannot read the
        // app's Info.plist, so this is the only value guaranteed to match what
        // the app itself is talking to — including the LAN address stamped into
        // Debug builds at compile time.
        guard let base = AppConfig.sharedOrigin else { return }

        var request = URLRequest(url: base.appendingPathComponent("bookmarks"))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.httpBody = try? JSONSerialization.data(
            withJSONObject: ["url": pending.url])
        request.timeoutInterval = networkBudget

        guard let (_, response) = try? await URLSession.shared.data(for: request),
              let http = response as? HTTPURLResponse else { return }

        // 2xx means saved. 409 means it was already there, which is also fine.
        if (200...299).contains(http.statusCode) || http.statusCode == 409 {
            PendingSaveQueue.remove(pending.id)
        }
    }

    // MARK: UI

    /// A single confirmation, then out. No form, no editing, no spinner that
    /// outlives its usefulness.
    private func showConfirmation() async {
        await MainActor.run {
            let label = UILabel()
            label.text = "Saved to Sava"
            label.font = .systemFont(ofSize: 17, weight: .semibold)
            label.textColor = .white
            label.textAlignment = .center
            label.translatesAutoresizingMaskIntoConstraints = false

            let card = UIView()
            card.backgroundColor = UIColor(white: 0.06, alpha: 0.97)
            card.layer.cornerRadius = 18
            card.translatesAutoresizingMaskIntoConstraints = false
            card.addSubview(label)
            view.addSubview(card)

            NSLayoutConstraint.activate([
                card.centerXAnchor.constraint(equalTo: view.centerXAnchor),
                card.centerYAnchor.constraint(equalTo: view.centerYAnchor),
                card.widthAnchor.constraint(equalToConstant: 220),
                card.heightAnchor.constraint(equalToConstant: 84),
                label.centerXAnchor.constraint(equalTo: card.centerXAnchor),
                label.centerYAnchor.constraint(equalTo: card.centerYAnchor),
            ])
        }
        try? await Task.sleep(nanoseconds: 450_000_000)
    }

    private func finish(message: String?) async {
        await MainActor.run {
            extensionContext?.completeRequest(returningItems: nil)
        }
    }

    /// Run `work`, abandoning it if it exceeds `seconds`.
    ///
    /// The extension must dismiss whether or not the network cooperates, and
    /// `URLRequest.timeoutInterval` alone does not cover DNS stalls or a
    /// captive portal.
    private func withTimeout(_ seconds: TimeInterval,
                             _ work: @escaping () async -> Void) async {
        await withTaskGroup(of: Void.self) { group in
            group.addTask { await work() }
            group.addTask {
                try? await Task.sleep(nanoseconds: UInt64(seconds * 1_000_000_000))
            }
            await group.next()
            group.cancelAll()
        }
    }
}
