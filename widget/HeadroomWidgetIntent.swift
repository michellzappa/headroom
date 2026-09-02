import AppIntents
import WidgetKit

// What "Edit Widget" opens, and the one question it asks.
//
// The Providers pane decides which providers exist and in what order, and the
// host serves the top 3 of that order — one answer, for the whole product.
// This is a different question: which of those a *tile* spends its space on.
// Two tiles on the same screen answer it differently, which is exactly what
// makes it a preference rather than another setting in the app
// (docs/product.md, "What earns a Setting"). The app keeps deciding what is
// worth tracking; the tile decides what it has room for.
//
// Strings are literals here rather than `HeadroomCopy` constants: the App
// Intents metadata extractor reads them out of the source at build time to
// build the picker, so it has to see the words themselves. `gallerySubtitle`
// in HeadroomWidget.swift is a literal for the same reason.

/// One provider in the picker, or the default that draws all of them.
struct HeadroomProviderEntity: AppEntity {
    /// The provider id from the cache — `claude`, `codex` — so a tile keeps
    /// pointing at the same provider across renames and reorders.
    let id: String
    let name: String

    /// Not a provider id, and it cannot collide with one: the host's ids are
    /// registry keys and none of them is this word.
    static let everyProviderID = "all"

    static let everyProvider = HeadroomProviderEntity(
        id: everyProviderID, name: "All providers"
    )

    static let typeDisplayRepresentation: TypeDisplayRepresentation = "Provider"
    static let defaultQuery = HeadroomProviderQuery()

    var displayRepresentation: DisplayRepresentation {
        DisplayRepresentation(title: "\(name)")
    }
}

/// The picker's list, read from the same cache the widget draws.
///
/// The extension has no model layer and cannot ask the host, so the choices
/// are whatever the last sync left behind. Before the first sync that is the
/// default alone, which is the honest list: there are no providers to name yet.
struct HeadroomProviderQuery: EntityQuery {
    func suggestedEntities() async throws -> [HeadroomProviderEntity] {
        Self.options()
    }

    /// A new tile starts on the provider closest to running out — the same
    /// one every compact surface leads with, because it is the one that
    /// changes what you do next. All three at once is a chart, and a chart is
    /// something to choose rather than something to land on.
    ///
    /// This is resolved once, when the widget is added, and stored with it. A
    /// tile does not quietly change which provider it draws because a
    /// different one started emptying faster — that would make a widget
    /// someone placed on purpose into a widget that wanders.
    func defaultResult() async -> HeadroomProviderEntity? {
        guard let binding = HeadroomWidgetSnapshot.cached()?.bindingProvider
        else { return .everyProvider }
        return HeadroomProviderEntity(id: binding.id, name: binding.spokenTitle)
    }

    func entities(
        for identifiers: [String]
    ) async throws -> [HeadroomProviderEntity] {
        let known = Self.options()
        return identifiers.map { id in
            // A provider the cache cannot name right now — turned off, or
            // simply not synced yet — keeps its row rather than resolving to
            // nothing. Dropping it here would clear a tile someone configured
            // on purpose, and the setting would not come back when the
            // provider did.
            known.first { $0.id == id }
                ?? HeadroomProviderEntity(id: id, name: id)
        }
    }

    private static func options() -> [HeadroomProviderEntity] {
        let cached = HeadroomWidgetSnapshot.cached()?.providers ?? []
        return [.everyProvider] + cached.map {
            // The full title, because this list has no mark beside it to say
            // which provider a bare "Work" belongs to.
            HeadroomProviderEntity(id: $0.id, name: $0.spokenTitle)
        }
    }
}

/// The tile's configuration: which provider, and nothing else.
///
/// Everything else the widget draws — rings against chart, what counts as
/// stale, how many providers fit — is a judgment the product already made, and
/// a second place to make it would be a second place for it to drift.
struct HeadroomWidgetConfiguration: WidgetConfigurationIntent {
    static let title: LocalizedStringResource = "Headroom widget"
    static let description = IntentDescription(
        "Choose which provider this widget shows."
    )

    @Parameter(title: "Provider")
    var provider: HeadroomProviderEntity?

    init() {}

    init(provider: HeadroomProviderEntity?) {
        self.provider = provider
    }

    /// Nil when the tile draws everything it is given.
    ///
    /// Two paths reach that: the explicit "All providers" choice, and an
    /// editable tile whose stored parameter is absent. The original static
    /// tiles do not come through this intent at all; their separate
    /// compatibility definition always draws every provider.
    var chosenProviderID: String? {
        guard let id = provider?.id,
              id != HeadroomProviderEntity.everyProviderID
        else { return nil }
        return id
    }
}
