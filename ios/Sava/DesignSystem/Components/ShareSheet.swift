import SwiftUI
import UIKit

/// The system share sheet.
///
/// Used for the data export: a JSON file is something people send to themselves
/// or save to Files, not something to read on a phone screen. Handing it to
/// `UIActivityViewController` means AirDrop, Mail, Files and every share target
/// the user already has, for no code.
struct ShareSheet: UIViewControllerRepresentable {
    let items: [Any]

    func makeUIViewController(context: Context) -> UIActivityViewController {
        UIActivityViewController(activityItems: items, applicationActivities: nil)
    }

    func updateUIViewController(_ controller: UIActivityViewController,
                                context: Context) {}
}

/// A file to share, wrapped so it can drive `.sheet(item:)`.
///
/// `URL` is not `Identifiable`, and adding that conformance to a standard-library
/// type from application code is a retroactive conformance — it would break if
/// the standard library ever added its own. A two-line wrapper avoids the whole
/// question.
struct SharePayload: Identifiable {
    let url: URL
    var id: String { url.absoluteString }
}
