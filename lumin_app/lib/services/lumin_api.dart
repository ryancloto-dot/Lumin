import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;

import '../models/budget.dart';
import '../models/chat.dart';
import '../models/desktop.dart';
import '../models/request.dart';
import '../models/stats.dart';

class LuminApi {
  LuminApi({
    required this.baseUrl,
    this.apiKey = '',
    this.mobileToken = '',
    http.Client? client,
  }) : _client = client ?? http.Client();

  final String baseUrl;
  final String apiKey;
  final String mobileToken;
  final http.Client _client;

  Uri _uri(String path) => Uri.parse('${baseUrl.replaceAll(RegExp(r'/$'), '')}$path');

  Map<String, String> _headers({
    bool requireAdminKey = false,
    bool json = false,
  }) {
    final headers = <String, String>{};
    if (requireAdminKey) {
      headers['X-Lumin-Key'] = apiKey;
    } else if (mobileToken.isNotEmpty) {
      headers['X-Lumin-Mobile-Token'] = mobileToken;
    } else if (apiKey.isNotEmpty) {
      headers['X-Lumin-Key'] = apiKey;
    }
    if (json) {
      headers['Content-Type'] = 'application/json';
    }
    return headers;
  }

  Future<Map<String, dynamic>> _getJson(String path, {bool requireAdminKey = false}) async {
    Object? lastError;
    for (var attempt = 0; attempt < 3; attempt++) {
      try {
        final response = await _client.get(
          _uri(path),
          headers: _headers(requireAdminKey: requireAdminKey),
        ).timeout(const Duration(seconds: 5));
        if (response.statusCode >= 400) {
          throw Exception('HTTP ${response.statusCode}');
        }
        return jsonDecode(response.body) as Map<String, dynamic>;
      } catch (error) {
        lastError = error;
        if (attempt < 2) {
          await Future<void>.delayed(Duration(milliseconds: 350 * (attempt + 1)));
        }
      }
    }
    throw Exception('Lumin request failed: $lastError');
  }

  Future<List<dynamic>> _getJsonList(String path, {bool requireAdminKey = false}) async {
    Object? lastError;
    for (var attempt = 0; attempt < 3; attempt++) {
      try {
        final response = await _client.get(
          _uri(path),
          headers: _headers(requireAdminKey: requireAdminKey),
        ).timeout(const Duration(seconds: 5));
        if (response.statusCode >= 400) {
          throw Exception('HTTP ${response.statusCode}');
        }
        return jsonDecode(response.body) as List<dynamic>;
      } catch (error) {
        lastError = error;
        if (attempt < 2) {
          await Future<void>.delayed(Duration(milliseconds: 350 * (attempt + 1)));
        }
      }
    }
    throw Exception('Lumin request failed: $lastError');
  }

  Future<Map<String, dynamic>> _postJson(
    String path,
    Map<String, dynamic> body, {
    bool requireAdminKey = false,
    Duration timeout = const Duration(seconds: 5),
  }) async {
    Object? lastError;
    for (var attempt = 0; attempt < 3; attempt++) {
      try {
        final response = await _client
            .post(
              _uri(path),
              headers: _headers(requireAdminKey: requireAdminKey, json: true),
              body: jsonEncode(body),
            )
            .timeout(timeout);
        if (response.statusCode >= 400) {
          throw Exception('HTTP ${response.statusCode}');
        }
        return jsonDecode(response.body) as Map<String, dynamic>;
      } catch (error) {
        lastError = error;
        if (attempt < 2) {
          await Future<void>.delayed(Duration(milliseconds: 350 * (attempt + 1)));
        }
      }
    }
    throw Exception('Lumin request failed: $lastError');
  }

  Future<Stats> getStats() async => Stats.fromJson(await _getJson('/api/stats'));

  Future<List<LuminRequest>> getRequests({int limit = 50}) async {
    final payload = await _getJsonList('/api/requests?limit=$limit');
    return payload
        .whereType<Map<String, dynamic>>()
        .map(LuminRequest.fromJson)
        .toList(growable: false);
  }

  Future<Budget> getBudget() async => Budget.fromJson(await _getJson('/api/budget'));

  Future<PairingSession> pairDevice({required String deviceName}) async {
    final codePayload = await _postJson(
      '/api/pairing/code',
      const <String, dynamic>{},
      requireAdminKey: true,
    );
    final code = codePayload['code'] as String? ?? '';
    if (code.isEmpty) {
      throw Exception('Pairing code was not returned by Lumin.');
    }

    final claimPayload = await _postJson(
      '/api/pairing/claim',
      {
        'code': code,
        'device_name': deviceName,
      },
      requireAdminKey: false,
      timeout: const Duration(seconds: 10),
    );
    return PairingSession.fromJson(claimPayload);
  }

  Future<List<DesktopAgent>> getDesktopAgents() async {
    final payload = await _getJsonList('/api/desktop/agents');
    return payload
        .whereType<Map<String, dynamic>>()
        .map(DesktopAgent.fromJson)
        .toList(growable: false);
  }

  Future<List<RemoteTask>> getTasks({int limit = 20}) async {
    final payload = await _getJsonList('/api/tasks?limit=$limit');
    return payload
        .whereType<Map<String, dynamic>>()
        .map(RemoteTask.fromJson)
        .toList(growable: false);
  }

  Future<List<AgentPreset>> getAgentPresets() async {
    final payload = await _getJsonList('/api/settings/agent-presets', requireAdminKey: true);
    return payload
        .whereType<Map<String, dynamic>>()
        .map(AgentPreset.fromJson)
        .toList(growable: false);
  }

  Future<AgentPreset> importAgentPreset({
    required String presetName,
    required String sourcePath,
    String applyToGroup = 'main',
  }) async {
    final payload = await _postJson(
      '/api/settings/agent-presets/import',
      {
        'preset_name': presetName,
        'source_path': sourcePath,
        'apply_to_group': applyToGroup,
      },
      requireAdminKey: true,
      timeout: const Duration(seconds: 20),
    );
    return AgentPreset.fromJson(payload);
  }

  Future<AgentPreset> applyAgentPreset({
    required String presetName,
    required String groupId,
  }) async {
    final payload = await _postJson(
      '/api/settings/agent-presets/${Uri.encodeComponent(presetName)}/apply',
      {'group_id': groupId},
      requireAdminKey: true,
      timeout: const Duration(seconds: 15),
    );
    return AgentPreset.fromJson(payload);
  }

  Future<List<ConnectorConfig>> getConnectors() async {
    final payload = await _getJsonList('/api/settings/connectors', requireAdminKey: true);
    return payload
        .whereType<Map<String, dynamic>>()
        .map(ConnectorConfig.fromJson)
        .toList(growable: false);
  }

  Future<ConnectorConfig> saveConnector({
    required String connectorType,
    required String displayName,
    required Map<String, String> config,
  }) async {
    final payload = await _postJson(
      '/api/settings/connectors',
      {
        'connector_type': connectorType,
        'display_name': displayName,
        'config': config,
      },
      requireAdminKey: true,
      timeout: const Duration(seconds: 15),
    );
    return ConnectorConfig.fromJson(payload);
  }

  Future<void> deleteConnector(String connectorType) async {
    final response = await _client.delete(
      _uri('/api/settings/connectors/${Uri.encodeComponent(connectorType)}'),
      headers: _headers(requireAdminKey: true),
    ).timeout(const Duration(seconds: 10));
    if (response.statusCode >= 400) {
      throw Exception('HTTP ${response.statusCode}');
    }
  }

  Future<ChatResponse> sendMessage(
    String message, {
    String? groupId,
    String? contextId,
  }) async {
    final payload = await _postJson('/api/chat', {
      'message': message,
      if (groupId != null && groupId.isNotEmpty) 'group_id': groupId,
      if (contextId != null && contextId.isNotEmpty) 'context_id': contextId,
    }, timeout: const Duration(seconds: 90));
    return ChatResponse.fromJson(payload);
  }

  Future<bool> testConnection() async {
    try {
      await getStats();
      return true;
    } catch (_) {
      return false;
    }
  }
}
