import 'package:fl_chart/fl_chart.dart';
import 'package:flutter/material.dart';

import '../models/request.dart';
import '../theme.dart';

class SavingsChart extends StatelessWidget {
  const SavingsChart({
    super.key,
    required this.requests,
  });

  final List<LuminRequest> requests;

  @override
  Widget build(BuildContext context) {
    final recent = requests.take(12).toList().reversed.toList();
    final spots = <FlSpot>[];
    for (var i = 0; i < recent.length; i++) {
      spots.add(FlSpot(i.toDouble(), recent[i].savedDollars));
    }
    if (spots.isEmpty) {
      spots.addAll([const FlSpot(0, 0), const FlSpot(1, 0)]);
    }

    return SizedBox(
      height: 160,
      child: LineChart(
        LineChartData(
          minY: 0,
          gridData: const FlGridData(show: false),
          borderData: FlBorderData(show: false),
          titlesData: const FlTitlesData(show: false),
          lineBarsData: [
            LineChartBarData(
              spots: spots,
              isCurved: true,
              barWidth: 3,
              color: LuminColors.primary,
              dotData: const FlDotData(show: false),
              belowBarData: BarAreaData(
                show: true,
                gradient: LinearGradient(
                  colors: [
                    LuminColors.primary.withValues(alpha: 0.26),
                    LuminColors.accent.withValues(alpha: 0.08),
                  ],
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
