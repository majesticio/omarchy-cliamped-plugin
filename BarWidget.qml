import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

BarWidget {
  id: root
  moduleName: "io.github.majesticio.cliamped"

  readonly property var stations: [
    { name: "Lofi", url: "https://radio.cliamp.stream/lofi/stream" },
    { name: "Synthwave", url: "https://radio.cliamp.stream/synthwave/stream" },
    { name: "EDM", url: "https://radio.cliamp.stream/edm/stream" },
    { name: "NCS", url: "https://radio.cliamp.stream/ncs/stream" },
    { name: "House", url: "https://radio.cliamp.stream/ncs-house/stream" },
    { name: "Dubstep", url: "https://radio.cliamp.stream/ncs-dubstep/stream" },
    { name: "Drum & Bass", url: "https://radio.cliamp.stream/ncs-dnb/stream" },
    { name: "Trap", url: "https://radio.cliamp.stream/ncs-trap/stream" },
    { name: "Phonk", url: "https://radio.cliamp.stream/ncs-phonk/stream" },
    { name: "Pop", url: "https://radio.cliamp.stream/ncs-pop/stream" },
    { name: "Chill", url: "https://radio.cliamp.stream/ncs-chill/stream" }
  ]

  property bool sessionReady: false
  property bool ownsDaemon: false
  property bool startingDaemon: false
  property bool probeInFlight: false
  property bool modeKnown: false
  property string sessionMode: "unknown"
  property string state: "stopped"
  property string trackTitle: "CLIAMPed"
  property string trackArtist: ""
  property string trackAlbum: ""
  property string trackPath: ""
  property real positionSeconds: 0
  property real durationSeconds: 0
  property real volumeDb: 0
  property bool shuffle: false
  property string repeatMode: "Off"
  property bool mono: false
  property real playbackSpeed: 1
  property string eqPreset: "Flat"
  readonly property var panelVisualizers: ["Spectrum", "Canyon", "Pulse"]
  property int panelVisualizerIndex: 0
  readonly property string panelVisualizerName: panelVisualizers[panelVisualizerIndex]
  property int currentIndex: 0
  property int trackTotal: 0
  property string errorText: ""
  property string queuedUrl: ""
  property var providers: []
  property var providerPlaylists: []
  property var providerResults: []
  property var searchFavorites: []
  property bool searchFavoritesBusy: false
  property string selectedProviderKey: ""
  property string loadedProviderPlaylistId: ""
  property string providerRequestKind: ""
  property bool providerBusy: false
  property bool providerSearchAttempted: false
  property int radioCatalogOffset: 0
  property bool radioCatalogHasMore: true
  property bool radioCatalogRequested: false
  property string providerError: ""
  property var queueTracks: []
  property var historyItems: []
  property var lyricLines: []
  property var audioDevices: []
  property var ipcQueue: []
  property var ipcCurrent: null
  property bool ipcBusy: false
  property bool volumeDirty: false
  property real pendingVolumeDelta: 0
  property var pendingAfterStart: null
  property int startupAttempts: 0

  readonly property bool playing: state === "playing"
  readonly property var providerCollections: {
    var collections = []
    for (var i = 0; i < providerPlaylists.length; ++i) {
      var item = providerPlaylists[i]
      // CLIAMP's built-in radio entry is an M3U index. The panel exposes its
      // resolved channels separately so it is never presented as a dead card.
      // Favorite aliases (`f:`) belong exclusively in Favorites; CLIAMP also
      // returns their starred catalog (`c:`) entries, so showing both here
      // would duplicate each favorited station in Browse.
      if (selectedProviderKey === "radio") {
        var itemId = String(item.id || "")
        if (itemId === "l:0" || itemId.indexOf("f:") === 0) continue
      }
      collections.push(item)
    }
    return collections
  }
  readonly property var providerFavorites: {
    var favorites = []
    for (var i = 0; i < providerPlaylists.length; ++i) {
      var item = providerPlaylists[i]
      if (String(item.id || "").indexOf("f:") === 0) favorites.push(item)
    }
    return favorites
  }
  readonly property var favoriteItems: {
    var favorites = []
    var nativeNames = ({})
    for (var i = 0; i < providerFavorites.length; ++i) {
      var item = providerFavorites[i]
      var cleanName = String(item.name || item.id).replace(/^★\s*/, "")
      nativeNames[cleanName.toLowerCase()] = true
      favorites.push({ kind: "provider", id: item.id, name: cleanName })
    }
    for (var j = 0; j < searchFavorites.length; ++j) {
      var track = searchFavorites[j]
      var title = String(track.title || track.path)
      if (nativeNames[title.toLowerCase()]) continue
      favorites.push({ kind: "search", id: "search:" + String(track.path), name: title, track: track })
    }
    return favorites
  }
  readonly property string sessionLabel: sessionMode === "headless" ? "BACKGROUND"
    : sessionMode === "tui" ? "CLIAMP TUI" : "CONNECTING"
  readonly property string selectedStation: stationNameFor(trackPath)
  readonly property var bands: bandStream.bands
  readonly property string barLabel: sessionReady
    ? ((playing ? "󰝚" : "󰏤") + "  " + (selectedStation || trackTitle || "CLIAMP"))
    : "󰝚  CLIAMP"

  function stationNameFor(path) {
    var clean = String(path || "").replace(/^https?:/i, "").replace(/\/$/, "")
    for (var i = 0; i < stations.length; ++i) {
      if (String(stations[i].url).replace(/^https?:/i, "").replace(/\/$/, "") === clean)
        return stations[i].name
    }
    return ""
  }

  function catalogSize(playlists) {
    var count = 0
    for (var i = 0; i < playlists.length; ++i)
      if (/^c:/.test(String(playlists[i].id || ""))) count += 1
    return count
  }

  function ipcHelperPath() {
    return String(Qt.resolvedUrl("cliamp_ipc.py")).replace(/^file:\/\//, "")
  }

  function searchFavoritesHelperPath() {
    return String(Qt.resolvedUrl("cliamped_search_favorites.py")).replace(/^file:\/\//, "")
  }

  function isSearchFavorite(track) {
    var path = String(track && track.path || "")
    for (var i = 0; i < searchFavorites.length; ++i)
      if (String(searchFavorites[i].path || "") === path) return true
    return false
  }

  function refreshSearchFavorites() {
    if (searchFavoriteProcess.running) return
    searchFavoritesBusy = true
    searchFavoriteProcess.command = ["python3", searchFavoritesHelperPath(), "list"]
    searchFavoriteProcess.running = true
  }

  function toggleSearchFavorite(track) {
    if (!track || !track.path || searchFavoriteProcess.running) return
    searchFavoritesBusy = true
    searchFavoriteProcess.command = ["python3", searchFavoritesHelperPath(), "toggle", JSON.stringify(track)]
    searchFavoriteProcess.running = true
  }

  function playFavorite(item) {
    if (!item) return
    if (item.kind === "search") playProviderTrack(item.track)
    else loadProviderPlaylist(item.id)
  }

  function removeFavorite(item) {
    if (!item) return
    if (item.kind === "search") toggleSearchFavorite(item.track)
    else toggleProviderFavorite(item.id)
  }

  function isProviderKind(kind) {
    return kind === "providers" || kind === "playlists" || kind === "catalog"
      || kind === "favorite" || kind === "search" || kind === "load" || kind === "urlLoad"
  }

  function syncProviderBusy() {
    if (ipcCurrent && isProviderKind(ipcCurrent.kind)) {
      providerBusy = true
      return
    }
    for (var i = 0; i < ipcQueue.length; ++i) {
      if (isProviderKind(ipcQueue[i].kind)) {
        providerBusy = true
        return
      }
    }
    providerBusy = false
  }

  function enqueueIpc(kind, request, fallbackArgs) {
    if (!sessionReady && kind !== "status") return false
    var pending = ipcQueue.slice()
    pending.push({ kind: kind, request: request, fallback: fallbackArgs || [] })
    ipcQueue = pending
    syncProviderBusy()
    pumpIpc()
    return true
  }

  function enqueueVolume(target, delta) {
    if (!delta) return
    var pending = ipcQueue.slice()
    // Socket volume is absolute; the public CLI fallback remains relative.
    pending.push({
      kind: "volumeAction",
      request: { cmd: "volume", value: target },
      fallback: ["volume", String(delta)]
    })
    ipcQueue = pending
    volumeDirty = true
    pumpIpc()
  }

  function volumeRequestPending() {
    if (ipcCurrent && ipcCurrent.kind === "volumeAction") return true
    for (var i = 0; i < ipcQueue.length; ++i)
      if (ipcQueue[i].kind === "volumeAction") return true
    return false
  }

  function pumpIpc() {
    if (ipcBusy || providerProcess.running || !ipcQueue.length) return
    var pending = ipcQueue.slice()
    ipcCurrent = pending.shift()
    ipcQueue = pending
    ipcBusy = true
    providerRequestKind = ipcCurrent.kind
    syncProviderBusy()
    providerProcess.command = ["python3", ipcHelperPath(), JSON.stringify(ipcCurrent.request)]
    providerProcess.running = true
  }

  function finishIpc() {
    ipcBusy = false
    ipcCurrent = null
    syncProviderBusy()
    Qt.callLater(pumpIpc)
  }

  function runProviderRequest(kind, request) {
    providerError = ""
    return enqueueIpc(kind, request, [])
  }

  function refreshProviders() {
    runProviderRequest("providers", { cmd: "provider.list" })
  }

  function selectProvider(key) {
    selectedProviderKey = String(key || "")
    loadedProviderPlaylistId = ""
    providerPlaylists = []
    providerResults = []
    providerSearchAttempted = false
    radioCatalogOffset = 0
    radioCatalogHasMore = true
    radioCatalogRequested = false
    if (selectedProviderKey)
      runProviderRequest("playlists", { cmd: "provider.playlists", provider: selectedProviderKey })
  }

  function searchProvider(query) {
    var value = String(query || "").trim()
    if (!value || !selectedProviderKey) return
    providerResults = []
    providerSearchAttempted = true
    runProviderRequest("search", {
      cmd: "provider.search", provider: selectedProviderKey, query: value, limit: 18
    })
  }

  function loadRadioCatalog() {
    if (selectedProviderKey !== "radio" || !radioCatalogHasMore) return
    radioCatalogRequested = true
    runProviderRequest("catalog", {
      cmd: "provider.catalog", provider: "radio", offset: radioCatalogOffset, limit: 18
    })
  }

  function toggleProviderFavorite(playlistId) {
    if (selectedProviderKey !== "radio" || !playlistId) return
    runProviderRequest("favorite", {
      cmd: "provider.favorite", provider: "radio", playlist: String(playlistId)
    })
  }

  function showFavorites() {
    if (!providers.length) refreshProviders()
    else if (selectedProviderKey !== "radio") selectProvider("radio")
    else runProviderRequest("playlists", { cmd: "provider.playlists", provider: "radio" })
  }

  function loadProviderPlaylist(playlistId) {
    if (!selectedProviderKey || !playlistId) return
    runProviderRequest("load", {
      cmd: "provider.load", provider: selectedProviderKey, playlist: String(playlistId)
    })
  }

  function playProviderTrack(track) {
    if (!track || !track.path) return
    loadedProviderPlaylistId = ""
    enqueueIpc("play", { cmd: "track.play", track: track }, [])
  }

  function playLocalFile(filePath) {
    var path = String(filePath || "")
    if (!path) return
    loadedProviderPlaylistId = ""
    enqueueIpc("play", { cmd: "track.play", track: { path: path } }, [])
  }

  function playLocalFiles(paths) {
    if (!paths || !paths.length) return
    loadedProviderPlaylistId = ""
    for (var i = 0; i < paths.length; ++i) {
      var path = String(paths[i] || "")
      if (!path) continue
      enqueueIpc(i === 0 ? "play" : "queueMutation", {
        cmd: i === 0 ? "track.play" : "track.queue", track: { path: path }
      }, [])
    }
  }

  function refreshQueue() { enqueueIpc("queueList", { cmd: "queue.list" }, []) }
  function playQueueIndex(index) { enqueueIpc("queueMutation", { cmd: "queue.play", index: index }, []) }
  function enqueueQueueIndex(index) { enqueueIpc("queueMutation", { cmd: "queue.enqueue", index: index }, []) }
  function removeQueueIndex(index) { enqueueIpc("queueMutation", { cmd: "queue.remove", index: index }, []) }
  function clearQueue() { enqueueIpc("queueMutation", { cmd: "queue.clear" }, []) }
  function refreshHistory() { enqueueIpc("history", { cmd: "history", limit: 24 }, []) }
  function playHistoryItem(item) {
    if (item && item.track) {
      loadedProviderPlaylistId = ""
      enqueueIpc("play", { cmd: "track.play", track: item.track }, [])
    }
  }
  function refreshLyrics() { enqueueIpc("lyrics", { cmd: "lyrics" }, []) }
  function refreshDevices() { enqueueIpc("devices", { cmd: "device", name: "list" }, ["device", "list"]) }
  function selectDevice(name) {
    if (name) enqueueIpc("deviceSet", { cmd: "device", name: String(name) }, ["device", String(name)])
  }

  function injectPanel() {
    var target = panelLoader.item
    if (!target) return
    if ("bar" in target) target.bar = root.bar
    if ("settings" in target) target.settings = root.settings
    if ("anchorItem" in target) target.anchorItem = button
    if ("hostWidget" in target) target.hostWidget = root
  }

  function probe() {
    if (probeInFlight || statusProbe.running) return
    probeInFlight = true
    enqueueIpc("status", { cmd: "status" }, ["status", "--json"])
  }

  function parseStatus(raw) {
    try {
      var value = JSON.parse(String(raw || ""))
      if (!value || !value.ok) return false
      sessionReady = true
      startingDaemon = false
      errorText = ""
      if (!modeKnown && !modeProbe.running) modeProbe.running = true
      state = String(value.state || "stopped").toLowerCase()
      if (value.volume !== undefined && !volumeDirty && !volumeRequestPending())
        volumeDb = Number(value.volume)
      shuffle = value.shuffle === true
      repeatMode = String(value.repeat || "Off")
      mono = value.mono === true
      playbackSpeed = value.speed === undefined ? 1 : Number(value.speed)
      eqPreset = String(value.eq_preset || "Flat")
      currentIndex = value.index === undefined ? 0 : Number(value.index)
      trackTotal = value.total === undefined ? 0 : Number(value.total)
      positionSeconds = value.position === undefined ? 0 : Number(value.position)
      durationSeconds = value.duration === undefined ? 0 : Number(value.duration)
      if (value.track) {
        var fallbackTitle = String(value.track.path || "").split("/").pop()
        fallbackTitle = fallbackTitle.replace(/\.[^.]+$/, "")
        trackTitle = String(value.track.title || fallbackTitle || "CLIAMPed")
        trackArtist = String(value.track.artist || "")
        trackAlbum = String(value.track.album || "")
        trackPath = String(value.track.path || "")
      }
      return true
    } catch (e) {
      return false
    }
  }

  function startOwnedDaemon() {
    if (daemon.running || startingDaemon) return
    ownsDaemon = true
    sessionMode = "headless"
    modeKnown = true
    startingDaemon = true
    startupAttempts = 0
    errorText = "Starting a private CLIAMP session…"
    daemon.running = true
    startupProbe.restart()
  }

  function runAction(args) {
    if (!args || !args.length) return false
    var cmd = String(args[0])
    var request = { cmd: cmd }
    if (cmd === "volume" || cmd === "speed" || cmd === "seek") request.value = Number(args[1])
    else if (cmd === "shuffle" || cmd === "repeat" || cmd === "mono" || cmd === "vis" || cmd === "eq")
      request.name = String(args[1] || "")
    return enqueueIpc("action", request, args)
  }

  function togglePlayback() {
    if (!sessionReady) {
      pendingAfterStart = { kind: "action", request: { cmd: "play" }, fallback: ["play"] }
      startOwnedDaemon()
      delayedSelection.restart()
      return
    }
    runAction([state === "stopped" ? "play" : "toggle"])
  }

  function next() { if (sessionReady) runAction(["next"]) }
  function previous() { if (sessionReady) runAction(["prev"]) }
  function stop() { if (sessionReady) runAction(["stop"]) }
  function adjustVolume(delta) {
    if (!sessionReady) return
    var target = Math.max(-30, Math.min(6, volumeDb + Number(delta || 0)))
    if (target === volumeDb) return
    volumeDirty = true
    pendingVolumeDelta += target - volumeDb
    volumeDb = target
    volumeCommit.restart()
  }
  function toggleShuffle() { if (sessionReady) runAction(["shuffle", "toggle"]) }
  function cycleRepeat() { if (sessionReady) runAction(["repeat", "cycle"]) }
  function toggleMono() { if (sessionReady) runAction(["mono", "toggle"]) }
  function nextVisualizer() {
    panelVisualizerIndex = (panelVisualizerIndex + 1) % panelVisualizers.length
  }
  function selectPanelVisualizer(name) {
    var requested = String(name || "").trim().toLowerCase()
    for (var i = 0; i < panelVisualizers.length; ++i) {
      if (panelVisualizers[i].toLowerCase() === requested) {
        panelVisualizerIndex = i
        return panelVisualizers[i]
      }
    }
    return "invalid visualizer"
  }
  function setSpeed(value) { if (sessionReady) runAction(["speed", String(value)]) }
  function setEqPreset(value) { if (sessionReady) runAction(["eq", String(value)]) }
  function seekTo(seconds) {
    if (sessionReady && durationSeconds > 0)
      runAction(["seek", String(Math.max(0, Math.min(durationSeconds, seconds)) - positionSeconds)])
  }
  function queueMedia(value) {
    var target = String(value || "").trim()
    if (!target) return
    if (!sessionReady) {
      queuedUrl = target
      pendingAfterStart = {
        kind: "queueMutation", request: { cmd: "track.queue", track: { path: target } },
        fallback: ["queue", target]
      }
      startOwnedDaemon()
      delayedSelection.restart()
      return
    }
    enqueueIpc("queueMutation", { cmd: "track.queue", track: { path: target } }, ["queue", target])
  }

  function selectStation(station) {
    if (!station || !station.url) return
    loadedProviderPlaylistId = ""
    errorText = ""
    providerError = ""
    if (!sessionReady) {
      queuedUrl = station.url
      pendingAfterStart = { kind: "play", request: { cmd: "track.play", track: {
        title: station.name + " Stream", path: station.url, stream: true
      } }, fallback: [] }
      startOwnedDaemon()
      delayedSelection.restart()
      return
    }
    enqueueIpc("queueMutation", { cmd: "queue.clear" }, [])
    enqueueIpc("play", { cmd: "track.play", track: {
      title: station.name + " Stream", path: station.url, stream: true
    } }, [])
  }

  function togglePanel() {
    if (!panelLoader.item || !panelLoader.item.toggle) return
    var opening = panelLoader.item.opened !== true
    panelLoader.item.toggle()
    if (opening && panelLoader.item.libraryTab === "favorites") showFavorites()
  }
  function openTab(tab) {
    var target = panelLoader.item
    if (!target) return "unavailable"
    var requested = String(tab || "favorites")
    requested = requested === "radio" || requested === "browse" ? "providers" : requested
    if (["favorites", "providers", "queue", "files", "more"].indexOf(requested) < 0)
      return "invalid tab"
    target.libraryTab = requested
    if (target.libraryTab === "favorites") showFavorites()
    else if (target.libraryTab === "providers" && !providers.length) refreshProviders()
    else if (target.libraryTab === "queue") refreshQueue()
    else if (target.libraryTab === "more") {
      refreshHistory()
      refreshLyrics()
      refreshDevices()
    }
    target.open()
    return "ok"
  }
  function open() {
    if (panelLoader.item && panelLoader.item.openFromHotkey) {
      panelLoader.item.openFromHotkey()
      if (panelLoader.item.libraryTab === "favorites") showFavorites()
    }
  }
  function close() { if (panelLoader.item && panelLoader.item.close) panelLoader.item.close() }
  function closeForPopoutSwitch() {
    if (panelLoader.item && panelLoader.item.closeForPopoutSwitch) panelLoader.item.closeForPopoutSwitch()
  }

  readonly property bool opened: panelLoader.item ? panelLoader.item.opened === true : false
  readonly property bool popoutSwitchClosing: panelLoader.item
    ? panelLoader.item.popoutSwitchClosing === true : false

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight
  onBarChanged: injectPanel()
  onSettingsChanged: injectPanel()

  BandStream { id: bandStream; enabled: root.sessionReady; fps: 30 }

  Loader {
    id: panelLoader
    active: true
    // Keep this query aligned with manifest.json so Qt drops stale panel components on updates.
    source: Qt.resolvedUrl("Panel.qml") + "?v=1.1.0"
    visible: false
    onLoaded: {
      root.injectPanel()
      Qt.callLater(root.injectPanel)
    }
  }

  WidgetButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: "radio"
    active: root.opened
    horizontalMargin: 9
    keepSpace: true
    labelVisible: false
    fixedWidth: barContent.implicitWidth + Style.space(18)
    tooltipText: root.sessionReady
      ? ((root.playing ? "Playing " : "Paused ") + (root.trackTitle || "CLIAMP")
        + " · left: panel · middle: play/pause · right: next")
      : "CLIAMPed is starting…"

    Row {
      id: barContent
      anchors.centerIn: parent
      spacing: Style.space(7)
      Text {
        anchors.verticalCenter: parent.verticalCenter
        text: root.playing ? "󰝚" : "󰏤"
        color: root.playing ? root.bar.barForeground : Qt.darker(root.bar.barForeground, 1.4)
        font.family: root.bar ? root.bar.fontFamily : Style.font.family
        font.pixelSize: Style.font.body
      }
      DesertVisualizer {
        anchors.verticalCenter: parent.verticalCenter
        width: 34
        height: 13
        bands: root.bands
        playing: root.playing
        mode: root.panelVisualizerIndex
        compact: true
        turquoise: Color.accent
        sand: root.bar.barForeground
        adobe: root.bar.urgent
        sky: root.bar ? root.bar.background : Color.background
        onCycleRequested: root.nextVisualizer()
      }
      Text {
        anchors.verticalCenter: parent.verticalCenter
        text: root.selectedStation || root.trackTitle || "Radio"
        color: root.bar.barForeground
        font.family: root.bar ? root.bar.fontFamily : Style.font.family
        font.pixelSize: Style.font.body
        elide: Text.ElideRight
        maximumLineCount: 1
        width: Math.min(implicitWidth, 120)
      }
    }

    onPressed: function(b) {
      if (b === Qt.MiddleButton) root.togglePlayback()
      else if (b === Qt.RightButton) root.next()
      else root.togglePanel()
    }

    // The bar adds its own left-button gesture layer for module reordering.
    // Keep middle-button playback independent of that layered dispatch so it
    // remains reliable as the bar's pointer handling evolves.
    MouseArea {
      anchors.fill: parent
      acceptedButtons: Qt.MiddleButton
      cursorShape: Qt.PointingHandCursor
      onPressed: root.togglePlayback()
    }
  }

  IpcHandler {
    target: root.moduleName

    function open(): void { root.open() }
    function close(): void { root.close() }
    function show(): void { root.open() }
    function hide(): void { root.close() }
    function toggle(): void { root.togglePanel() }
    function tab(name: string): string { return root.openTab(name) }
    function visualizer(name: string): string { return root.selectPanelVisualizer(name) }
  }

  Process {
    id: statusProbe
    command: ["cliamp", "status", "--json"]
    stdout: StdioCollector { id: statusOut; waitForEnd: true }
    onExited: function(exitCode) {
      root.probeInFlight = false
      var ok = exitCode === 0 && root.parseStatus(statusOut.text)
      if (!ok) {
        root.sessionReady = false
        root.modeKnown = false
        if (!daemon.running && !root.startingDaemon) root.startOwnedDaemon()
      }
    }
  }

  Process {
    id: providerProcess
    command: ["true"]
    stdout: StdioCollector { id: providerOut; waitForEnd: true }
    stderr: StdioCollector { id: providerErr; waitForEnd: true }
    onExited: function(exitCode) {
      var current = root.ipcCurrent
      var kind = current ? current.kind : root.providerRequestKind
      var response = null
      try { response = JSON.parse(String(providerOut.text || "")) } catch (e) {}
      if (exitCode !== 0 || !response || !response.ok) {
        var message = response && response.error
          ? String(response.error) : String(providerErr.text || "IPC request failed.").trim()
        if (kind === "status") {
          root.probeInFlight = false
          if (!statusProbe.running) statusProbe.running = true
        } else if (current && current.fallback && current.fallback.length) {
          root.runFallback(current.fallback)
        } else if (kind === "providers" || kind === "playlists" || kind === "catalog"
            || kind === "favorite" || kind === "search" || kind === "load" || kind === "urlLoad") {
          root.providerError = message
        } else {
          root.errorText = message
        }
        root.finishIpc()
        return
      }
      if (kind === "status") {
        root.probeInFlight = false
        root.parseStatus(JSON.stringify(response))
      } else if (kind === "providers") {
        root.providerError = ""
        root.providers = response.providers || []
        if (root.providers.length) {
          var nextProvider = root.providers[0].key
          for (var providerIndex = 0; providerIndex < root.providers.length; ++providerIndex) {
            if (root.providers[providerIndex].key === root.selectedProviderKey) {
              nextProvider = root.selectedProviderKey
              break
            }
          }
          // Always refresh the selected source's collections after listing
          // providers. This also repairs state after a shell/plugin reload.
          root.selectProvider(nextProvider)
        }
      } else if (kind === "playlists") {
        root.providerError = ""
        root.providerPlaylists = response.playlists || []
        if (root.selectedProviderKey === "radio") {
          root.radioCatalogOffset = root.catalogSize(root.providerPlaylists)
          if (root.radioCatalogOffset === 0 && !root.radioCatalogRequested)
            root.loadRadioCatalog()
        }
      } else if (kind === "catalog") {
        root.providerError = ""
        root.providerPlaylists = response.playlists || []
        var added = Number(response.total || 0)
        root.radioCatalogOffset = root.catalogSize(root.providerPlaylists)
        root.radioCatalogHasMore = added > 0
      } else if (kind === "favorite") {
        root.providerError = ""
        root.runProviderRequest("playlists", { cmd: "provider.playlists", provider: "radio" })
      } else if (kind === "search") {
        root.providerError = ""
        root.providerResults = response.tracks || []
      } else if (kind === "load") {
        root.providerError = ""
        root.loadedProviderPlaylistId = current && current.request
          ? String(current.request.playlist || "") : ""
        var loadedTracks = response.tracks || []
        var radioIndex = loadedTracks.length === 1
          && /\.m3u8?(?:$|\?)/i.test(String(loadedTracks[0].path || ""))
          && root.selectedProviderKey === "radio"
        if (radioIndex) {
          root.queueTracks = []
        } else {
          root.queueTracks = loadedTracks
        }
        root.currentIndex = root.queueTracks.length ? 0 : -1
        if (radioIndex) {
          root.enqueueIpc("queueMutation", { cmd: "queue.clear" }, [])
          root.enqueueIpc("urlLoad", { cmd: "url.load", path: loadedTracks[0].path }, [])
        } else if (root.queueTracks.length) {
          root.enqueueIpc("action", { cmd: "play" }, ["play"])
        }
        actionRefresh.restart()
      } else if (kind === "urlLoad") {
        root.providerError = ""
        root.queueTracks = response.tracks || []
        root.currentIndex = root.queueTracks.length ? 0 : -1
        actionRefresh.restart()
      } else if (kind === "queueList" || kind === "queueMutation") {
        root.queueTracks = response.tracks || []
        if (response.index !== undefined) root.currentIndex = Number(response.index)
      } else if (kind === "play") {
        if (response.tracks) root.queueTracks = response.tracks
        if (response.index !== undefined) root.currentIndex = Number(response.index)
        actionRefresh.restart()
      } else if (kind === "history") {
        root.historyItems = response.history || []
      } else if (kind === "lyrics") {
        root.lyricLines = response.lyrics || []
      } else if (kind === "devices") {
        root.audioDevices = response.devices || []
      } else if (kind === "action" || kind === "deviceSet" || kind === "volumeAction") {
        if (kind === "volumeAction") {
          root.volumeDirty = false
          for (var i = 0; i < root.ipcQueue.length; ++i)
            if (root.ipcQueue[i].kind === "volumeAction") root.volumeDirty = true
        }
        actionRefresh.restart()
      }
      if (kind === "load" || kind === "play" || kind === "queueMutation") queueRefresh.restart()
      root.finishIpc()
    }
  }

  Process {
    id: daemon
    command: ["cliamp", "--daemon", "--provider", "radio"]
    running: false
    onExited: function(exitCode) {
      root.sessionReady = false
      root.sessionMode = "unknown"
      root.modeKnown = false
      root.startingDaemon = false
      if (root.ownsDaemon) {
        root.ownsDaemon = false
        root.errorText = exitCode === 0 ? "CLIAMP stopped." : "CLIAMP daemon exited unexpectedly."
      }
    }
  }

  Process {
    id: searchFavoriteProcess
    command: ["true"]
    stdout: StdioCollector { id: searchFavoriteOut; waitForEnd: true }
    stderr: StdioCollector { id: searchFavoriteErr; waitForEnd: true }
    onExited: function(exitCode) {
      root.searchFavoritesBusy = false
      try {
        var response = JSON.parse(String(searchFavoriteOut.text || ""))
        if (exitCode === 0 && response && response.ok) {
          root.searchFavorites = response.favorites || []
          return
        }
        root.providerError = String(response.error || searchFavoriteErr.text || "Could not update favorite.")
      } catch (e) {
        root.providerError = String(searchFavoriteErr.text || "Could not update favorite.")
      }
    }
  }

  Process {
    id: modeProbe
    command: ["bash", String(Qt.resolvedUrl("cliamp-session-mode.sh")).replace(/^file:\/\//, "")]
    stdout: StdioCollector { id: modeOut; waitForEnd: true }
    onExited: function(exitCode) {
      var detected = String(modeOut.text || "").trim()
      if (exitCode === 0 && (detected === "headless" || detected === "tui")) {
        root.sessionMode = detected
        root.modeKnown = true
      }
    }
  }

  function runFallback(args) {
    if (fallbackAction.running || !args || !args.length) return false
    fallbackAction.command = ["cliamp"].concat(args)
    fallbackAction.running = true
    return true
  }

  Process {
    id: fallbackAction
    command: ["cliamp", "status", "--json"]
    onExited: function(exitCode) {
      if (exitCode !== 0) root.errorText = "CLIAMP did not accept that control."
      actionRefresh.restart()
    }
  }

  Timer { id: startupProbe; interval: 700; onTriggered: root.probe() }
  Timer {
    id: delayedSelection
    interval: 900
    onTriggered: {
      if (root.sessionReady && root.pendingAfterStart) {
        var pending = root.pendingAfterStart
        root.pendingAfterStart = null
        root.enqueueIpc(pending.kind, pending.request, pending.fallback)
      }
      else if (root.pendingAfterStart && root.startupAttempts < 12) {
        root.startupAttempts += 1
        delayedSelection.restart()
      } else if (root.pendingAfterStart) {
        root.pendingAfterStart = null
        root.startingDaemon = false
        root.errorText = "CLIAMP did not become ready. Start CLIAMP and try again."
      }
    }
  }
  Timer { id: actionRefresh; interval: 250; onTriggered: root.probe() }
  Timer {
    id: volumeCommit
    interval: 90
    onTriggered: {
      var delta = root.pendingVolumeDelta
      root.pendingVolumeDelta = 0
      root.enqueueVolume(root.volumeDb, delta)
    }
  }
  Timer { id: queueRefresh; interval: 350; onTriggered: root.refreshQueue() }
  Timer {
    interval: 2200
    running: true
    repeat: true
    triggeredOnStart: true
    onTriggered: root.probe()
  }
  Timer {
    interval: 250
    running: root.playing && root.durationSeconds > 0
    repeat: true
    onTriggered: root.positionSeconds = Math.min(root.durationSeconds, root.positionSeconds + interval / 1000)
  }
  Component.onCompleted: refreshSearchFavorites()
}
