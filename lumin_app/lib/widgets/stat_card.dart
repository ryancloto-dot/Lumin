import 'package:flutter/material.dart';

import '../theme.dart';

class StatCard extends StatelessWidget {
  const StatCard({
    super.key,
    required this.title,
    required this.value,
    required this.subtitle,
    this.highlight = false,
  });

  final String title;
  final String value;
  final String subtitle;
  final bool highlight;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.all(18),
      decoration: BoxDecoration(
        color: highlight ? LuminColors.primary.withValues(alpha: 0.08) : LuminColors.card,
        borderRadius: BorderRadius.circular(22),
        border: Border.all(color: highlight ? LuminColors.primary.withValues(alpha: 0.35) : LuminColors.border),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(title.toUpperCase(), style: Theme.of(context).textTheme.bodySmall),
          const Spacer(),
          TweenAnimationBuilder<double>(
            tween: Tween<double>(begin: 0, end: 1),
            duration: const Duration(milliseconds: 700),
            builder: (context, _, child) => child!,
            child: Text(value, style: Theme.of(context).textTheme.headlineMedium),
          ),
          const SizedBox(height: 6),
          Text(subtitle, style: Theme.of(context).textTheme.bodySmall),
        ],
      ),
    );
  }
}
