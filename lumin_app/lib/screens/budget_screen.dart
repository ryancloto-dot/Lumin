import 'package:flutter/material.dart';
import 'package:intl/intl.dart';
import 'package:provider/provider.dart';

import '../providers/lumin_provider.dart';
import '../theme.dart';

class BudgetScreen extends StatelessWidget {
  const BudgetScreen({super.key});

  Color _progressColor(double ratio) {
    if (ratio >= 0.95) {
      return LuminColors.danger;
    }
    if (ratio >= 0.80) {
      return LuminColors.warning;
    }
    return LuminColors.primary;
  }

  String _currency(double value) => NumberFormat.currency(symbol: '\$', decimalDigits: 2).format(value);

  @override
  Widget build(BuildContext context) {
    return Consumer<LuminProvider>(
      builder: (context, provider, _) {
        final budget = provider.budget;
        final dailyRatio = budget.dailyLimit == 0 ? 0.0 : budget.dailySpent / budget.dailyLimit;
        final monthlyRatio = budget.monthlyLimit == 0 ? 0.0 : budget.monthlySpent / budget.monthlyLimit;

        return Scaffold(
          appBar: AppBar(title: const Text('Budget')),
          body: RefreshIndicator(
            onRefresh: provider.refreshAll,
            child: ListView(
              padding: const EdgeInsets.all(20),
              children: [
                Row(
                  children: [
                    Expanded(
                      child: _BudgetGauge(
                        title: 'Daily',
                        spent: budget.dailySpent,
                        limit: budget.dailyLimit,
                        ratio: dailyRatio,
                        color: _progressColor(dailyRatio),
                      ),
                    ),
                    const SizedBox(width: 16),
                    Expanded(
                      child: _BudgetGauge(
                        title: 'Monthly',
                        spent: budget.monthlySpent,
                        limit: budget.monthlyLimit,
                        ratio: monthlyRatio,
                        color: _progressColor(monthlyRatio),
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 18),
                _DetailCard(
                  title: 'Burn Rate',
                  rows: [
                    ('Hourly burn', _currency(budget.burnRatePerHour)),
                    ('Projected today', _currency(budget.projectedDailyTotal)),
                    ('Daily remaining', _currency(budget.dailyRemaining)),
                    ('Monthly remaining', _currency(budget.monthlyRemaining)),
                    ('Alert threshold', '${(budget.alertThresholdPct * 100).toStringAsFixed(0)}%'),
                  ],
                ),
              ],
            ),
          ),
        );
      },
    );
  }
}

class _BudgetGauge extends StatelessWidget {
  const _BudgetGauge({
    required this.title,
    required this.spent,
    required this.limit,
    required this.ratio,
    required this.color,
  });

  final String title;
  final double spent;
  final double limit;
  final double ratio;
  final Color color;

  String _currency(double value) => NumberFormat.currency(symbol: '\$', decimalDigits: 2).format(value);

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(18),
      decoration: BoxDecoration(
        color: LuminColors.card,
        borderRadius: BorderRadius.circular(24),
        border: Border.all(color: LuminColors.border),
      ),
      child: Column(
        children: [
          Text(title, style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 18),
          TweenAnimationBuilder<double>(
            tween: Tween<double>(begin: 0, end: ratio.clamp(0.0, 1.0)),
            duration: const Duration(milliseconds: 900),
            builder: (context, value, _) {
              return Stack(
                alignment: Alignment.center,
                children: [
                  SizedBox(
                    width: 136,
                    height: 136,
                    child: CircularProgressIndicator(
                      value: value,
                      strokeWidth: 12,
                      color: color,
                      backgroundColor: LuminColors.border,
                    ),
                  ),
                  Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Text(_currency(spent), style: Theme.of(context).textTheme.titleLarge),
                      Text('/ ${_currency(limit)}', style: Theme.of(context).textTheme.bodySmall),
                    ],
                  ),
                ],
              );
            },
          ),
        ],
      ),
    );
  }
}

class _DetailCard extends StatelessWidget {
  const _DetailCard({
    required this.title,
    required this.rows,
  });

  final String title;
  final List<(String, String)> rows;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(18),
      decoration: BoxDecoration(
        color: LuminColors.card,
        borderRadius: BorderRadius.circular(24),
        border: Border.all(color: LuminColors.border),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(title, style: Theme.of(context).textTheme.titleLarge),
          const SizedBox(height: 16),
          ...rows.map(
            (row) => Padding(
              padding: const EdgeInsets.only(bottom: 10),
              child: Row(
                children: [
                  Expanded(child: Text(row.$1, style: Theme.of(context).textTheme.bodySmall)),
                  Text(row.$2, style: Theme.of(context).textTheme.bodyMedium),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }
}
