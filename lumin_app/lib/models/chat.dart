class ChatSavings {
  const ChatSavings({
    required this.tokensSaved,
    required this.dollarsSaved,
    required this.savingsPct,
    required this.contextCompressed,
  });

  final int tokensSaved;
  final double dollarsSaved;
  final double savingsPct;
  final bool contextCompressed;

  factory ChatSavings.fromJson(Map<String, dynamic> json) {
    return ChatSavings(
      tokensSaved: (json['tokens_saved'] as num?)?.toInt() ?? 0,
      dollarsSaved: (json['dollars_saved'] as num?)?.toDouble() ?? 0,
      savingsPct: (json['savings_pct'] as num?)?.toDouble() ?? 0,
      contextCompressed: json['context_compressed'] as bool? ?? false,
    );
  }
}

class ChatMessageItem {
  const ChatMessageItem({
    required this.id,
    required this.text,
    required this.isUser,
    required this.timestamp,
    this.modelUsed,
    this.savings,
  });

  final String id;
  final String text;
  final bool isUser;
  final DateTime timestamp;
  final String? modelUsed;
  final ChatSavings? savings;
}

class ChatResponse {
  const ChatResponse({
    required this.response,
    required this.savings,
    required this.modelUsed,
    required this.latencyMs,
  });

  final String response;
  final ChatSavings savings;
  final String modelUsed;
  final int latencyMs;

  factory ChatResponse.fromJson(Map<String, dynamic> json) {
    return ChatResponse(
      response: json['response'] as String? ?? '',
      savings: ChatSavings.fromJson(
        (json['savings'] as Map<String, dynamic>?) ?? const <String, dynamic>{},
      ),
      modelUsed: json['model_used'] as String? ?? 'unknown',
      latencyMs: (json['latency_ms'] as num?)?.toInt() ?? 0,
    );
  }
}
