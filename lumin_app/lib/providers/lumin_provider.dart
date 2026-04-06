import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../models/budget.dart';
import '../models/chat.dart';
import '../models/desktop.dart';
import '../models/request.dart';
import '../models/stats.dart';
import '../services/lumin_api.dart';
import '../services/websocket_service.dart';

class LuminProvider extends ChangeNotifier {
  static const _urlKey = 'lumin_url';
  static const _apiKeyKey = 'lumin_admin_key';
  static const _mobileTokenKey = 'lumin_mobile_token';
  static const _mobileClientIdKey = 'lumin_mobile_client_id';
  static const _refreshIntervalKey = 'lumin_refresh_interval';

  String baseUrl = '';
  String apiKey = '';
  String mobileToken = '';
  String mobileClientId = '';
  String statusMessage = 'Not connected';
  bool isLoading = false;
  bool isConfigured = false;
  bool isOnline = false;
  bool isLive = false;
  bool isPaired = false;
  int refreshIntervalSeconds = 30;
  int selectedTab = 0;

  Stats stats = Stats.empty();
  Budget budget = Budget.empty();
  List<LuminRequest> requests = const [];
  List<DesktopAgent> desktopAgents = const [];
  List<RemoteTask> remoteTasks = const [];
  List<AgentPreset> agentPresets = const [];
  List<ConnectorConfig> connectors = const [];
  List<ChatMessageItem> chatMessages = const [];
  bool isSendingChat = false;
  bool isManagingPresets = false;
  bool isManagingConnectors = false;
  String presetStatusMessage = '';
  String connectorStatusMessage = '';

  LuminApi? _api;
  WebSocketService? _websocket;
  Timer? _refreshTimer;

  Future<void> initialize() async {
    final prefs = await SharedPreferences.getInstance();
    baseUrl = prefs.getString(_urlKey) ?? '';
    apiKey = prefs.getString(_apiKeyKey) ?? '';
    mobileToken = prefs.getString(_mobileTokenKey) ?? '';
    mobileClientId = prefs.getString(_mobileClientIdKey) ?? '';
    refreshIntervalSeconds = prefs.getInt(_refreshIntervalKey) ?? 30;
    if (baseUrl.isNotEmpty && (mobileToken.isNotEmpty || apiKey.isNotEmpty)) {
      isConfigured = true;
      await connect(
        baseUrl: baseUrl,
        apiKey: apiKey,
        mobileToken: mobileToken,
        persist: false,
      );
    } else {
      notifyListeners();
    }
  }

  Future<void> connect({
    required String baseUrl,
    String apiKey = '',
    String mobileToken = '',
    String? deviceName,
    bool persist = true,
  }) async {
    isLoading = true;
    statusMessage = 'Pairing with your computer...';
    notifyListeners();

    this.baseUrl = baseUrl.trim();
    this.apiKey = apiKey.trim();
    this.mobileToken = mobileToken.trim();

    var api = LuminApi(
      baseUrl: this.baseUrl,
      apiKey: this.apiKey,
      mobileToken: this.mobileToken,
    );

    if (this.mobileToken.isEmpty && this.apiKey.isNotEmpty) {
      try {
        final pairing = await api.pairDevice(
          deviceName: deviceName ?? defaultTargetPlatform.name,
        );
        this.mobileToken = pairing.mobileToken;
        mobileClientId = pairing.clientId;
        isPaired = true;
        api = LuminApi(
          baseUrl: this.baseUrl,
          apiKey: this.apiKey,
          mobileToken: this.mobileToken,
        );
      } catch (_) {
        isLoading = false;
        isOnline = false;
        isConfigured = false;
        isPaired = false;
        statusMessage = 'Pairing failed';
        notifyListeners();
        return;
      }
    }

    _api = api;

    var ok = await _api!.testConnection();
    if (!ok && this.apiKey.isNotEmpty && this.mobileToken.isNotEmpty) {
      try {
        final pairing = await LuminApi(
          baseUrl: this.baseUrl,
          apiKey: this.apiKey,
        ).pairDevice(deviceName: deviceName ?? 'Lumin Mobile');
        this.mobileToken = pairing.mobileToken;
        mobileClientId = pairing.clientId;
        isPaired = true;
        _api = LuminApi(
          baseUrl: this.baseUrl,
          apiKey: this.apiKey,
          mobileToken: this.mobileToken,
        );
        ok = await _api!.testConnection();
      } catch (_) {
        ok = false;
      }
    }

    if (!ok) {
      isLoading = false;
      isOnline = false;
      isConfigured = false;
      isPaired = this.mobileToken.isNotEmpty;
      statusMessage = 'Offline or invalid key';
      notifyListeners();
      return;
    }

    if (persist) {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setString(_urlKey, this.baseUrl);
      await prefs.setString(_apiKeyKey, this.apiKey);
      await prefs.setString(_mobileTokenKey, this.mobileToken);
      await prefs.setString(_mobileClientIdKey, mobileClientId);
    }

    isConfigured = true;
    isOnline = true;
    isPaired = this.mobileToken.isNotEmpty;
    statusMessage = isPaired ? 'Connected to your computer' : 'Connected';
    isLoading = false;

    await refreshAll();
    _startRefreshLoop();
    _connectLiveFeed();
    notifyListeners();
  }

  Future<void> refreshAll() async {
    if (_api == null) {
      return;
    }
    try {
      final results = await Future.wait<dynamic>([
        _api!.getStats(),
        _api!.getRequests(limit: 50),
        _api!.getBudget(),
        _api!.getDesktopAgents(),
        _api!.getTasks(limit: 20),
        if (hasAdminKey) _api!.getAgentPresets(),
        if (hasAdminKey) _api!.getConnectors(),
      ]);
      stats = results[0] as Stats;
      requests = results[1] as List<LuminRequest>;
      budget = results[2] as Budget;
      desktopAgents = results[3] as List<DesktopAgent>;
      remoteTasks = results[4] as List<RemoteTask>;
      agentPresets =
          hasAdminKey
              ? (results[5] as List<AgentPreset>)
              : const [];
      connectors =
          hasAdminKey
              ? (results[hasAdminKey ? 6 : 5] as List<ConnectorConfig>)
              : const [];
      isOnline = true;
      statusMessage = isDesktopOnline
          ? (isLive ? 'Connected · Desktop live' : 'Connected · Desktop online')
          : 'Connected · Desktop offline';
    } catch (_) {
      isOnline = false;
      statusMessage = 'Offline';
    }
    notifyListeners();
  }

  void _startRefreshLoop() {
    _refreshTimer?.cancel();
    _refreshTimer = Timer.periodic(
      Duration(seconds: refreshIntervalSeconds),
      (_) => refreshAll(),
    );
  }

  void _connectLiveFeed() {
    _websocket?.dispose();
    _websocket = null;
    if (apiKey.isEmpty) {
      isLive = false;
      notifyListeners();
      return;
    }
    _websocket = WebSocketService(baseUrl: baseUrl, apiKey: apiKey)
      ..connect();
    _websocket!.stream.listen((event) async {
      if (event['type'] == 'request_complete') {
        isLive = true;
        await refreshAll();
      }
    }, onError: (_) {
      isLive = false;
      notifyListeners();
    });
    Future<void>.delayed(const Duration(milliseconds: 400), () {
      isLive = _websocket?.isConnected ?? false;
      notifyListeners();
    });
  }

  Future<void> saveSettings({
    required String baseUrl,
    required String apiKey,
    required int refreshIntervalSeconds,
  }) async {
    this.refreshIntervalSeconds = refreshIntervalSeconds;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setInt(_refreshIntervalKey, refreshIntervalSeconds);
    await connect(
      baseUrl: baseUrl,
      apiKey: apiKey,
      deviceName: 'Lumin Mobile',
    );
  }

  Future<void> disconnect() async {
    _refreshTimer?.cancel();
    _websocket?.dispose();
    _websocket = null;
    _api = null;
    isConfigured = false;
    isOnline = false;
    isLive = false;
    isPaired = false;
    mobileToken = '';
    mobileClientId = '';
    stats = Stats.empty();
    budget = Budget.empty();
    requests = const [];
    desktopAgents = const [];
    remoteTasks = const [];
    agentPresets = const [];
    connectors = const [];
    chatMessages = const [];
    presetStatusMessage = '';
    connectorStatusMessage = '';
    statusMessage = 'Disconnected';
    final prefs = await SharedPreferences.getInstance();
    await prefs.remove(_urlKey);
    await prefs.remove(_apiKeyKey);
    await prefs.remove(_mobileTokenKey);
    await prefs.remove(_mobileClientIdKey);
    notifyListeners();
  }

  void setSelectedTab(int index) {
    selectedTab = index;
    notifyListeners();
  }

  Future<void> sendChatMessage(
    String message, {
    String? groupId,
    String? contextId,
  }) async {
    if (_api == null || message.trim().isEmpty) {
      return;
    }

    final now = DateTime.now();
    final userMessage = ChatMessageItem(
      id: 'user-${now.microsecondsSinceEpoch}',
      text: message.trim(),
      isUser: true,
      timestamp: now,
    );
    chatMessages = [...chatMessages, userMessage];
    isSendingChat = true;
    notifyListeners();

    try {
      final response = await _api!.sendMessage(
        message.trim(),
        groupId: groupId,
        contextId: contextId,
      );
      final assistantMessage = ChatMessageItem(
        id: 'assistant-${DateTime.now().microsecondsSinceEpoch}',
        text: response.response,
        isUser: false,
        timestamp: DateTime.now(),
        modelUsed: response.modelUsed,
        savings: response.savings,
      );
      chatMessages = [...chatMessages, assistantMessage];
      await refreshAll();
    } catch (_) {
      final assistantMessage = ChatMessageItem(
        id: 'assistant-error-${DateTime.now().microsecondsSinceEpoch}',
        text: 'Offline. Lumin could not send that message right now.',
        isUser: false,
        timestamp: DateTime.now(),
        modelUsed: 'offline',
      );
      chatMessages = [...chatMessages, assistantMessage];
    } finally {
      isSendingChat = false;
      notifyListeners();
    }
  }

  Future<void> importAgentPreset({
    required String presetName,
    required String sourcePath,
    String applyToGroup = 'main',
  }) async {
    if (_api == null || !hasAdminKey) {
      presetStatusMessage = 'Admin key required to import presets.';
      notifyListeners();
      return;
    }
    isManagingPresets = true;
    presetStatusMessage = 'Importing preset...';
    notifyListeners();
    try {
      await _api!.importAgentPreset(
        presetName: presetName,
        sourcePath: sourcePath,
        applyToGroup: applyToGroup,
      );
      agentPresets = await _api!.getAgentPresets();
      presetStatusMessage = 'Preset imported and applied.';
    } catch (_) {
      presetStatusMessage = 'Preset import failed.';
    } finally {
      isManagingPresets = false;
      notifyListeners();
    }
  }

  Future<void> applyAgentPreset({
    required String presetName,
    required String groupId,
  }) async {
    if (_api == null || !hasAdminKey) {
      presetStatusMessage = 'Admin key required to apply presets.';
      notifyListeners();
      return;
    }
    isManagingPresets = true;
    presetStatusMessage = 'Applying preset...';
    notifyListeners();
    try {
      await _api!.applyAgentPreset(presetName: presetName, groupId: groupId);
      agentPresets = await _api!.getAgentPresets();
      presetStatusMessage = 'Preset applied.';
    } catch (_) {
      presetStatusMessage = 'Preset apply failed.';
    } finally {
      isManagingPresets = false;
      notifyListeners();
    }
  }

  Future<void> saveConnector({
    required String connectorType,
    required String displayName,
    required Map<String, String> config,
  }) async {
    if (_api == null || !hasAdminKey) {
      connectorStatusMessage = 'Admin key required to manage connectors.';
      notifyListeners();
      return;
    }
    isManagingConnectors = true;
    connectorStatusMessage = 'Saving connector...';
    notifyListeners();
    try {
      await _api!.saveConnector(
        connectorType: connectorType,
        displayName: displayName,
        config: config,
      );
      connectors = await _api!.getConnectors();
      connectorStatusMessage = 'Connector saved.';
    } catch (_) {
      connectorStatusMessage = 'Connector save failed.';
    } finally {
      isManagingConnectors = false;
      notifyListeners();
    }
  }

  Future<void> deleteConnector(String connectorType) async {
    if (_api == null || !hasAdminKey) {
      connectorStatusMessage = 'Admin key required to manage connectors.';
      notifyListeners();
      return;
    }
    isManagingConnectors = true;
    connectorStatusMessage = 'Removing connector...';
    notifyListeners();
    try {
      await _api!.deleteConnector(connectorType);
      connectors = await _api!.getConnectors();
      connectorStatusMessage = 'Connector removed.';
    } catch (_) {
      connectorStatusMessage = 'Connector remove failed.';
    } finally {
      isManagingConnectors = false;
      notifyListeners();
    }
  }

  @override
  void dispose() {
    _refreshTimer?.cancel();
    _websocket?.dispose();
    super.dispose();
  }

  bool get hasAdminKey => apiKey.isNotEmpty;

  bool get isDesktopOnline =>
      desktopAgents.any((agent) => agent.status.toLowerCase() == 'online');

  DesktopAgent? get primaryDesktop =>
      desktopAgents.isNotEmpty ? desktopAgents.first : null;
}
