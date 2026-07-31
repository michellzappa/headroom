import SwiftUI

struct DragReorder: ViewModifier {
    let enabled: Bool
    let id: String
    let onTargeted: (Bool) -> Void
    let onDrop: (String) -> Void

    func body(content: Content) -> some View {
        if enabled {
            content
                .draggable(id) {
                    // Dragging the row itself would drag the live toggle.
                    Label(id.capitalized, systemImage: "line.3.horizontal")
                        .padding(6)
                }
                .dropDestination(for: String.self) { items, _ in
                    guard let dragged = items.first else { return false }
                    onDrop(dragged)
                    return true
                } isTargeted: { targeted in
                    onTargeted(targeted)
                }
        } else {
            content
        }
    }
}
