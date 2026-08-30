import SwiftUI

/// Stage 1 — the constellation of things you already care about.
///
/// ── What it is trying to say ────────────────────────────────────────────
///
/// "The internet you scroll past → your memory." So the cards drift the way a
/// feed does, and then you can take one and put it into Sava, and it stays.
/// The gesture *is* the pitch: drag a card down onto the Sava mark and it locks
/// in with a Saved chip on it. Nobody has to read a sentence explaining that.
///
/// ── Why it is not a particle field ──────────────────────────────────────
///
/// Every object on screen is a real card from `DemoLibrary` with real artwork,
/// a platform, a creator and a duration. Decorative blobs would have been much
/// less work and would have communicated nothing — the point is that these look
/// like *things worth keeping*, because that is the feeling Sava is selling.
///
/// ── Performance ─────────────────────────────────────────────────────────
///
/// One `TimelineView(.animation)` drives every card from a single clock, at a
/// capped frame interval, and SwiftUI stops it when the view goes away — so
/// there is no `repeatForever` animation left running behind Stage 4. Under
/// Reduce Motion the timeline is not created at all and the cards are laid out
/// in their rest positions, which is a composition that reads correctly on its
/// own rather than a frozen frame of an animation.
struct ContentUniverse: View {
    /// Cards the user has dragged onto the mark.
    @Binding var savedIDs: Set<String>
    var onSave: () -> Void = {}

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var dragged: String?
    @State private var dragOffset: CGSize = .zero
    @State private var appeared = false

    /// Rest positions, in fractions of the field.
    ///
    /// Two rows of three, hand-placed, and deliberately **not overlapping**.
    ///
    /// The first attempt scattered seven cards freely and looked wonderful in
    /// the abstract: on a phone the lower row covered the upper row's titles —
    /// a card's title sits along its bottom edge, so any vertical overlap eats
    /// exactly the part that carries the meaning — and the Sava target landed
    /// on top of a third card. Rows are now spaced by more than a card's
    /// height, and the bottom fifth of the field is reserved for the target.
    ///
    /// Six cards rather than the full seven for the same reason: the seventh
    /// had nowhere to go that was not on top of something.
    private static let slots: [(x: CGFloat, y: CGFloat, scale: CGFloat, tilt: Double)] = [
        (0.19, 0.17, 0.96, -6),
        (0.50, 0.14, 1.00,  3),
        (0.81, 0.19, 0.92, -3),
        (0.19, 0.52, 0.90,  5),
        (0.50, 0.55, 0.98, -2),
        (0.81, 0.51, 0.94,  7),
    ]

    private var items: [DemoItem] { Array(DemoLibrary.all.prefix(Self.slots.count)) }

    var body: some View {
        GeometryReader { geo in
            let size = geo.size
            ZStack {
                // Each layer is pinned to the field's own size.
                //
                // `.position` resolves against its *parent's* bounds, and a
                // `TimelineView` sizes itself to its content rather than to the
                // enclosing `GeometryReader`. Without these explicit frames the
                // animated path measured against a different rectangle from the
                // Reduce Motion path, and the cards fanned out diagonally down
                // the screen instead of sitting in two rows.
                if reduceMotion {
                    ZStack { cards(in: size, phase: 0) }
                        .frame(width: size.width, height: size.height)
                } else {
                    TimelineView(.animation(minimumInterval: 1.0 / 30.0)) { timeline in
                        let t = timeline.date.timeIntervalSinceReferenceDate
                        ZStack { cards(in: size, phase: t) }
                            .frame(width: size.width, height: size.height)
                    }
                    .frame(width: size.width, height: size.height)
                }
                savingTarget(in: size)
            }
            .frame(width: size.width, height: size.height)
            .onAppear {
                withAnimation(Motion.respecting(.easeOut(duration: 0.7), reduceMotion)) {
                    appeared = true
                }
            }
        }
    }

    // MARK: The drifting cards

    @ViewBuilder
    private func cards(in size: CGSize, phase: TimeInterval) -> some View {
        ForEach(Array(items.enumerated()), id: \.element.id) { index, item in
            let slot = Self.slots[index % Self.slots.count]
            let drift = self.drift(index: index, phase: phase)
            let isDragging = dragged == item.id
            let width = min(size.width * 0.285, 116) * slot.scale

            DemoCard(item: item, width: width, isSaved: savedIDs.contains(item.id))
                .rotationEffect(.degrees(slot.tilt + drift.tilt))
                .scaleEffect(isDragging ? 1.10 : 1)
                .position(x: slot.x * size.width + drift.x
                             + (isDragging ? dragOffset.width : 0),
                          y: slot.y * size.height + drift.y
                             + (isDragging ? dragOffset.height : 0))
                .zIndex(isDragging ? 10 : Double(index))
                .opacity(appeared ? 1 : 0)
                .animation(Motion.respecting(Motion.standard, reduceMotion)
                    .delay(Double(index) * 0.055), value: appeared)
                // High priority, or the enclosing ScrollView claims the pan
                // and the cards become undraggable on a screen tall enough to
                // scroll — which is every screen at large Dynamic Type.
                .highPriorityGesture(dragGesture(for: item, in: size))
                .onTapGesture { nudge(item) }
                .accessibilityAddTraits(.isButton)
                .accessibilityHint(savedIDs.contains(item.id)
                                   ? "Saved" : "Double tap to save to Sava")
                .accessibilityAction { save(item) }
        }
    }

    /// A slow figure-of-eight per card, out of phase with its neighbours.
    ///
    /// Two sines at different periods rather than one: a single sine reads as a
    /// mechanical bounce, and everything moving on the same period reads as the
    /// whole screen sliding rather than as individual objects floating.
    private func drift(index: Int, phase: TimeInterval) -> (x: CGFloat, y: CGFloat, tilt: Double) {
        guard !reduceMotion else { return (0, 0, 0) }
        let seed = Double(index) * 1.7
        let slow = phase * 0.16 + seed
        let slower = phase * 0.11 + seed * 0.6
        return (x: CGFloat(sin(slow) * 6),
                y: CGFloat(cos(slower) * 7),
                tilt: sin(slow * 0.7) * 1.4)
    }

    // MARK: Dragging one into Sava

    private func dragGesture(for item: DemoItem, in size: CGSize) -> some Gesture {
        DragGesture()
            .onChanged { value in
                if dragged != item.id { dragged = item.id; Haptics.tap() }
                dragOffset = value.translation
            }
            .onEnded { value in
                let slot = Self.slots[(items.firstIndex(of: item) ?? 0) % Self.slots.count]
                let endY = slot.y * size.height + value.translation.height
                // Dropped in the lower band, where the mark sits.
                if endY > size.height * 0.74 {
                    save(item)
                }
                withAnimation(Motion.respecting(Motion.standard, reduceMotion)) {
                    dragged = nil
                    dragOffset = .zero
                }
            }
    }

    private func save(_ item: DemoItem) {
        guard !savedIDs.contains(item.id) else { return }
        Haptics.success()
        withAnimation(Motion.respecting(Motion.standard, reduceMotion)) {
            _ = savedIDs.insert(item.id)
        }
        onSave()
    }

    /// A tap is a smaller promise than a drag, so it saves too — discovering
    /// the drag is a reward, not a requirement. Someone using VoiceOver or with
    /// limited motor control gets to the same place by the same control.
    private func nudge(_ item: DemoItem) { save(item) }

    // MARK: The target

    private func savingTarget(in size: CGSize) -> some View {
        VStack(spacing: 6) {
            ZStack {
                Circle()
                    .fill(SavaColor.accent.opacity(dragged != nil ? 0.22 : 0.10))
                    .frame(width: dragged != nil ? 74 : 62,
                           height: dragged != nil ? 74 : 62)
                Circle()
                    .strokeBorder(SavaColor.accent.opacity(dragged != nil ? 0.9 : 0.35),
                                  style: .init(lineWidth: 1.5,
                                               dash: dragged != nil ? [] : [4, 4]))
                    .frame(width: 62, height: 62)
                SavaMark(size: 26)
            }
            Text(savedIDs.isEmpty ? "Drag something in" : savedCountLabel)
                .font(SavaType.meta)
                .foregroundStyle(savedIDs.isEmpty ? SavaColor.tertiary : SavaColor.accent)
                .contentTransition(.opacity)
        }
        .animation(Motion.respecting(Motion.tap, reduceMotion), value: dragged)
        .animation(Motion.respecting(Motion.standard, reduceMotion), value: savedIDs)
        .position(x: size.width / 2, y: size.height * 0.89)
        .allowsHitTesting(false)
        .accessibilityHidden(true)
    }

    private var savedCountLabel: String {
        savedIDs.count == 1 ? "1 saved" : "\(savedIDs.count) saved"
    }
}
