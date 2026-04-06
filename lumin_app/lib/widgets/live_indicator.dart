import 'package:flutter/material.dart';

import '../theme.dart';

class LiveIndicator extends StatefulWidget {
  const LiveIndicator({
    super.key,
    required this.live,
    this.label,
  });

  final bool live;
  final String? label;

  @override
  State<LiveIndicator> createState() => _LiveIndicatorState();
}

class _LiveIndicatorState extends State<LiveIndicator> with SingleTickerProviderStateMixin {
  late final AnimationController _controller;

  @override
  void initState() {
    super.initState();
    _controller = AnimationController(vsync: this, duration: const Duration(milliseconds: 1600))
      ..repeat(reverse: false);
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        AnimatedBuilder(
          animation: _controller,
          builder: (context, _) {
            final scale = widget.live ? 1 + (_controller.value * 0.25) : 1.0;
            return Transform.scale(
              scale: scale,
              child: Container(
                width: 10,
                height: 10,
                decoration: BoxDecoration(
                  color: widget.live ? LuminColors.primary : LuminColors.muted,
                  shape: BoxShape.circle,
                  boxShadow: widget.live
                      ? [
                          BoxShadow(
                            color: LuminColors.primary.withValues(alpha: 0.35),
                            blurRadius: 14,
                            spreadRadius: 1,
                          ),
                        ]
                      : null,
                ),
              ),
            );
          },
        ),
        if (widget.label != null) ...[
          const SizedBox(width: 8),
          Text(widget.label!, style: Theme.of(context).textTheme.bodySmall),
        ],
      ],
    );
  }
}
