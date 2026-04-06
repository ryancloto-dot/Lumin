import 'package:flutter/material.dart';
import 'package:intl/intl.dart';
import 'package:provider/provider.dart';

import '../providers/lumin_provider.dart';
import '../theme.dart';
import '../widgets/live_indicator.dart';
import '../widgets/request_tile.dart';
import '../widgets/savings_chart.dart';
import '../widgets/stat_card.dart';
import 'budget_screen.dart';
import 'chat_screen.dart';
import 'requests_screen.dart';
import 'settings_screen.dart';

class DashboardScreen extends StatelessWidget {
  const DashboardScreen({super.key});

  @override
  Widget build(BuildContext context) {
    return Consumer<LuminProvider>(
      builder: (context, provider, _) {
        final pages = [
          const _DashboardHome(),
          const ChatScreen(),
          const RequestsScreen(),
          const BudgetScreen(),
          const SettingsScreen(),
        ];

        return Scaffold(
          body: SafeArea(child: pages[provider.selectedTab]),
          bottomNavigationBar: BottomNavigationBar(
            currentIndex: provider.selectedTab,
            onTap: provider.setSelectedTab,
            items: const [
              BottomNavigationBarItem(icon: Icon(Icons.dashboard_rounded), label: 'Dashboard'),
              BottomNavigationBarItem(icon: Icon(Icons.chat_bubble_outline_rounded), label: 'Chat'),
              BottomNavigationBarItem(icon: Icon(Icons.receipt_long_rounded), label: 'Requests'),
              BottomNavigationBarItem(icon: Icon(Icons.account_balance_wallet_rounded), label: 'Budget'),
              BottomNavigationBarItem(icon: Icon(Icons.settings_rounded), label: 'Settings'),
            ],
          ),
        );
      },
    );
  }
}

class _DashboardHome extends StatelessWidget {
  const _DashboardHome();

  String _currency(double value) => NumberFormat.currency(symbol: '\$', decimalDigits: 2).format(value);

  @override
  Widget build(BuildContext context) {
    return Consumer<LuminProvider>(
      builder: (context, provider, _) {
        final stats = provider.stats;
        final latest = provider.requests.take(5).toList();
        final desktop = provider.primaryDesktop;
        final tasks = provider.remoteTasks.take(3).toList();

        return RefreshIndicator(
          onRefresh: provider.refreshAll,
          child: ListView(
            padding: const EdgeInsets.all(20),
            children: [
              Container(
                padding: const EdgeInsets.all(24),
                decoration: BoxDecoration(
                  gradient: LinearGradient(
                    colors: [
                      LuminColors.primary.withValues(alpha: 0.18),
                      LuminColors.accent.withValues(alpha: 0.2),
                    ],
                    begin: Alignment.topLeft,
                    end: Alignment.bottomRight,
                  ),
                  borderRadius: BorderRadius.circular(28),
                  border: Border.all(color: LuminColors.border),
                ),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        Image.asset(
                          'assets/lumin-mark.png',
                          width: 32,
                          height: 32,
                        ),
                        const SizedBox(width: 12),
                        _StatusChip(label: provider.isOnline ? 'Connected' : 'Offline'),
                        const SizedBox(width: 10),
                        LiveIndicator(live: provider.isLive, label: provider.isLive ? 'LIVE' : 'Polling'),
                        const Spacer(),
                        _StatusChip(
                          label: provider.isDesktopOnline
                              ? 'Desktop online'
                              : 'Desktop offline',
                        ),
                      ],
                    ),
                    const SizedBox(height: 24),
                    Text('YOU SAVED', style: Theme.of(context).textTheme.bodySmall),
                    const SizedBox(height: 8),
                    TweenAnimationBuilder<double>(
                      tween: Tween<double>(begin: 0, end: stats.savedToday),
                      duration: const Duration(milliseconds: 900),
                      builder: (context, value, _) {
                        return Text(
                          _currency(value),
                          style: Theme.of(context).textTheme.headlineLarge,
                        );
                      },
                    ),
                    const SizedBox(height: 4),
                    Text('today', style: Theme.of(context).textTheme.bodySmall),
                    const SizedBox(height: 18),
                    Text('Would have spent: ${_currency(stats.wouldHaveSpentDollars)}'),
                    const SizedBox(height: 6),
                    Text('Actually spent:   ${_currency(stats.totalSpentDollars)}'),
                    if (desktop != null) ...[
                      const SizedBox(height: 12),
                      Text(
                        'Computer: ${desktop.name} on ${desktop.hostname}',
                        style: Theme.of(context).textTheme.bodySmall,
                      ),
                    ],
                  ],
                ),
              ),
              const SizedBox(height: 18),
              GridView.count(
                crossAxisCount: 2,
                shrinkWrap: true,
                crossAxisSpacing: 14,
                mainAxisSpacing: 14,
                physics: const NeverScrollableScrollPhysics(),
                childAspectRatio: 1.25,
                children: [
                  StatCard(
                    title: 'Requests',
                    value: NumberFormat.decimalPattern().format(stats.totalRequests),
                    subtitle: 'today ${stats.requestsToday}',
                  ),
                  StatCard(
                    title: 'Avg Save',
                    value: '${stats.avgSavingsPct.toStringAsFixed(1)}%',
                    subtitle: 'all requests',
                  ),
                  StatCard(
                    title: 'Cache',
                    value: '${(stats.cacheHitRate * 100).toStringAsFixed(1)}%',
                    subtitle: 'hit rate',
                  ),
                  StatCard(
                    title: 'Routing',
                    value: 'balanced',
                    subtitle: stats.topModelUsed,
                    highlight: true,
                  ),
                ],
              ),
              const SizedBox(height: 18),
              _SectionCard(
                title: 'Savings Over Time',
                child: SavingsChart(requests: provider.requests),
              ),
              const SizedBox(height: 18),
              _SectionCard(
                title: 'Live Feed',
                child: Column(
                  children: latest.isEmpty
                      ? [
                          Padding(
                            padding: const EdgeInsets.only(top: 12),
                            child: Text('No live requests yet.', style: Theme.of(context).textTheme.bodySmall),
                          )
                        ]
                      : latest
                          .map((request) => Padding(
                                padding: const EdgeInsets.only(bottom: 12),
                                child: RequestTile(request: request, compact: true),
                              ))
                          .toList(),
                ),
              ),
              const SizedBox(height: 18),
              _SectionCard(
                title: 'Desktop Tasks',
                child: Column(
                  children: tasks.isEmpty
                      ? [
                          Padding(
                            padding: const EdgeInsets.only(top: 12),
                            child: Text(
                              'No recent desktop tasks yet.',
                              style: Theme.of(context).textTheme.bodySmall,
                            ),
                          ),
                        ]
                      : tasks
                          .map(
                            (task) => Container(
                              width: double.infinity,
                              margin: const EdgeInsets.only(bottom: 12),
                              padding: const EdgeInsets.all(14),
                              decoration: BoxDecoration(
                                color: LuminColors.background,
                                borderRadius: BorderRadius.circular(16),
                                border: Border.all(color: LuminColors.border),
                              ),
                              child: Column(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                                  Row(
                                    children: [
                                      Expanded(
                                        child: Text(
                                          task.message,
                                          maxLines: 2,
                                          overflow: TextOverflow.ellipsis,
                                        ),
                                      ),
                                      const SizedBox(width: 10),
                                      Text(
                                        task.status,
                                        style: Theme.of(context).textTheme.bodySmall?.copyWith(
                                              color: task.status == 'completed'
                                                  ? LuminColors.primary
                                                  : LuminColors.muted,
                                            ),
                                      ),
                                    ],
                                  ),
                                  const SizedBox(height: 8),
                                  Text(
                                    'Group ${task.groupId} • ${task.createdAt.hour.toString().padLeft(2, '0')}:${task.createdAt.minute.toString().padLeft(2, '0')}',
                                    style: Theme.of(context).textTheme.bodySmall,
                                  ),
                                ],
                              ),
                            ),
                          )
                          .toList(),
                ),
              ),
            ],
          ),
        );
      },
    );
  }
}

class _SectionCard extends StatelessWidget {
  const _SectionCard({
    required this.title,
    required this.child,
  });

  final String title;
  final Widget child;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: double.infinity,
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
          const SizedBox(height: 14),
          child,
        ],
      ),
    );
  }
}

class _StatusChip extends StatelessWidget {
  const _StatusChip({required this.label});

  final String label;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
      decoration: BoxDecoration(
        color: LuminColors.card,
        borderRadius: BorderRadius.circular(999),
        border: Border.all(color: LuminColors.border),
      ),
      child: Text(label, style: Theme.of(context).textTheme.bodySmall),
    );
  }
}
