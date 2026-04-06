import 'package:intl/intl.dart';

class LuminRequest {
  const LuminRequest({
    required this.id,
    required this.timestamp,
    required this.modelRequested,
    required this.modelUsed,
    required this.originalTokens,
    required this.sentTokens,
    required this.savingsPct,
    required this.savedDollars,
    required this.compressionTier,
    required this.cacheHit,
    required this.routingReason,
    required this.latencyMs,
  });

  final String id;
  final DateTime timestamp;
  final String modelRequested;
  final String modelUsed;
  final int originalTokens;
  final int sentTokens;
  final double savingsPct;
  final double savedDollars;
  final String compressionTier;
  final bool cacheHit;
  final String routingReason;
  final int latencyMs;

  factory LuminRequest.fromJson(Map<String, dynamic> json) {
    return LuminRequest(
      id: json['id'] as String? ?? '',
      timestamp: DateTime.tryParse(json['timestamp'] as String? ?? '')?.toLocal() ?? DateTime.now(),
      modelRequested: json['model_requested'] as String? ?? 'unknown',
      modelUsed: json['model_used'] as String? ?? 'unknown',
      originalTokens: (json['original_tokens'] as num?)?.toInt() ?? 0,
      sentTokens: (json['sent_tokens'] as num?)?.toInt() ?? 0,
      savingsPct: (json['savings_pct'] as num?)?.toDouble() ?? 0,
      savedDollars: (json['saved_dollars'] as num?)?.toDouble() ?? 0,
      compressionTier: json['compression_tier'] as String? ?? 'free',
      cacheHit: json['cache_hit'] as bool? ?? false,
      routingReason: json['routing_reason'] as String? ?? 'manual',
      latencyMs: (json['latency_ms'] as num?)?.toInt() ?? 0,
    );
  }

  String get relativeTime {
    final difference = DateTime.now().difference(timestamp);
    if (difference.inSeconds < 60) {
      return '${difference.inSeconds}s ago';
    }
    if (difference.inMinutes < 60) {
      return '${difference.inMinutes} mins ago';
    }
    if (difference.inHours < 24) {
      return '${difference.inHours} hrs ago';
    }
    return DateFormat('MMM d').format(timestamp);
  }
}
