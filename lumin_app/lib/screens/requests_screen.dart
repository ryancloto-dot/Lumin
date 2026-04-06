import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../providers/lumin_provider.dart';
import '../theme.dart';
import '../widgets/request_tile.dart';

class RequestsScreen extends StatefulWidget {
  const RequestsScreen({super.key});

  @override
  State<RequestsScreen> createState() => _RequestsScreenState();
}

class _RequestsScreenState extends State<RequestsScreen> {
  String _modelFilter = 'all';
  String _tierFilter = 'all';
  String _cacheFilter = 'all';

  @override
  Widget build(BuildContext context) {
    return Consumer<LuminProvider>(
      builder: (context, provider, _) {
        final models = {'all', ...provider.requests.map((item) => item.modelUsed)};
        final requests = provider.requests.where((request) {
          if (_modelFilter != 'all' && request.modelUsed != _modelFilter) {
            return false;
          }
          if (_tierFilter != 'all' && request.compressionTier != _tierFilter) {
            return false;
          }
          if (_cacheFilter == 'hit' && !request.cacheHit) {
            return false;
          }
          if (_cacheFilter == 'miss' && request.cacheHit) {
            return false;
          }
          return true;
        }).toList();

        return Scaffold(
          appBar: AppBar(title: const Text('Requests')),
          body: RefreshIndicator(
            onRefresh: provider.refreshAll,
            child: ListView(
              padding: const EdgeInsets.all(20),
              children: [
                Wrap(
                  spacing: 10,
                  runSpacing: 10,
                  children: [
                    _FilterDropdown(
                      label: 'Model',
                      value: _modelFilter,
                      items: models.toList()..sort(),
                      onChanged: (value) => setState(() => _modelFilter = value),
                    ),
                    _FilterDropdown(
                      label: 'Tier',
                      value: _tierFilter,
                      items: const ['all', 'free'],
                      onChanged: (value) => setState(() => _tierFilter = value),
                    ),
                    _FilterDropdown(
                      label: 'Cache',
                      value: _cacheFilter,
                      items: const ['all', 'hit', 'miss'],
                      onChanged: (value) => setState(() => _cacheFilter = value),
                    ),
                  ],
                ),
                const SizedBox(height: 16),
                if (requests.isEmpty)
                  Padding(
                    padding: const EdgeInsets.only(top: 24),
                    child: Text('No matching requests.', style: Theme.of(context).textTheme.bodySmall),
                  ),
                ...requests.map(
                  (request) => Padding(
                    padding: const EdgeInsets.only(bottom: 14),
                    child: RequestTile(request: request),
                  ),
                ),
              ],
            ),
          ),
        );
      },
    );
  }
}

class _FilterDropdown extends StatelessWidget {
  const _FilterDropdown({
    required this.label,
    required this.value,
    required this.items,
    required this.onChanged,
  });

  final String label;
  final String value;
  final List<String> items;
  final ValueChanged<String> onChanged;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 14),
      decoration: BoxDecoration(
        color: LuminColors.card,
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: LuminColors.border),
      ),
      child: DropdownButton<String>(
        value: value,
        underline: const SizedBox.shrink(),
        dropdownColor: LuminColors.card,
        items: items
            .map((item) => DropdownMenuItem<String>(
                  value: item,
                  child: Text('$label: $item'),
                ))
            .toList(),
        onChanged: (next) {
          if (next != null) {
            onChanged(next);
          }
        },
      ),
    );
  }
}
