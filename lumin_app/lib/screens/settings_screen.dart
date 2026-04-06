import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../providers/lumin_provider.dart';
import '../theme.dart';

class SettingsScreen extends StatefulWidget {
  const SettingsScreen({super.key});

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  late final TextEditingController _urlController;
  late final TextEditingController _keyController;
  late final TextEditingController _presetSourceController;
  late final TextEditingController _presetNameController;
  late final TextEditingController _presetGroupController;
  late final TextEditingController _dailyBudgetController;
  late final TextEditingController _monthlyBudgetController;
  late final TextEditingController _alertThresholdController;
  late final TextEditingController _connectorDisplayNameController;
  final Map<String, TextEditingController> _connectorFieldControllers = <String, TextEditingController>{};
  int _refreshInterval = 30;
  String _selectedConnectorType = 'slack';

  @override
  void initState() {
    super.initState();
    final provider = context.read<LuminProvider>();
    _urlController = TextEditingController(text: provider.baseUrl);
    _keyController = TextEditingController(text: provider.apiKey);
    _presetSourceController = TextEditingController();
    _presetNameController = TextEditingController(text: 'openclaw-main');
    _presetGroupController = TextEditingController(text: 'main');
    _dailyBudgetController = TextEditingController(text: provider.budget.dailyLimit.toStringAsFixed(2));
    _monthlyBudgetController = TextEditingController(text: provider.budget.monthlyLimit.toStringAsFixed(2));
    _alertThresholdController = TextEditingController(text: (provider.budget.alertThresholdPct * 100).toStringAsFixed(0));
    _connectorDisplayNameController = TextEditingController();
    _refreshInterval = provider.refreshIntervalSeconds;
  }

  @override
  void dispose() {
    _urlController.dispose();
    _keyController.dispose();
    _presetSourceController.dispose();
    _presetNameController.dispose();
    _presetGroupController.dispose();
    _dailyBudgetController.dispose();
    _monthlyBudgetController.dispose();
    _alertThresholdController.dispose();
    _connectorDisplayNameController.dispose();
    for (final controller in _connectorFieldControllers.values) {
      controller.dispose();
    }
    super.dispose();
  }

  TextEditingController _connectorFieldController(String key, {String initialValue = ''}) {
    return _connectorFieldControllers.putIfAbsent(
      key,
      () => TextEditingController(text: initialValue),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Consumer<LuminProvider>(
      builder: (context, provider, _) {
        final connectorOptions = provider.connectors;
        final selectedConnector = connectorOptions.isEmpty
            ? null
            : connectorOptions.firstWhere(
                (item) => item.connectorType == _selectedConnectorType,
                orElse: () => connectorOptions.first,
              );
        if (selectedConnector != null && _selectedConnectorType != selectedConnector.connectorType) {
          _selectedConnectorType = selectedConnector.connectorType;
        }
        if (selectedConnector != null &&
            (_connectorDisplayNameController.text.isEmpty ||
                _connectorDisplayNameController.text == selectedConnector.name ||
                _connectorDisplayNameController.text == selectedConnector.displayName)) {
          _connectorDisplayNameController.text = selectedConnector.displayName.isEmpty
              ? selectedConnector.name
              : selectedConnector.displayName;
        }
        return Scaffold(
          appBar: AppBar(title: const Text('Settings')),
          body: ListView(
            padding: const EdgeInsets.all(20),
            children: [
              TextField(
                controller: _urlController,
                decoration: const InputDecoration(labelText: 'Lumin URL'),
              ),
              const SizedBox(height: 14),
              TextField(
                controller: _keyController,
                obscureText: true,
                decoration: const InputDecoration(labelText: 'Pairing/admin key'),
              ),
              const SizedBox(height: 14),
              Container(
                padding: const EdgeInsets.all(16),
                decoration: BoxDecoration(
                  color: LuminColors.card,
                  borderRadius: BorderRadius.circular(18),
                  border: Border.all(color: LuminColors.border),
                ),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text('Mobile session', style: Theme.of(context).textTheme.titleMedium),
                    const SizedBox(height: 8),
                    Text(
                      provider.isPaired
                          ? 'Paired to this computer as ${provider.mobileClientId.isEmpty ? 'mobile client' : provider.mobileClientId}'
                          : 'Not paired yet',
                    ),
                    const SizedBox(height: 6),
                    Text(
                      provider.hasAdminKey
                          ? 'Admin key is stored for live dashboard features and re-pairing.'
                          : 'No admin key saved. Pairing and live admin features are unavailable.',
                      style: Theme.of(context).textTheme.bodySmall?.copyWith(color: LuminColors.muted),
                    ),
                  ],
                ),
              ),
              const SizedBox(height: 14),
              TextField(
                controller: _dailyBudgetController,
                decoration: const InputDecoration(labelText: 'Daily budget limit'),
              ),
              const SizedBox(height: 14),
              TextField(
                controller: _monthlyBudgetController,
                decoration: const InputDecoration(labelText: 'Monthly budget limit'),
              ),
              const SizedBox(height: 14),
              TextField(
                controller: _alertThresholdController,
                decoration: const InputDecoration(labelText: 'Alert threshold (%)'),
              ),
              const SizedBox(height: 18),
              Container(
                padding: const EdgeInsets.all(16),
                decoration: BoxDecoration(
                  color: LuminColors.card,
                  borderRadius: BorderRadius.circular(18),
                  border: Border.all(color: LuminColors.border),
                ),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text('Refresh interval', style: Theme.of(context).textTheme.titleMedium),
                    Slider(
                      value: _refreshInterval.toDouble(),
                      min: 10,
                      max: 120,
                      divisions: 11,
                      label: '$_refreshInterval s',
                      onChanged: (value) => setState(() => _refreshInterval = value.round()),
                    ),
                    Text('$_refreshInterval seconds', style: Theme.of(context).textTheme.bodySmall),
                  ],
                ),
              ),
              const SizedBox(height: 20),
              ElevatedButton(
                onPressed: provider.isLoading
                    ? null
                    : () => provider.saveSettings(
                          baseUrl: _urlController.text,
                          apiKey: _keyController.text,
                          refreshIntervalSeconds: _refreshInterval,
                        ),
                child: const Text('Save Connection'),
              ),
              const SizedBox(height: 12),
              OutlinedButton(
                onPressed: provider.disconnect,
                style: OutlinedButton.styleFrom(
                  minimumSize: const Size.fromHeight(52),
                  side: const BorderSide(color: LuminColors.border),
                  foregroundColor: LuminColors.text,
                ),
                child: const Text('Disconnect'),
              ),
              const SizedBox(height: 20),
              Container(
                padding: const EdgeInsets.all(16),
                decoration: BoxDecoration(
                  color: LuminColors.card,
                  borderRadius: BorderRadius.circular(18),
                  border: Border.all(color: LuminColors.border),
                ),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text('Agent presets', style: Theme.of(context).textTheme.titleMedium),
                    const SizedBox(height: 8),
                    Text(
                      'Import your OpenClaw/NanoClaw setup into a reusable preset, then apply it to an agent group on your computer.',
                      style: Theme.of(context).textTheme.bodySmall?.copyWith(color: LuminColors.muted),
                    ),
                    const SizedBox(height: 12),
                    TextField(
                      controller: _presetSourceController,
                      decoration: const InputDecoration(labelText: 'OpenClaw source path'),
                    ),
                    const SizedBox(height: 12),
                    TextField(
                      controller: _presetNameController,
                      decoration: const InputDecoration(labelText: 'Preset name'),
                    ),
                    const SizedBox(height: 12),
                    TextField(
                      controller: _presetGroupController,
                      decoration: const InputDecoration(labelText: 'Apply to group'),
                    ),
                    const SizedBox(height: 12),
                    ElevatedButton(
                      onPressed: provider.isManagingPresets
                          ? null
                          : () => provider.importAgentPreset(
                                presetName: _presetNameController.text.trim(),
                                sourcePath: _presetSourceController.text.trim(),
                                applyToGroup: _presetGroupController.text.trim().isEmpty
                                    ? 'main'
                                    : _presetGroupController.text.trim(),
                              ),
                      child: const Text('Import preset'),
                    ),
                    const SizedBox(height: 8),
                    Text(
                      provider.presetStatusMessage,
                      style: Theme.of(context).textTheme.bodySmall?.copyWith(color: LuminColors.muted),
                    ),
                    const SizedBox(height: 12),
                    if (provider.agentPresets.isEmpty)
                      Text(
                        provider.hasAdminKey
                            ? 'No presets imported yet.'
                            : 'Save an admin key to manage presets from the phone.',
                        style: Theme.of(context).textTheme.bodySmall?.copyWith(color: LuminColors.muted),
                      ),
                    ...provider.agentPresets.map(
                      (preset) => Container(
                        margin: const EdgeInsets.only(top: 10),
                        padding: const EdgeInsets.all(14),
                        decoration: BoxDecoration(
                          color: Colors.white.withOpacity(0.02),
                          borderRadius: BorderRadius.circular(14),
                          border: Border.all(color: LuminColors.border),
                        ),
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Row(
                              children: [
                                Expanded(
                                  child: Text(
                                    preset.name,
                                    style: Theme.of(context).textTheme.titleSmall,
                                  ),
                                ),
                                Text('${preset.fileCount} files • ${preset.skillCount} skills'),
                              ],
                            ),
                            const SizedBox(height: 6),
                            Text(
                              preset.sourcePath,
                              style: Theme.of(context).textTheme.bodySmall?.copyWith(color: LuminColors.muted),
                            ),
                            const SizedBox(height: 6),
                            Text(
                              'Applied: ${preset.appliedGroups.isEmpty ? 'none' : preset.appliedGroups.join(', ')}',
                              style: Theme.of(context).textTheme.bodySmall?.copyWith(color: LuminColors.muted),
                            ),
                            const SizedBox(height: 10),
                            OutlinedButton(
                              onPressed: provider.isManagingPresets
                                  ? null
                                  : () => provider.applyAgentPreset(
                                        presetName: preset.name,
                                        groupId: _presetGroupController.text.trim().isEmpty
                                            ? 'main'
                                            : _presetGroupController.text.trim(),
                                      ),
                              child: const Text('Apply to group'),
                            ),
                          ],
                        ),
                      ),
                    ),
                  ],
                ),
              ),
              const SizedBox(height: 20),
              Container(
                padding: const EdgeInsets.all(16),
                decoration: BoxDecoration(
                  color: LuminColors.card,
                  borderRadius: BorderRadius.circular(18),
                  border: Border.all(color: LuminColors.border),
                ),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text('Connectors', style: Theme.of(context).textTheme.titleMedium),
                    const SizedBox(height: 8),
                    Text(
                      'Save external integrations like Slack and Notion for later use.',
                      style: Theme.of(context).textTheme.bodySmall?.copyWith(color: LuminColors.muted),
                    ),
                    const SizedBox(height: 12),
                    if (connectorOptions.isEmpty)
                      Text(
                        provider.hasAdminKey ? 'No connectors available.' : 'Save an admin key to manage connectors from the phone.',
                        style: Theme.of(context).textTheme.bodySmall?.copyWith(color: LuminColors.muted),
                      )
                    else ...[
                      DropdownButtonFormField<String>(
                        value: selectedConnector?.connectorType,
                        items: connectorOptions
                            .map(
                              (connector) => DropdownMenuItem<String>(
                                value: connector.connectorType,
                                child: Text(connector.name),
                              ),
                            )
                            .toList(growable: false),
                        onChanged: (value) {
                          if (value == null) return;
                          setState(() {
                            _selectedConnectorType = value;
                            final connector = connectorOptions.firstWhere((item) => item.connectorType == value);
                            _connectorDisplayNameController.text =
                                connector.displayName.isEmpty ? connector.name : connector.displayName;
                          });
                        },
                        decoration: const InputDecoration(labelText: 'Connector'),
                      ),
                      const SizedBox(height: 12),
                      TextField(
                        controller: _connectorDisplayNameController,
                        decoration: const InputDecoration(labelText: 'Display name'),
                      ),
                      if (selectedConnector != null) ...[
                        const SizedBox(height: 8),
                        Text(
                          selectedConnector.description,
                          style: Theme.of(context).textTheme.bodySmall?.copyWith(color: LuminColors.muted),
                        ),
                        const SizedBox(height: 12),
                        ...selectedConnector.fields.map((field) {
                          final controller = _connectorFieldController(
                            '${selectedConnector.connectorType}:${field.key}',
                            initialValue: selectedConnector.configValues[field.key] ?? '',
                          );
                          if (controller.text.isEmpty && (selectedConnector.configValues[field.key] ?? '').isNotEmpty) {
                            controller.text = selectedConnector.configValues[field.key] ?? '';
                          }
                          return Padding(
                            padding: const EdgeInsets.only(bottom: 12),
                            child: TextField(
                              controller: controller,
                              obscureText: field.secret,
                              decoration: InputDecoration(
                                labelText: field.label,
                                hintText: field.placeholder,
                              ),
                            ),
                          );
                        }),
                        Row(
                          children: [
                            Expanded(
                              child: ElevatedButton(
                                onPressed: provider.isManagingConnectors
                                    ? null
                                    : () {
                                        final config = <String, String>{};
                                        for (final field in selectedConnector.fields) {
                                          final value = _connectorFieldControllers['${selectedConnector.connectorType}:${field.key}']?.text.trim() ?? '';
                                          if (value.isNotEmpty) {
                                            config[field.key] = value;
                                          }
                                        }
                                        provider.saveConnector(
                                          connectorType: selectedConnector.connectorType,
                                          displayName: _connectorDisplayNameController.text.trim().isEmpty
                                              ? selectedConnector.name
                                              : _connectorDisplayNameController.text.trim(),
                                          config: config,
                                        );
                                      },
                                child: const Text('Save connector'),
                              ),
                            ),
                            const SizedBox(width: 12),
                            Expanded(
                              child: OutlinedButton(
                                onPressed: provider.isManagingConnectors || !selectedConnector.isConfigured
                                    ? null
                                    : () => provider.deleteConnector(selectedConnector.connectorType),
                                child: const Text('Remove connector'),
                              ),
                            ),
                          ],
                        ),
                      ],
                    ],
                    const SizedBox(height: 8),
                    Text(
                      provider.connectorStatusMessage,
                      style: Theme.of(context).textTheme.bodySmall?.copyWith(color: LuminColors.muted),
                    ),
                  ],
                ),
              ),
            ],
          ),
        );
      },
    );
  }
}
