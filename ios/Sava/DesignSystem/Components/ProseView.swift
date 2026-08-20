import SwiftUI

/// Renders an answer as reading text.
///
/// The intelligence layer replies in Markdown — `**bold**`, `* ` bullets, the
/// occasional `###` lead-in, and `[1]` markers pointing back at the saves it
/// used. Dropping that into a plain `Text` puts raw asterisks on screen, which
/// is the single most recognisable "this is a chatbot" tell there is.
///
/// So the answer is parsed into the same three shapes the rest of the app
/// already sets: a lead line, a paragraph, and a bullet. Inline emphasis is
/// resolved through `AttributedString`, and citation markers become superscript
/// numerals that match the numbered media above the prose.
struct ProseView: View {
    let text: String
    /// Font for paragraph text. Bullets always step down to `callout`.
    var font: Font = SavaType.prose

    var body: some View {
        VStack(alignment: .leading, spacing: Space.m) {
            ForEach(Array(blocks.enumerated()), id: \.offset) { _, block in
                switch block {
                case .paragraph(let value):
                    Text(Self.styled(value))
                        .font(font)
                        .foregroundStyle(SavaColor.primary)
                        .lineSpacing(4)
                        .fixedSize(horizontal: false, vertical: true)

                case .lead(let value):
                    Text(Self.styled(value))
                        .font(SavaType.mediaTitle)
                        .foregroundStyle(SavaColor.primary)
                        .fixedSize(horizontal: false, vertical: true)

                case .bullets(let items):
                    VStack(alignment: .leading, spacing: Space.s) {
                        ForEach(Array(items.enumerated()), id: \.offset) { _, item in
                            HStack(alignment: .firstTextBaseline, spacing: Space.m) {
                                Circle()
                                    .fill(SavaColor.tertiary)
                                    .frame(width: 3, height: 3)
                                    .offset(y: -4)
                                Text(Self.styled(item))
                                    .font(SavaType.callout)
                                    .foregroundStyle(SavaColor.primary)
                                    .fixedSize(horizontal: false, vertical: true)
                            }
                        }
                    }
                }
            }
        }
        .textSelection(.enabled)
    }

    // MARK: Parsing

    private enum Block {
        case paragraph(String)
        case lead(String)
        case bullets([String])
    }

    private var blocks: [Block] {
        var out: [Block] = []
        var paragraph: [String] = []
        var bullets: [String] = []

        func flushParagraph() {
            guard !paragraph.isEmpty else { return }
            out.append(.paragraph(paragraph.joined(separator: " ")))
            paragraph.removeAll()
        }
        func flushBullets() {
            guard !bullets.isEmpty else { return }
            out.append(.bullets(bullets))
            bullets.removeAll()
        }

        for rawLine in text.components(separatedBy: .newlines) {
            let line = rawLine.trimmingCharacters(in: .whitespaces)

            if line.isEmpty {
                flushBullets(); flushParagraph(); continue
            }
            if let bullet = Self.bulletBody(line) {
                flushParagraph()
                bullets.append(bullet)
                continue
            }
            if line.hasPrefix("#") {
                flushBullets(); flushParagraph()
                out.append(.lead(line.drop { $0 == "#" }.trimmingCharacters(in: .whitespaces)))
                continue
            }
            flushBullets()
            paragraph.append(line)
        }
        flushBullets(); flushParagraph()
        return out
    }

    /// The text of a bullet, or nil if the line is not one. Handles `-`, `*`,
    /// `•` and `1.` list markers.
    private static func bulletBody(_ line: String) -> String? {
        for marker in ["- ", "* ", "• "] where line.hasPrefix(marker) {
            return String(line.dropFirst(marker.count))
        }
        // "1. " through "99. "
        if let dot = line.firstIndex(of: "."),
           line.distance(from: line.startIndex, to: dot) <= 2,
           line[line.startIndex..<dot].allSatisfy(\.isNumber),
           line.index(after: dot) < line.endIndex,
           line[line.index(after: dot)] == " " {
            return String(line[line.index(dot, offsetBy: 2)...])
        }
        return nil
    }

    /// Inline emphasis, with `[1]` citation markers set as superscript numerals
    /// matching the numbered saves shown above the prose.
    private static func styled(_ line: String) -> AttributedString {
        let cleaned = superscriptCitations(in: line)
        guard let attributed = try? AttributedString(
            markdown: cleaned,
            options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace))
        else { return AttributedString(cleaned) }
        return attributed
    }

    private static let superscripts: [Character: Character] = [
        "0": "\u{2070}", "1": "\u{00B9}", "2": "\u{00B2}", "3": "\u{00B3}", "4": "\u{2074}",
        "5": "\u{2075}", "6": "\u{2076}", "7": "\u{2077}", "8": "\u{2078}", "9": "\u{2079}",
    ]

    private static func superscriptCitations(in line: String) -> String {
        guard let regex = try? NSRegularExpression(pattern: "\\[(\\d{1,2})\\]")
        else { return line }

        var result = line
        let matches = regex.matches(in: line, range: NSRange(line.startIndex..., in: line))
        for match in matches.reversed() {
            guard let full = Range(match.range, in: result),
                  let digits = Range(match.range(at: 1), in: result) else { continue }
            let raised = String(result[digits].compactMap { superscripts[$0] })
            result.replaceSubrange(full, with: raised)
        }
        return result
    }
}
