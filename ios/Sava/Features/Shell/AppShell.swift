import SwiftUI

/// The signed-in container: a custom glass tab bar over Library, Search, and
/// Profile, with a prominent center Save action. The library model is shared so
/// a quick save appears in the feed immediately.
struct AppShell: View {
    let user: User
    @EnvironmentObject private var session: SessionStore
    @StateObject private var library = LibraryViewModel()

    @State private var tab: SavaTab = .library
    @State private var showSave = false
    @State private var libraryPath: [Bookmark] = []

    var body: some View {
        ZStack(alignment: .bottom) {
            tabContent
                .environmentObject(library)

            SavaTabBar(selection: $tab) {
                Haptics.tap()
                showSave = true
            }
        }
        .ignoresSafeArea(.keyboard)
        .sheet(isPresented: $showSave) {
            QuickSaveSheet { bookmark in
                library.insertSaved(bookmark)
                withAnimation(SavaMotion.standard) { tab = .library }
            }
            .environmentObject(session)
        }
        .onAppear(perform: applyDevFlags)
        .onReceive(library.$all) { items in
            guard DevFlags.openFirst, libraryPath.isEmpty, let first = items.first else { return }
            libraryPath = [first]
        }
    }

    @ViewBuilder private var tabContent: some View {
        switch tab {
        case .library:
            NavigationStack(path: $libraryPath) { LibraryView().bookmarkDestination() }
        case .search:
            NavigationStack { SearchView().bookmarkDestination() }
        case .profile:
            NavigationStack { ProfileView(user: user).bookmarkDestination() }
        }
    }

    private func applyDevFlags() {
        switch DevFlags.initialTab {
        case "search": tab = .search
        case "profile": tab = .profile
        default: break
        }
    }
}

enum SavaTab: CaseIterable {
    case library, search, profile

    var title: String {
        switch self {
        case .library: return "Library"
        case .search: return "Search"
        case .profile: return "Profile"
        }
    }
    var icon: String {
        switch self {
        case .library: return "square.grid.2x2"
        case .search: return "magnifyingglass"
        case .profile: return "person"
        }
    }
    var iconSelected: String {
        switch self {
        case .library: return "square.grid.2x2.fill"
        case .search: return "magnifyingglass"
        case .profile: return "person.fill"
        }
    }
}

/// Shared navigation destination so every stack opens bookmark details.
extension View {
    func bookmarkDestination() -> some View {
        navigationDestination(for: Bookmark.self) { bookmark in
            BookmarkDetailView(bookmark: bookmark)
        }
    }
}

/// A floating glass tab bar with a central Save button.
struct SavaTabBar: View {
    @Binding var selection: SavaTab
    let onSave: () -> Void

    var body: some View {
        HStack(spacing: 0) {
            tabButton(.library)
            tabButton(.search)
            saveButton
            tabButton(.profile)
        }
        .padding(.horizontal, Spacing.sm)
        .padding(.vertical, Spacing.xs)
        .background(
            Capsule(style: .continuous)
                .fill(.ultraThinMaterial)
                .overlay(Capsule().strokeBorder(SavaColors.hairline, lineWidth: 1))
                .shadow(color: .black.opacity(0.12), radius: 18, y: 8)
        )
        .padding(.horizontal, Spacing.xxl)
        .padding(.bottom, Spacing.xs)
    }

    private func tabButton(_ tab: SavaTab) -> some View {
        let isSelected = selection == tab
        return Button {
            guard selection != tab else { return }
            Haptics.selection()
            withAnimation(SavaMotion.tap) { selection = tab }
        } label: {
            VStack(spacing: 3) {
                Image(systemName: isSelected ? tab.iconSelected : tab.icon)
                    .font(.system(size: 20, weight: .semibold))
                    .symbolVariant(.none)
                Text(tab.title)
                    .font(.system(size: 10, weight: .semibold))
            }
            .foregroundStyle(isSelected ? SavaColors.textPrimary : SavaColors.textTertiary)
            .frame(maxWidth: .infinity)
            .frame(height: 46)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(tab.title)
        .accessibilityAddTraits(isSelected ? [.isSelected, .isButton] : .isButton)
    }

    private var saveButton: some View {
        Button(action: onSave) {
            Image(systemName: "plus")
                .font(.system(size: 22, weight: .bold))
                .foregroundStyle(SavaColors.background)
                .frame(width: 52, height: 52)
                .background(Circle().fill(SavaColors.accent))
                .shadow(color: SavaColors.accent.opacity(0.4), radius: 10, y: 4)
        }
        .buttonStyle(.pressable)
        .padding(.horizontal, Spacing.xs)
        .accessibilityLabel("Save a link")
    }
}
