import Foundation

/// User-facing chrome shared by macOS, iOS, and widgets.
///
/// Keep in sync with `docs/glossary.md`. Firmware mirrors the same words via
/// `LABEL_*` constants in `firmware/src/main.cpp`. Data titles (providers,
/// pools, verdicts) still come from the host API.
enum HeadroomCopy {
    static let product = "Headroom"

    // MARK: Navigation & sections

    static let usage = "Usage"
    static let summary = "Summary"
    static let quotas = "Quotas"
    static let codingQuotas = "Coding quotas"

    /// "Reset: 5d1h, Sun 1pm." — the two reset readings already available
    /// to the overview, combined into the caption directly below one ring.
    static func quotaOverviewReset(
        duration: String?,
        resetEpoch: Double?,
        timeZone: TimeZone = .autoupdatingCurrent
    ) -> String? {
        var readings: [String] = []
        if let compact = duration?.filter({ !$0.isWhitespace }),
           !compact.isEmpty {
            readings.append(compact)
        }
        if let clock = quotaOverviewClock(
            resetEpoch: resetEpoch, timeZone: timeZone
        ) {
            readings.append(clock)
        }
        guard !readings.isEmpty else { return nil }
        return "Reset: \(readings.joined(separator: ", "))."
    }

    /// "11% to Spare" / "4% Over" — standalone ring-column slack. When a
    /// fit has no signed distance yet, retain the pace state without a number.
    static func quotaOverviewSlack(
        overPace: Bool,
        deltaPct: Double?
    ) -> String {
        guard let deltaPct, abs(deltaPct) >= 1 else {
            return overPace ? "Over Pace" : "On Pace"
        }
        let rounded = Int(abs(deltaPct).rounded())
        return deltaPct > 0 ? "\(rounded)% to Spare" : "\(rounded)% Over"
    }

    private static func quotaOverviewClock(
        resetEpoch: Double?,
        timeZone: TimeZone
    ) -> String? {
        guard let resetEpoch else { return nil }
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = timeZone
        let date = Date(timeIntervalSince1970: resetEpoch)
        let components = calendar.dateComponents([.weekday, .hour], from: date)
        guard let weekday = components.weekday,
              let hour = components.hour,
              (1...7).contains(weekday)
        else { return nil }
        let weekdays = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
        let clockHour = (hour + 11) % 12 + 1
        let meridiem = hour < 12 ? "am" : "pm"
        return "\(weekdays[weekday - 1]) \(clockHour)\(meridiem)"
    }

    static let activity = "Activity"
    static let services = "Services"
    static let supabase = "Supabase"
    static let plausible = "Plausible"
    static let posthog = "PostHog"
    static let sentry = "Sentry"
    static let datadog = "Datadog"
    static let axiom = "Axiom"
    static let localServers = "Local servers"
    static let xcodeBuilds = "Xcode builds"
    static let local = "Local"
    static let integrationsOrderHint = "Drag to reorder · toggles what Activity watches"
    static let activityRowsHint =
        "How many Recent rows the Activity feed draws on this Mac. Same control on Git, Actions, Vercel, Supabase, Sentry, Datadog, and Axiom."
    static let otherMacs = "Other Macs"
    static let computers = "Computers"
    static let addComputer = "Add computer"
    static let noComputersPaired = "No computers paired yet."
    static let computerPairingHint =
        "Each computer keeps its own token in this iPhone’s Keychain. The selected computer supplies live data."
    static let settings = "Settings"
    static let about = "About"
    static let attention = "Attention"

    // MARK: Settings panes
    //
    // Shared taxonomy for the Mac sidebar / iOS Settings stack. Welcome uses
    // friendlier rail titles for the same ideas (see `SettingsDestination`).

    static let settingsGeneral = "General"
    static let settingsSources = "Providers"
    static let settingsiPhone = "iPhone"
    static let settingsSync = "Sync"
    static let settingsTelemetry = "Telemetry"
    static let settingsIntegrations = "Integrations"
    /// The two halves of Integrations that are not agents. Code is where work
    /// lands; services are what you point a key at.
    static let integrationsCode = "Code and deploys"
    static let integrationsServices = "Services"
    static let integrationsBalances = "API balances"
    static let openRouter = "OpenRouter"
    static let aiGateway = "AI Gateway"
    static let balanceLeft = "left"
    static let settingsConnection = "Connection"
    static let settingsPermissions = "Permissions"
    /// General pane: row-count steppers for what this Mac draws.
    /// LabeledContent title on every integration detail Status row.
    static let settingsStatus = "Status"
    static let settingsConnect = "Connect"
    static let settingsDisconnect = "Disconnect"
    static let settingsReplace = "Replace"
    static let settingsRefresh = "Refresh"
    static let hostRunning = "Running"
    static let hostProcess = "Process"
    static let hostStatus = "Host status"
    static let hostVersion = "Version"
    static let hostBuild = "Build"
    static let hostUptime = "Uptime"
    static let hostSourcesReporting = "Sources reporting"
    static let hostReachable = "Reachable"
    static let hostUnavailable = "Unavailable"
    static let hostLocalLaunchAgent = "Local LaunchAgent"
    static let hostLocalProcess = "Local process"
    static let hostRemoteEndpoint = "Remote endpoint"
    /// Host lifecycle (Settings → General → Host).
    static let hostKeepRunning = "Keep the host running when Headroom is closed"
    static let hostKeepRunningOn = "A background service starts at login and serves the board, iPhone and Watch whether or not Headroom is open."
    static let hostKeepRunningOff = "The host starts and stops with Headroom. Quitting the app stops the board, iPhone and Watch too."
    static let usbFallback = "Use USB fallback for the ESP32"
    static let usbFallbackOn = "USB is enabled as a fallback when Wi-Fi is unavailable. It reserves the serial port while active."
    static let usbFallbackOff = "Wi-Fi is the default. USB is available for flashing and serial monitoring."
    static let usbDevice = "USB device"
    static let usbBridge = "USB bridge"
    static let usbDisabled = "Disabled"
    static let usbWaiting = "Enabled · waiting for device"
    static let hostOwnedByApp = "Local process · with Headroom"
    static let hostStoppedCleanly = "The host stopped and did not restart. Something else may own port 8737."
    static let hostGaveUp = "The host failed to stay up. Check ~/.headroom/logs/headroom.err."
    /// Leaving cleanly, for someone about to delete the app.
    static let hostRemoveService = "Remove background service…"
    static let hostRemoveServiceTitle = "Remove the background service?"
    static let hostRemoveServiceBody = "Headroom stops the host, removes its login item, and quits. Nothing runs in the background until you open Headroom again. Your settings, tokens, and history stay in ~/.headroom."
    static let hostRemoveServiceConfirm = "Remove and Quit"
    static let hostRefreshDetails = "Refresh details"
    static let hostNotAvailable = "Not available"
    static let settingsSave = "Save"
    static let settingsCreateToken = "Create token…"
    static let settingsCreateKey = "Create key…"
    static let settingsScanning = "Scanning"
    static let settingsWatching = "Watching"
    /// Toggle / multi-Mac trailing captions.
    static let on = "On"
    static let off = "Off"
    /// macOS Settings → General. Matches System Settings → Login Items wording.
    static let openAtLogin = "Open at Login"
    static let openLoginItemsSettings = "Open Login Items…"
    /// macOS Settings → General. Menu-bar glyph style (fuel vs pace).
    static let menuBarIcon = "Menu bar icon"
    static let menuBarIconRemaining = "Remaining"
    static let menuBarIconPace = "Pace"
    static let menuBarIconInvert = "Invert"
    static let menuBarIconHint =
        "①②③ follow Providers order. Remaining fills each slot by what’s left. Pace places a mark above or below even spend — small gaps move more than big ones. Invert flips either reading (used instead of left, under instead of over)."

    /// Welcome rail / first-run heading for the Sources step.
    static let welcomeWhatToWatch = "What to watch"
    /// Welcome rail for the iPhone step — Settings keeps the short “iPhone”.
    static let welcomeOnYourPhone = "On your phone"
    /// Welcome phone step and Settings → iPhone. Same public invite both places.
    static let openTestFlightInvite = "Open the TestFlight invite"
    /// Canonical join URL — keep in sync with `docs/install-links.md`.
    static let testFlightInvite = URL(string: "https://testflight.apple.com/join/PsQY3YET")!

    /// Person who made it (LICENSE copyright holder).
    static let createdBy = "Created by Michell Zappa"
    /// App Store / signing entity — secondary to the personal credit.
    static let publisher = "Centaur Labs"
    static let aboutSourceOnGitHub = "Source on GitHub"
    static let aboutGitHubStarsLabel = "GitHub stars"
    static let aboutCommunityPulse = "Community Pulse"
    /// Release notes from the bundled CHANGELOG.md (Settings → About).
    static let changelog = "Changelog"
    static let changelogUnavailable = "Changelog unavailable"
    static let changelogUnavailableHint =
        "This build did not ship with release notes."
    static let changelogOnGitHub = "View on GitHub"
    static let aboutOpenSourceFooter =
        "Headroom is open source. Star counts come from GitHub."
    static let done = "Done"

    static func aboutGitHubStars(_ count: Int) -> String {
        "\(count.formatted()) \(aboutGitHubStarsLabel)"
    }

    // MARK: Charts

    static let burndown = "Burndown"
    static let overallBurndown = "Overall burndown"

    // Every burndown says which frame it draws, because there are two and they
    // disagree on purpose. Usage is anchored to the clock (it has no
    // single window to anchor to, which is also why it has no budget line); a
    // provider chart is anchored to its window. Unlabelled, the pair reads as
    // one chart that keeps changing its mind. `frameLabel` on each Domain
    // picks the string, so no surface writes these words itself.

    /// Usage subtitle. Says the anchor, not just the span: the old "7 days"
    /// left a reader guessing whether that meant the week behind, the week
    /// ahead, or the one around them. The domain is three and a half days either
    /// side of today
    /// today, so say so — see `OverallBurndownChartMath`'s history extension.
    static let overallBurndownSubtitle = "7 days around today"
    /// Duration form of the same fact, for the watch, where the long form
    /// wraps to two lines under a chart 62pt tall.
    static let overallBurndownSubtitleShort = "±3.5d"
    /// Provider-chart subtitle: the plot spans this pool's whole window, from
    /// its start to its reset, which is what the budget diagonal measures.
    static let windowFrame = "This window"
    /// Provider-chart subtitle for a monthly pool, where the whole window will
    /// not fit seven weekday columns and the plot is a slice inside it. Not
    /// "around today" — the slice is clamped by the window's edges, so at the
    /// start and end of a month it is not centred on today at all.
    static let windowSliceFrame = "7 days of this window"

    static let dailyBurn = "Daily burn"

    /// The token-value card. "Spend" and not "Cost", because on a
    /// subscription this is what the same work would have cost on the API,
    /// which is a value and not a bill.
    static let spend = "Spend"
    /// Every figure on that card is derived from local token counts and a
    /// price table, never from a provider's billing. It says so on the card
    /// rather than in a tooltip: nobody audits a percentage against a card
    /// statement, and everybody audits a dollar. See docs/metering.md.
    static let spendEstimated = "Estimated"
    static let spendObserved = "Observed"
    static let accountUse = "Account use"
    static let spendToday = "today"
    static let spendPerActiveDay = "per active day"
    static let spendPerDay = "per day"
    static let spendRunway = "runway"
    static let spendRecentDays = "Recent days"
    static let spendByModel = "By model"
    static let spendByKey = "By key"
    static func spendLastDays(_ days: Int) -> String { "\(days)d" }
    /// Shown only when a model burned tokens that `pricing.py` has no rates
    /// for, so part of the figure came from the fallback rate. The names are
    /// the fix, so the names are what it shows.
    static let spendUnpriced = "Unpriced"
    /// Percentage points of a quota window, per day — the same quantity the
    /// host writes as `%/day`. Never "pts": every provider bills in a real
    /// unit of its own called points, credits, or premium requests, and a
    /// reader with those numbers open in another tab will take this for one
    /// of them. Percent is the only unit Headroom claims.
    static let dailyBurnUnit = "% / day"

    /// "Resets 3d" — duration form, for surfaces too narrow for a weekday and
    /// a clock. Host prose says the same instant as "resets Thu 14:00"; see
    /// docs/glossary.md, "Telling time", for which surface gets which.
    static func resets(_ label: String) -> String {
        "Resets \(label)"
    }

    /// "42% used" — the rings' reading. Rounded, because a ring drawn to a
    /// tenth of a percent is the same ring.
    static func percentUsed(_ percent: Double) -> String {
        "\(Int(percent.rounded()))% used"
    }

    /// "58% left" — the burndown's reading, which is remaining rather than
    /// used. Both words stay attached to their number wherever the two glyphs
    /// share a surface, so they never look like one figure disagreeing with
    /// itself.
    static func percentLeft(_ percent: Double) -> String {
        "\(Int(percent.rounded()))% left"
    }

    /// The medium widget's compact pace reading. The slot is deliberate even
    /// when an older cache has no pace value: losing the words would make the
    /// legend jump between layouts and hide that the reading is unavailable.
    static func widgetPaceSlack(_ deltaPct: Double?) -> String {
        guard let deltaPct else { return "— to spare" }
        let rounded = Int(abs(deltaPct).rounded())
        return deltaPct >= 0 ? "\(rounded)% to spare" : "\(rounded)% over"
    }

    /// One mandatory small-widget reset row. Host durations are spaced for
    /// prose (`5d 2h`); the tile removes that internal whitespace (`5d2h`).
    static func widgetReset(
        _ title: String,
        duration: String?,
        resetEpoch: Double? = nil,
        timeZone: TimeZone = .autoupdatingCurrent
    ) -> String {
        let compact = duration?.filter { !$0.isWhitespace }
        let reading = compact.flatMap { $0.isEmpty ? nil : $0 } ?? "—"
        var answer = "\(title): \(reading)"
        if let clock = quotaOverviewClock(
            resetEpoch: resetEpoch, timeZone: timeZone
        ) {
            answer += ", \(clock)"
        }
        return answer
    }

    /// "Empty Thu" — the forecast reaches zero before the pool renews.
    ///
    /// The counterpart to `resets(_:)`, and the one that outranks it wherever
    /// only one fits: a pool that runs dry inside the week is the fact worth
    /// spending the wrist's single line on.
    static func empty(_ label: String) -> String {
        "Empty \(label)"
    }

    static func poolBurndown(_ poolTitle: String) -> String {
        "\(poolTitle) burndown"
    }

    /// "Reset granted · 42% back" — caption on the Codex burndown when a
    /// mid-window grant restarted the curve. Not shown on Usage.
    ///
    /// Percent, not "pts", even though Codex itself grants credits: the number
    /// here is a share of the window this chart draws, and borrowing the
    /// provider's unit for a figure that isn't in it is the worse lie.
    static let resetGranted = "Reset granted"

    static func resetGranted(forgivenPct: Double?) -> String {
        guard let forgivenPct, forgivenPct >= 1 else { return resetGranted }
        return "\(resetGranted) · \(Int(forgivenPct.rounded()))% back"
    }

    static func resetCreditExpires(_ label: String) -> String {
        "Reset credit expires \(label)"
    }

    /// Header over the grant calendar on a pool's detail card. "Recent"
    /// rather than "All": the host journal only reaches ~six months, and
    /// promising a complete record would be a lie the first time retention
    /// drops something off the end.
    static let resetHistory = "Recent resets"

    /// Caption beside the heatmap legend — how many grants the journal still
    /// holds for this pool, not how many cells are lit.
    static func resetHistoryCount(_ count: Int) -> String {
        count == 1 ? "1 reset" : "\(count) resets"
    }

    /// Shown under the legend when the grid is sized from real data rather
    /// than the full six-month journal window.
    static func resetHistorySince(_ date: Date) -> String {
        let formatted = date.formatted(.dateTime.month(.abbreviated).day())
        return "since \(formatted)"
    }

    /// Legend swatch for a public / matched global grant.
    static let resetHistoryGlobal = "Global"

    /// Legend swatch for a banked credit the reader spent themselves.
    static let resetHistoryYours = "Your credit"

    /// One-line footnote under the reset heatmap. Scheduled weekly rolls are
    /// deliberately absent — the chart axis already ends on those.
    static let resetHistoryFootnote =
        "Global grants and credits you spent. Weekly auto-resets stay off the grid."

    /// Day-detail kind label for one grant row.
    static func resetHistoryKind(_ source: String?) -> String {
        switch source {
        case "observed": return resetHistoryYours
        case "announced", "both": return resetHistoryGlobal
        default: return resetGranted
        }
    }

    /// The amount half of a reset history row — the date carries the rest.
    /// Falls back to the bare noun when a grant handed back too little to
    /// round to a point, which happens when a window rolls near empty.
    static func resetPointsBack(_ forgivenPct: Double?) -> String {
        guard let forgivenPct, forgivenPct >= 1 else { return "reset" }
        return "\(Int(forgivenPct.rounded()))% back"
    }

    /// Shown in place of the heatmap before any grant has been seen. Codex
    /// resets are something you spend a credit on, so an empty grid is the
    /// normal state, not a missing-data state.
    static let noResetsYet = "No resets yet"

    /// Settings toggle, both platforms.
    static let notifyOnQuotaReset = "Notify when a quota resets"

    // MARK: Status

    /// Healthy attention summary from the host / Attention card fallback.
    static let allClear = "All clear"
    static let needsAttention = "Needs attention"
    /// iOS link health when the Mac host is reachable, and Integrations hub
    /// trailing caption when a token-backed service has a key.
    static let connected = "Connected"
    /// Integrations hub / detail when nothing is pasted yet.
    static let notConnected = "Not connected"
    /// Detail Status / Credential row: the credential is in this Mac's Keychain.
    static let inKeychain = "Keychain"
    /// Empty SecureField prompt when a token is already stored — shows a key
    /// is present without revealing it. Paste replaces.
    static let settingsKeySavedPrompt = "••••••••••••"
    static let signedIn = "Signed in"
    static let notSignedIn = "Not signed in"
    static let hooksInstalled = "Hooks installed"
    static let hooksOff = "Hooks off"
    static let hooksInstalledShort = "Installed"
    static let hooksNotInstalled = "Not installed"
    static let hooksUpdateAvailable = "Update available"
    static let hooksModifiedExternally = "Modified externally"
    static let hooksConfigurationError = "Configuration error"
    static let gatewayOn = "Gateway on"
    static let gatewayOff = "Gateway off"
    static let folderMissing = "Folder missing"
    /// Host predates the field — not a warning, just a non-answer.
    static let statusUnknown = "Unknown"
    static let macUnavailable = "Mac unavailable"
    static let collectingHistory = "Collecting history"
    /// Host just answered again; sources are being kicked so meters move.
    static let reconnecting = "Reconnecting…"
    /// In-flight poll / sync while the link is already healthy.
    static let refreshing = "Refreshing…"
    /// Popover header / setup body while `/health` is quiet — same fact as the
    /// menu-bar tooltip ("host not answering"), not a Foundation URLError.
    static let hostNotAnswering = "Host not answering"
    /// Auto-start / Start host in flight on the setup card.
    static let startingHost = "Starting the host…"
    static let hostIsRunning = "Host is running"
    static let hostStartHint =
        "Starts at login. Needs the local process on :8737."
    static let hostNothingOnPort = "Nothing is answering on :8737 yet."
    static let startHost = "Start host"
    static let restartHost = "Restart host"
    static let retryCheck = "Retry check"
    static let whatToTrack = "What to track"
    static let whatToTrackHint =
        "From local sign-in. Change either list later in Settings."
    static let dismiss = "Dismiss"
    /// Bulk clear on either Attention section: passive agent notices, or the
    /// warnings the rollup and the feed put in the queue. Mac Attention uses
    /// the same label and action (no per-row swipe there).
    static let dismissAll = "Dismiss all"
    /// Legacy name for `dismissAll` — Mac Attention used to say Clear and only
    /// ack the rollup; both surfaces now dismiss the queue and ack together.
    static let clearAttention = dismissAll
    static let refreshAll = "Refresh all"
    static let answerCodingAgents = "Answer coding agents"
    static let codingAgents = "Agents"
    static let agentAlerts = "Agent alerts"
    static let agentAlertsHelp =
        "Show passive agent notices such as “Ready for your next instruction”. Questions, choices, and approvals always remain visible."
    static let agentCompanionTitle = "Your agents, wherever you are"
    static let agentQuestionMode = "Claude questions"
    static let agentQuestionNotify = "Show on Mac + iPhone"
    static let agentQuestionAnswer = "Let iPhone answer"
    static let agentQuestionOff = "Mac only"
    static let agentQuestionModeHelp =
        "Let iPhone answer briefly pauses a question while Headroom waits for your choice."
    static let claudeCode = "Claude Code"
    static let claudeCodeHooks = "Claude Code hooks"
    static let installHooks = "Install hooks"
    static let reinstallHooks = "Reinstall hooks"
    static let removeHooks = "Remove hooks"
    /// Adds one harmless Claude row to the common feed. Named for what it
    /// does: "attention" is the card's name and the API's table, not a thing
    /// you can have one of and send.
    static let sendTestAttention = "Add a test row"
    static func usingCodex(at path: String) -> String {
        "Using Codex at \(path)"
    }

    /// The agent's own request, shown before you answer it. An approval you
    /// cannot read is not an approval, so these labels sit above the actual
    /// fields rather than a paraphrase of them.
    static let agentRequest = "Request"
    static let agentWhyAsking = "Why"
    static let showFullRequest = "Show request"
    static let hideFullRequest = "Hide request"
    /// Said plainly: the value on screen is a prefix, not the whole thing.
    static let agentValueShortened = "Shortened to fit"
    /// Shown beside an always-allow answer. A durable grant made from a phone
    /// should say exactly what it will write before you tap it.
    static let agentWouldSaveRule = "Saves the rule"
    /// The free-text answer. None of the fixed buttons is ever quite the
    /// thing you want to say, so every request that has a channel for words
    /// offers one.
    static let agentReplyPlaceholder = "Reply to the agent…"
    /// A question shows in both places and is answered where it was asked.
    static let answerInTheTerminal = "Answer in the terminal"

    /// Giving an agent work, rather than answering work it already started.
    static let startTask = "Start task"
    static let startTaskAgent = "Agent"
    static let startTaskFolder = "Folder"
    static let startTaskPromptPlaceholder = "What should it do?"
    static let chooseFolder = "Choose folder…"
    /// A phone cannot browse the Mac's disk, so it waits for the Mac to use
    /// one first. Says that, rather than showing an empty picker.
    static let noFoldersYet = "Start one on the Mac first to pick a folder here"
    static let noAgentCanTakeWork = "No agent is connected to take work"

    /// Said after a start succeeds. Both providers answer `ok` and then work
    /// quietly, so without this the surface looked like it had done nothing.
    static func agentIsWorking(_ agent: String, in folder: String) -> String {
        "\(agent) is working in \(folder)"
    }
    /// The Mac has no feed of its own, so it says where the answers arrive.
    static let watchOnPhone = "Requests appear on your iPhone"
    static func agentFieldsOmitted(_ count: Int) -> String {
        count == 1 ? "1 more field not shown" : "\(count) more fields not shown"
    }

    /// Shown when the phone is drawing its last saved payload because the Mac
    /// is not answering. The numbers are real, they are just not current, and
    /// the copy has to say which.
    static let recentHistory = "Recent history"
    static let recentHistoryHint = "Saved on this iPhone. Not live."
    static let nothingSavedYet = "Nothing saved yet"

    /// "Recent history · 2 hours ago" — one label, both facts.
    static func recentHistory(age: TimeInterval) -> String {
        "\(recentHistory) · \(ago(age))"
    }

    /// Coarse on purpose: "4 minutes ago" on a quota bar reads as precision
    /// the saved number does not have.
    static func ago(_ age: TimeInterval) -> String {
        let minutes = Int((age / 60).rounded())
        if minutes < 2 { return "just now" }
        if minutes < 60 { return "\(minutes) min ago" }
        let hours = Int((age / 3600).rounded())
        if hours < 24 { return hours == 1 ? "1 hour ago" : "\(hours) hours ago" }
        let days = Int((age / 86_400).rounded())
        return days == 1 ? "1 day ago" : "\(days) days ago"
    }

    /// Compact age for dense rows: "just now", "4m ago", "18h ago", "3d ago".
    /// `ago(_:)` spells the units out, which is right on a card and wrong in
    /// a table column that has to hold eight of them.
    static func agoShort(_ age: TimeInterval) -> String {
        let minutes = Int((age / 60).rounded())
        if minutes < 2 { return "just now" }
        if minutes < 60 { return "\(minutes)m ago" }
        let hours = Int((age / 3600).rounded())
        if hours < 24 { return "\(hours)h ago" }
        return "\(Int((age / 86_400).rounded()))d ago"
    }

    // MARK: Sources pane (design 2a "Active vs. Library")

    /// Enabled sources, rich rows with live usage.
    static let sourcesActive = "Active"
    /// Everything available but off, as compact chips.
    static let sourcesLibrary = "Library"
    static let sourcesActiveHint = "Drag to reorder · ①②③ show in the menu bar"
    static let sourcesLibraryHint = "Turn one on to move it up to Active"
    /// Sends a service from Active back to the Library. Tracking stops;
    /// credentials are never touched — Headroom has no sign-ins to revoke.
    static let moveToLibrary = "Move to Library"
    /// A paused row: configured, still in Active, not polled.
    static let sourcePaused = "Off"
    /// The inline add link under a multi-account-capable service.
    static let addAccount = "Add account…"
    static let addAccountSection = "Add account"
    /// A Library chip whose credential has no local trace to import.
    static let notDetected = "not detected"
    /// Row-subtitle category labels. The Library groups use `aiProvidersGroup`
    /// / `devTools`; in Active the category is metadata riding the subtitle.
    static let aiProviderCategory = "AI provider"
    static let devToolCategory = "Dev tool"
    /// Library group header for the coding side. Distinct from `aiTools`
    /// ("AI coding tools"), which titles onboarding's checklist.
    static let aiProvidersGroup = "AI providers"

    /// The on/off an integration leaf owns now that dev tools have left the
    /// Sources pane. Deliberately about visibility rather than connection:
    /// switching it off stops the polling and hides the rows, and leaves the
    /// key in the Keychain — Disconnect is the other button, and it is the
    /// one that forgets a credential.
    static let showInHeadroom = "Show in Headroom"

    /// A meter the Mac is replaying instead of fetching. The word alone reads
    /// as a hiccup you can wait out, so the age travels with it — "2 hours
    /// ago" is what turns it into something to go and fix.
    static let notUpdating = "Not updating"

    static func notUpdating(age: TimeInterval) -> String {
        "\(notUpdating) · \(ago(age))"
    }

    /// A meter the host is deliberately not refreshing — usually a provider
    /// rate limit. Not an alarm: the host already backed off, and naming the
    /// wait stops the reflex of hammering Refresh.
    static let updatingPaused = "Paused"

    static func updatingPaused(retryIn: TimeInterval) -> String {
        "\(updatingPaused) · retries in \(inAbout(retryIn))"
    }

    /// Compact future duration for retry copy: "1m", "5m", "1h".
    static func inAbout(_ seconds: TimeInterval) -> String {
        let minutes = max(1, Int((seconds / 60).rounded()))
        if minutes < 60 { return "\(minutes)m" }
        let hours = Int((seconds / 3600).rounded())
        if hours < 24 { return hours <= 1 ? "1h" : "\(hours)h" }
        let days = max(1, Int((seconds / 86_400).rounded()))
        return days == 1 ? "1d" : "\(days)d"
    }

    /// A meter whose login is gone or refused. "Not updating" is true of this
    /// too, and useless: it reads as a connection to wait out, when the fetch
    /// will keep failing until someone signs in. The age still travels with
    /// it, because how long the numbers have been fiction is the part that
    /// decides whether this matters now.
    static let needsSignIn = "Needs sign-in"

    static func needsSignIn(age: TimeInterval) -> String {
        "\(needsSignIn) · \(ago(age))"
    }

    // MARK: Service health
    //
    // Supabase, Plausible, and the Supabase advisors, on the same axis as
    // source health above: does the reader wait, or go and do something.
    //
    // These three used to say "Supabase unavailable". That word was carrying
    // a missing key, a failed fetch, and a Mac that wasn't answering, and it
    // named none of them — a reader who has not pasted a token and a reader
    // whose network blipped got the same sentence. The host's own `error`
    // string still wins when there is one; this is the fallback for when
    // there isn't.

    /// No credential yet. `configured == false` on the service payload.
    static func serviceNeedsKey(_ service: String) -> String {
        "\(service) needs a key"
    }

    /// Configured, and it did not answer. Nothing for the reader to do.
    static func serviceNotReporting(_ service: String) -> String {
        "\(service) not reporting"
    }

    /// Picks between the two so no surface has to, the way `statusNote` does
    /// for sources.
    static func serviceStatus(_ service: String, configured: Bool?) -> String {
        configured == false ? serviceNeedsKey(service) : serviceNotReporting(service)
    }

    /// The provider didn't name the plan. Not a failure and not actionable —
    /// the status label beside it already says whether anything is wrong — so
    /// it says what it knows rather than borrowing an alarm word.
    static let planUnknown = "Plan unknown"

    // MARK: Activity feed

    /// What a feed row's host status (`failure`, `ready`, `pushed`, …) is
    /// called out loud. Every row says its state in words as well as colour,
    /// so a red dot is never carrying the fact on its own.
    /// `Shared/ActivityStatus.swift` owns the mapping.
    static let activityFailed = "Failed"
    static let activityBuilding = "Building"
    static let activityRunning = "Running"
    static let activityQueued = "Queued"
    static let activityDeployed = "Deployed"
    static let activityPassed = "Passed"
    static let activityCanceled = "Canceled"
    /// Feed label for a quota the provider handed back early.
    static let activityReset = "Reset"
    static let activityPushed = "Pushed"
    static let activityLocal = "Local"
    static let activityCommitted = "Committed"
    /// Incoming PR review on a watched repo (GitHub inbox → Attention).
    static let activityReviewRequest = "Review"
    /// Issue or PR assigned to you on a watched repo.
    static let activityAssigned = "Assigned"
    /// Open issue or PR on a watched repo that @mentions you.
    static let activityMention = "Mention"

    /// The feed's section title on the iOS Activity tab and Mac Activity
    /// mode, where it shares a screen with the service panels. "Recent"
    /// rather than "Activity", which is the tab / mode it already sits in.
    static let recentActivity = "Recent"

    /// Function-level titles for activity kinds. The Activity tab itself is
    /// one chronological list; these labels remain for Attention captions,
    /// contract tests, and any surface that still groups by kind.
    static let vercelDeployments = "Vercel deployments"
    static let gitCommits = "Git commits"
    static let quotaResets = "Quota resets"
    static let claudeStatus = "Claude status"
    static let otherActivity = "Other activity"

    static func activityGroupTitle(for kind: String?) -> String {
        switch kind {
        case "github": return githubActions
        case "deployment": return vercelDeployments
        case "commit": return gitCommits
        case "supabase": return supabase
        case "sentry": return sentry
        case "datadog": return datadog
        case "axiom": return axiom
        case "reset": return quotaResets
        case "claude-status": return claudeStatus
        default: return otherActivity
        }
    }

    /// "2 need attention" — the count in Attention's section header. The tab
    /// badge carries the same signal when the user is on another tab.
    static func needsAttention(count: Int) -> String {
        count == 1 ? "1 needs attention" : "\(count) need attention"
    }

    // MARK: Empty states

    static let noHistoryYet = "No history yet"
    static let noBurnHistoryYet = "No burn history yet"
    static let noSpendYet = "No spend recorded yet"
    static let noCodingSources = "No coding sources"
    static let noActivityYet = "No activity yet"
    static let noLocalServers = "No local servers"
    static let noXcodeBuilds = "No Xcode builds"
    static let waitingForMacSync = "Waiting for Mac sync"
    static let searchingNearby = "Searching…"

    // MARK: Sources

    static let githubActions = "GitHub Actions"

    /// The two halves of Sources. AI tools meter a plan you're signed into;
    /// dev tools watch projects you connect with a key. Keep them apart.
    static let aiTools = "AI coding tools"
    static let aiToolsHint = "Signed in on this Mac. Nothing to paste."
    static let devTools = "Dev tools"
    static let devToolsHint = "Projects and pipelines. Some need a key."

    // MARK: Welcome

    /// First-run chrome. Only the names that appear in more than one place
    /// live here — the pane prose is macOS-only and stays in `WelcomeView`,
    /// which keeps forty strings out of the watch and widget binaries.
    static let welcomeTitle = "Welcome to Headroom"
    static let welcomeFinish = "Start using Headroom"
    static let showWelcome = "Show welcome"
    /// The callout that points at the menu bar icon on first run.
    static let welcomeCoachMark = "Headroom lives here"

    // MARK: Updates

    /// App updates. The host has its own version skew story and its own words
    /// (`HostSkew`); these are only ever about the .app.
    static let appUpdates = "App updates"
    /// This copy's `CFBundleShortVersionString`. Always shown in Settings.
    static let appUpdatesCurrent = "Current"
    /// What the update feed last reported. Always shown once a check has run.
    static let appUpdatesLatest = "Latest"
    static let automaticUpdateChecks = "Check automatically"
    static let checkForUpdates = "Check for updates"
    static let checkingForUpdates = "Checking…"
    static let upToDate = "Up to date"
    static let installUpdate = "Install and restart"
    /// Popover footer and Settings status when a newer build is on the feed.
    /// Left is what this copy is; right is what the feed offers.
    static func newVersionAvailable(from installed: String, to latest: String) -> String {
        "\(installed) > \(latest)"
    }
    static func newVersionAvailableAccessibility(
        from installed: String,
        to latest: String
    ) -> String {
        "New version available: \(installed) to \(latest), install Headroom \(latest)"
    }
    /// Said to a copy that is not in /Applications, so it must not replace
    /// itself. Names the reason rather than greying a button with no comment.
    static let updatesNotFromHere =
        "Updates install to /Applications. This copy runs from somewhere else."
    static let updateCheckFailed = "Could not reach the update feed."

    // MARK: Telemetry

    static let telemetryHeader = "Anonymous diagnostics"
    static let telemetryToggle = "Share anonymous product diagnostics"
    static let telemetryWhatIsShared = "What Headroom shares"
    static let telemetrySharedDetail =
        "Once a week: the app and host versions, macOS version, processor family, enabled and healthy service names, normalized model-family shares, a few feature flags, and a week-scoped dedupe key so this Mac cannot be counted twice in the same week."
    static let telemetryNeverSharedDetail =
        "Never: prompts, commands, files, repositories, branches, account names, email addresses, tokens, exact spend, raw model names, a stable install id, or per-request activity."
    static let telemetryViewSource = "Read the telemetry code on GitHub"
    static let telemetryCommunityPulse = "See the public Community Pulse"
    static let telemetryCommunityHeader = "Community Pulse"
    static let telemetryCommunityLoading = "Loading the community snapshot…"
    static let telemetryCommunityUnavailable = "Community data is unavailable right now."
    static let telemetryCommunityGrowing = "The community is still growing."
    static let telemetryCommunityThreshold = "Counts appear after at least five Macs contribute."
    static let telemetryWeeklyActive = "Weekly active Macs"
    static let telemetryWeekOverWeek = "Week over week"
    static let telemetryLatestBuild = "Most common build"
    static let telemetryTopArchitecture = "Most common CPU"
    static let telemetryServicesInUse = "Services in use"
    static let telemetryModelMix = "Model family mix"
    static let telemetryBuildSpread = "Build spread"
    static let telemetryVersionDistribution = "Version distribution"
    static let telemetryLatestRelease = "Latest release"
    static let telemetryOnLatest = "on latest"
    static let telemetryArchitectureMix = "Architecture"
    static let telemetryMacOSMix = "macOS major"
    static let telemetryCountryMix = "Countries"
    static let telemetryServiceMix = "Service mix"
    static let telemetryServicesEnabled = "Enabled"
    static let telemetryServicesUsed = "Used"
    static let telemetryServicesHealthy = "Healthy"
    static let telemetryFeatureAdoption = "Feature adoption"
    static let telemetryNoPublishedData = "Not enough community data to publish this yet."
    static let telemetryRefreshCommunity = "Refresh community"
    static let telemetryLatestWeek = "latest week"
    static let telemetryMacs = "Macs"
    static let telemetryNeedPriorWeek = "Need two published weeks"
    static let telemetryLastCompleteWeek = "last complete week"
    static let telemetryNeedCompleteWeeks = "Need two complete weeks"
    static let telemetryWeekToDate = "so far"
    static let telemetryWeekToDateNote =
        "The newest week is still filling. Every count below covers it so far, not a full seven days."
    static let telemetryWeekInProgressKey = "Faded bar is the week in progress."
    static let telemetryGrowthHeader = "New vs returning"
    static let telemetryCohortNew = "New"
    static let telemetryCohortReturning = "Returning"
    static let telemetryCohortReactivated = "Reactivated"
    static let telemetryCohortPending =
        "The split appears once a full week has closed."
    static let telemetryRetentionSuffix = "of the week before came back"
    static let telemetryCommunityFooter =
        "A public aggregate of other opted-in Macs. Small groups stay hidden; weekly counts are reports, not user identities. This Mac’s payload is shown above."
    static let telemetryFooter =
        "On by default so we can see which builds and services are actually in use. Turn it off at any time; pending data is deleted from this Mac."
    static let telemetryVisualizer = "Inspect this week's aggregate"
    static let telemetryWaitingForPreview = "No preview loaded"
    static let telemetryPreviewReady = "This is what Headroom would send"
    static let telemetryRefreshPreview = "Refresh"
    static let telemetryCopyPayload = "Copy JSON"
    static let telemetryCopied = "Copied"
    static let telemetryPreviewOff =
        "Turn diagnostics on to build a local preview. Nothing is sent by this view."
    static let telemetryBuildingPreview = "Building preview…"
    static let telemetryPreviewUnavailable =
        "A preview is not available yet. Try Refresh."
    static let telemetryApp = "App"
    static let telemetryHost = "Host"
    static let telemetryPeriod = "Period"
    static let telemetryMac = "Mac"
    static let telemetryLastSent = "Last sent"
    static let telemetryHostVersionDetail = "host version"
    static let telemetryWeeklyDetail = "one batch per week"
    static let telemetryServices = "Services"
    static let telemetryNoServices = "No enabled or observed services yet."
    static let telemetryEnabled = "enabled"
    static let telemetryUsed = "used"
    static let telemetryHealthy = "healthy"
    static let telemetryModels = "Model families"
    static let telemetryNoModels = "No model-family history is available yet."
    static let telemetryFeatures = "Feature flags"
    static let telemetryNoFeatures = "No feature flags are available yet."
    static let telemetryNever = "Never"
    static let telemetryNoPending = "No pending batch"
    static let telemetryPending = "Pending locally"
    static let notAvailable = "Not available"

    // MARK: Widget

    static let openToSync = "Open to sync"
    static let openHeadroom = "Open Headroom"
    static let openPermalink = "Open"

    // MARK: Watch

    /// The watch's empty state. It cannot reach the Mac itself — the phone
    /// forwards what it fetched — so "open Headroom" has to say where.
    static let openOnPhone = "Open Headroom on iPhone"
    /// Header above the combined dial on the watch app's one screen.
    static let onePerProvider = "One ring per source"
}
