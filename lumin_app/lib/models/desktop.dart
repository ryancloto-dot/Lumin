class DesktopAgent {
  const DesktopAgent({
    required this.agentId,
    required this.name,
    required this.hostname,
    required this.groupId,
    required this.status,
    required this.lastSeenAt,
  });

  final String agentId;
  final String name;
  final String hostname;
  final String groupId;
  final String status;
  final DateTime lastSeenAt;

  factory DesktopAgent.fromJson(Map<String, dynamic> json) {
    return DesktopAgent(
      agentId: json['agent_id'] as String? ?? '',
      name: json['name'] as String? ?? 'Desktop',
      hostname: json['hostname'] as String? ?? 'unknown',
      groupId: json['group_id'] as String? ?? 'main',
      status: json['status'] as String? ?? 'unknown',
      lastSeenAt:
          DateTime.tryParse(json['last_seen_at'] as String? ?? '')?.toLocal() ??
          DateTime.now(),
    );
  }
}

class RemoteTask {
  const RemoteTask({
    required this.id,
    required this.groupId,
    required this.message,
    required this.status,
    required this.createdAt,
    this.agentId,
    this.responseText,
    this.errorText,
    this.modelUsed,
    this.latencyMs = 0,
    this.metadata = const <String, dynamic>{},
  });

  final String id;
  final String groupId;
  final String message;
  final String status;
  final DateTime createdAt;
  final String? agentId;
  final String? responseText;
  final String? errorText;
  final String? modelUsed;
  final int latencyMs;
  final Map<String, dynamic> metadata;

  factory RemoteTask.fromJson(Map<String, dynamic> json) {
    return RemoteTask(
      id: json['id'] as String? ?? '',
      groupId: json['group_id'] as String? ?? 'main',
      message: json['message'] as String? ?? '',
      status: json['status'] as String? ?? 'queued',
      createdAt:
          DateTime.tryParse(json['created_at'] as String? ?? '')?.toLocal() ??
          DateTime.now(),
      agentId: json['agent_id'] as String?,
      responseText: json['response_text'] as String?,
      errorText: json['error_text'] as String?,
      modelUsed: json['model_used'] as String?,
      latencyMs: (json['latency_ms'] as num?)?.toInt() ?? 0,
      metadata:
          (json['metadata'] as Map<String, dynamic>?) ?? const <String, dynamic>{},
    );
  }
}

class AgentPreset {
  const AgentPreset({
    required this.name,
    required this.sourcePath,
    required this.importedAt,
    required this.files,
    required this.appliedGroups,
    required this.fileCount,
    required this.skillCount,
  });

  final String name;
  final String sourcePath;
  final DateTime importedAt;
  final List<String> files;
  final List<String> appliedGroups;
  final int fileCount;
  final int skillCount;

  factory AgentPreset.fromJson(Map<String, dynamic> json) {
    return AgentPreset(
      name: json['name'] as String? ?? '',
      sourcePath: json['source_path'] as String? ?? '',
      importedAt:
          DateTime.tryParse(json['imported_at'] as String? ?? '')?.toLocal() ??
          DateTime.now(),
      files:
          (json['files'] as List<dynamic>? ?? const [])
              .map((item) => item.toString())
              .toList(growable: false),
      appliedGroups:
          (json['applied_groups'] as List<dynamic>? ?? const [])
              .map((item) => item.toString())
              .toList(growable: false),
      fileCount: (json['file_count'] as num?)?.toInt() ?? 0,
      skillCount: (json['skill_count'] as num?)?.toInt() ?? 0,
    );
  }
}

class ConnectorFieldModel {
  const ConnectorFieldModel({
    required this.key,
    required this.label,
    required this.kind,
    required this.placeholder,
    required this.required,
    required this.secret,
  });

  final String key;
  final String label;
  final String kind;
  final String placeholder;
  final bool required;
  final bool secret;

  factory ConnectorFieldModel.fromJson(Map<String, dynamic> json) {
    return ConnectorFieldModel(
      key: json['key'] as String? ?? '',
      label: json['label'] as String? ?? '',
      kind: json['kind'] as String? ?? 'text',
      placeholder: json['placeholder'] as String? ?? '',
      required: json['required'] as bool? ?? false,
      secret: json['secret'] as bool? ?? false,
    );
  }
}

class ConnectorConfig {
  const ConnectorConfig({
    required this.connectorType,
    required this.name,
    required this.description,
    required this.category,
    required this.status,
    required this.displayName,
    required this.fields,
    required this.configValues,
  });

  final String connectorType;
  final String name;
  final String description;
  final String category;
  final String status;
  final String displayName;
  final List<ConnectorFieldModel> fields;
  final Map<String, String> configValues;

  bool get isConfigured => status != 'not_configured';

  factory ConnectorConfig.fromJson(Map<String, dynamic> json) {
    return ConnectorConfig(
      connectorType: json['connector_type'] as String? ?? '',
      name: json['name'] as String? ?? '',
      description: json['description'] as String? ?? '',
      category: json['category'] as String? ?? 'General',
      status: json['status'] as String? ?? 'not_configured',
      displayName: json['display_name'] as String? ?? '',
      fields:
          (json['fields'] as List<dynamic>? ?? const [])
              .whereType<Map<String, dynamic>>()
              .map(ConnectorFieldModel.fromJson)
              .toList(growable: false),
      configValues:
          ((json['config_values'] as Map<String, dynamic>?) ?? const <String, dynamic>{})
              .map((key, value) => MapEntry(key, value?.toString() ?? '')),
    );
  }
}

class PairingSession {
  const PairingSession({
    required this.clientId,
    required this.mobileToken,
  });

  final String clientId;
  final String mobileToken;

  factory PairingSession.fromJson(Map<String, dynamic> json) {
    return PairingSession(
      clientId: json['client_id'] as String? ?? '',
      mobileToken: json['mobile_token'] as String? ?? '',
    );
  }
}
