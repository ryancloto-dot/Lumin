class Budget {
  const Budget({
    required this.dailyLimit,
    required this.dailySpent,
    required this.dailyRemaining,
    required this.monthlyLimit,
    required this.monthlySpent,
    required this.monthlyRemaining,
    required this.burnRatePerHour,
    required this.projectedDailyTotal,
    required this.alertThresholdPct,
  });

  final double dailyLimit;
  final double dailySpent;
  final double dailyRemaining;
  final double monthlyLimit;
  final double monthlySpent;
  final double monthlyRemaining;
  final double burnRatePerHour;
  final double projectedDailyTotal;
  final double alertThresholdPct;

  factory Budget.empty() {
    return const Budget(
      dailyLimit: 10,
      dailySpent: 0,
      dailyRemaining: 10,
      monthlyLimit: 100,
      monthlySpent: 0,
      monthlyRemaining: 100,
      burnRatePerHour: 0,
      projectedDailyTotal: 0,
      alertThresholdPct: 0.8,
    );
  }

  factory Budget.fromJson(Map<String, dynamic> json) {
    return Budget(
      dailyLimit: (json['daily_limit'] as num?)?.toDouble() ?? 0,
      dailySpent: (json['daily_spent'] as num?)?.toDouble() ?? 0,
      dailyRemaining: (json['daily_remaining'] as num?)?.toDouble() ?? 0,
      monthlyLimit: (json['monthly_limit'] as num?)?.toDouble() ?? 0,
      monthlySpent: (json['monthly_spent'] as num?)?.toDouble() ?? 0,
      monthlyRemaining: (json['monthly_remaining'] as num?)?.toDouble() ?? 0,
      burnRatePerHour: (json['burn_rate_per_hour'] as num?)?.toDouble() ?? 0,
      projectedDailyTotal: (json['projected_daily_total'] as num?)?.toDouble() ?? 0,
      alertThresholdPct: (json['alert_threshold_pct'] as num?)?.toDouble() ?? 0.8,
    );
  }
}
