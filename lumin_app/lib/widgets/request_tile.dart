import 'package:flutter/material.dart';
import 'package:intl/intl.dart';

import '../models/request.dart';
import '../theme.dart';

class RequestTile extends StatelessWidget {
  const RequestTile({
    super.key,
    required this.request,
    this.compact = false,
  });

  final LuminRequest request;
  final bool compact;

  String _currency(double value) => NumberFormat.currency(symbol: '\$', decimalDigits: 4).format(value);

  @override
  Widget build(BuildContext context) {
    return AnimatedContainer(
      duration: const Duration(milliseconds: 280),
      curve: Curves.easeOut,
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: LuminColors.card,
        borderRadius: BorderRadius.circular(20),
        border: Border.all(color: LuminColors.border),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(
                  request.modelUsed,
                  style: Theme.of(context).textTheme.titleMedium,
                ),
              ),
              Text(request.relativeTime, style: Theme.of(context).textTheme.bodySmall),
            ],
          ),
          const SizedBox(height: 10),
          Row(
            children: [
              _Badge(label: '${request.savingsPct.toStringAsFixed(1)}% saved', color: LuminColors.primary),
              const SizedBox(width: 8),
              _Badge(label: request.compressionTier, color: LuminColors.accent),
              const SizedBox(width: 8),
              _Badge(label: request.cacheHit ? 'cache hit' : 'cache miss', color: request.cacheHit ? LuminColors.primary : LuminColors.border),
            ],
          ),
          const SizedBox(height: 12),
          Text(
            '${NumberFormat.decimalPattern().format(request.originalTokens)} → ${NumberFormat.decimalPattern().format(request.sentTokens)} tokens',
            style: Theme.of(context).textTheme.bodyMedium,
          ),
          if (!compact) ...[
            const SizedBox(height: 6),
            Text(
              'Saved ${_currency(request.savedDollars)} · ${request.routingReason} · ${request.latencyMs} ms',
              style: Theme.of(context).textTheme.bodySmall,
            ),
          ],
        ],
      ),
    );
  }
}

class _Badge extends StatelessWidget {
  const _Badge({
    required this.label,
    required this.color,
  });

  final String label;
  final Color color;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.14),
        borderRadius: BorderRadius.circular(999),
        border: Border.all(color: color.withValues(alpha: 0.35)),
      ),
      child: Text(
        label,
        style: Theme.of(context).textTheme.bodySmall?.copyWith(color: LuminColors.text),
      ),
    );
  }
}
