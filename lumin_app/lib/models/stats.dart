class CompressionBreakdown {
  const CompressionBreakdown({
    required this.basicSavingsPct,
    required this.semanticSavingsPct,
    required this.cacheHits,
    required this.transpileSaves,
  });

  final double basicSavingsPct;
  final double semanticSavingsPct;
  final int cacheHits;
  final int transpileSaves;

  factory CompressionBreakdown.fromJson(Map<String, dynamic> json) {
    return CompressionBreakdown(
      basicSavingsPct: (json['basic_savings_pct'] as num?)?.toDouble() ?? 0,
      semanticSavingsPct: (json['semantic_savings_pct'] as num?)?.toDouble() ?? 0,
      cacheHits: (json['cache_hits'] as num?)?.toInt() ?? 0,
      transpileSaves: (json['transpile_saves'] as num?)?.toInt() ?? 0,
    );
  }
}

class Stats {
  const Stats({
    required this.totalRequests,
    required this.totalSavedTokens,
    required this.totalSavedDollars,
    required this.totalSpentDollars,
    required this.wouldHaveSpentDollars,
    required this.avgSavingsPct,
    required this.weightedSavingsPct,
    required this.cacheHitRate,
    required this.requestsToday,
    required this.savedToday,
    required this.topModelUsed,
    required this.compressionBreakdown,
  });

  final int totalRequests;
  final int totalSavedTokens;
  final double totalSavedDollars;
  final double totalSpentDollars;
  final double wouldHaveSpentDollars;
  final double avgSavingsPct;
  final double weightedSavingsPct;
  final double cacheHitRate;
  final int requestsToday;
  final double savedToday;
  final String topModelUsed;
  final CompressionBreakdown compressionBreakdown;

  factory Stats.empty() {
    return Stats(
      totalRequests: 0,
      totalSavedTokens: 0,
      totalSavedDollars: 0,
      totalSpentDollars: 0,
      wouldHaveSpentDollars: 0,
      avgSavingsPct: 0,
      weightedSavingsPct: 0,
      cacheHitRate: 0,
      requestsToday: 0,
      savedToday: 0,
      topModelUsed: 'none',
      compressionBreakdown: const CompressionBreakdown(
        basicSavingsPct: 0,
        semanticSavingsPct: 0,
        cacheHits: 0,
        transpileSaves: 0,
      ),
    );
  }

  factory Stats.fromJson(Map<String, dynamic> json) {
    return Stats(
      totalRequests: (json['total_requests'] as num?)?.toInt() ?? 0,
      totalSavedTokens: (json['total_saved_tokens'] as num?)?.toInt() ?? 0,
      totalSavedDollars: (json['total_saved_dollars'] as num?)?.toDouble() ?? 0,
      totalSpentDollars: (json['total_spent_dollars'] as num?)?.toDouble() ?? 0,
      wouldHaveSpentDollars: (json['would_have_spent_dollars'] as num?)?.toDouble() ?? 0,
      avgSavingsPct: (json['avg_savings_pct'] as num?)?.toDouble() ?? 0,
      weightedSavingsPct: (json['weighted_savings_pct'] as num?)?.toDouble() ?? 0,
      cacheHitRate: (json['cache_hit_rate'] as num?)?.toDouble() ?? 0,
      requestsToday: (json['requests_today'] as num?)?.toInt() ?? 0,
      savedToday: (json['saved_today'] as num?)?.toDouble() ?? 0,
      topModelUsed: json['top_model_used'] as String? ?? 'none',
      compressionBreakdown: CompressionBreakdown.fromJson(
        (json['compression_breakdown'] as Map<String, dynamic>?) ?? const <String, dynamic>{},
      ),
    );
  }
}
