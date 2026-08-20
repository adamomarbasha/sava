import SwiftUI

/// A tactile button style: subtle scale + brightness dip on press, with an
/// optional light haptic. Used for secondary and icon buttons.
struct PressableStyle: ButtonStyle {
    /// Deliberately shallow. A card that shrinks by 4% under a finger reads as a
    /// toy; 2% reads as the surface giving slightly, which is what a physical
    /// control does. The spring is fast enough to keep up with a quick tap and
    /// soft enough not to wobble on release.
    var scale: CGFloat = 0.978
    var haptic: Bool = true

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .scaleEffect(configuration.isPressed ? scale : 1, anchor: .center)
            .opacity(configuration.isPressed ? 0.88 : 1)
            .animation(.spring(response: 0.22, dampingFraction: 0.86),
                       value: configuration.isPressed)
            .onChange(of: configuration.isPressed) { _, pressed in
                if pressed && haptic { Haptics.tap() }
            }
    }
}

extension ButtonStyle where Self == PressableStyle {
    static var pressable: PressableStyle { PressableStyle() }
}
