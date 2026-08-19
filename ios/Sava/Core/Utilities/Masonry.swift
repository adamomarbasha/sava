import Foundation

/// Distributes items into `columns` balanced columns using a greedy shortest-
/// column heuristic over estimated heights. Keeps a masonry feed visually even
/// without measuring real view sizes.
enum Masonry {
    static func distribute<Item>(_ items: [Item],
                                 columns: Int,
                                 estimatedHeight: (Item) -> CGFloat) -> [[Item]] {
        guard columns > 1 else { return [items] }
        var buckets = Array(repeating: [Item](), count: columns)
        var heights = Array(repeating: CGFloat(0), count: columns)
        for item in items {
            let target = heights.enumerated().min(by: { $0.element < $1.element })?.offset ?? 0
            buckets[target].append(item)
            heights[target] += estimatedHeight(item)
        }
        return buckets
    }
}
