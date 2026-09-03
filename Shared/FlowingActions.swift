import SwiftUI

/// The answers to an agent's request.
///
/// Two shapes, chosen by whether the answers carry their own reasons.
///
/// Plain answers — Allow once, Deny — are bordered pills that wrap when three
/// of them will not fit a row. That is the right control for a short verb.
///
/// A question's options are not short verbs: each is a sentence with a reason
/// under it. Filling those with a tint turns every option into a large
/// coloured slab, which is not how iOS offers a choice. They render instead as
/// plain rows with a divider between them and a chevron to say they act —
/// the same shape a grouped list uses everywhere else on the system. Colour
/// stays on the chevron, where it marks the control without shouting.
///
/// Free-text replies are deliberately not rendered here. Headroom is the
/// attention layer, not a mobile agent client; questions that need words stay
/// available on the Mac where the agent's full context is visible.
struct FlowingActions: View {
    let actions: [AgentAttentionAction]
    let tint: Color
    let disabled: Bool
    let responding: Bool
    var answer: (AgentAttentionAction) -> Void

    /// Choice rows whenever the ledger handed us `choice_*` actions — even
    /// when Claude omitted descriptions. Requiring a subtitle used to drop
    /// label-only options into bordered pills, which wraps badly for real
    /// sentences and looked like the options failed to parse.
    private var isChoiceList: Bool {
        buttons.contains { $0.id.hasPrefix("choice_") }
            || buttons.contains { $0.subtitle?.isEmpty == false }
    }

    private var buttons: [AgentAttentionAction] {
        actions.filter { $0.acceptsText != true }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if isChoiceList {
                choiceList
            } else {
                ViewThatFits(in: .horizontal) {
                    pillRow
                    pillColumn
                }
            }
        }
    }

    private var choiceList: some View {
        VStack(spacing: 0) {
            ForEach(Array(choices.enumerated()), id: \.element.id) { index, action in
                if index > 0 { Divider() }
                choiceRow(action)
            }
            if let aside {
                Divider()
                Button(aside.label) { answer(aside) }
                    .font(.subheadline)
                    .buttonStyle(.plain)
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.vertical, 10)
                    .contentShape(Rectangle())
                    .disabled(disabled)
            }
            if responding {
                Divider()
                spinner.padding(.vertical, 8)
            }
        }
    }

    /// The real answers, and the one that declines to answer. Kept apart so
    /// "Ask on Mac" reads as a way out rather than a fifth option.
    private var choices: [AgentAttentionAction] {
        buttons.filter { $0.id != "ask_on_mac" }
    }

    private var aside: AgentAttentionAction? {
        buttons.first { $0.id == "ask_on_mac" }
    }

    private func choiceRow(_ action: AgentAttentionAction) -> some View {
        Button {
            answer(action)
        } label: {
            HStack(alignment: .firstTextBaseline, spacing: 10) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(action.label)
                        .font(.subheadline.weight(.medium))
                        .foregroundStyle(.primary)
                    if let subtitle = action.subtitle, !subtitle.isEmpty {
                        Text(subtitle)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                Spacer(minLength: 8)
                Image(systemName: "chevron.right")
                    .font(.caption2.weight(.semibold))
                    .foregroundStyle(disabled ? AnyShapeStyle(.tertiary)
                                              : AnyShapeStyle(tint))
            }
            .multilineTextAlignment(.leading)
            .padding(.vertical, 10)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(disabled)
    }

    private var pillRow: some View {
        HStack(spacing: 8) {
            pills
            if responding { spinner }
            Spacer(minLength: 0)
        }
    }

    private var pillColumn: some View {
        VStack(alignment: .leading, spacing: 6) {
            pills
            if responding { spinner }
        }
    }

    @ViewBuilder
    private var pills: some View {
        ForEach(buttons) { action in
            Button(action.label) { answer(action) }
                .buttonStyle(.bordered)
                .tint(color(for: action))
                .disabled(disabled)
        }
    }

    /// Deny is not destructive — it is the safe answer — so it keeps the
    /// accent. Only a genuinely destructive action earns an alarm colour.
    private func color(for action: AgentAttentionAction) -> Color {
        action.risk == "destructive" ? HeadroomPalette.red : tint
    }

    private var spinner: some View {
        ProgressView().controlSize(.small)
    }
}
