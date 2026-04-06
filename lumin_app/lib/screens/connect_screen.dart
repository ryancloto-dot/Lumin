import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../providers/lumin_provider.dart';
import '../theme.dart';
import 'dashboard_screen.dart';
import '../widgets/live_indicator.dart';

class ConnectScreen extends StatefulWidget {
  const ConnectScreen({super.key});

  @override
  State<ConnectScreen> createState() => _ConnectScreenState();
}

class _ConnectScreenState extends State<ConnectScreen> {
  late final TextEditingController _urlController;
  late final TextEditingController _keyController;

  @override
  void initState() {
    super.initState();
    final provider = context.read<LuminProvider>();
    _urlController = TextEditingController(text: provider.baseUrl);
    _keyController = TextEditingController(text: provider.apiKey);
  }

  @override
  void dispose() {
    _urlController.dispose();
    _keyController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Consumer<LuminProvider>(
      builder: (context, provider, _) {
        if (provider.isConfigured && provider.isOnline) {
          return const DashboardScreen();
        }

        return Scaffold(
          body: SafeArea(
            child: Center(
              child: SingleChildScrollView(
                padding: const EdgeInsets.all(24),
                child: ConstrainedBox(
                  constraints: const BoxConstraints(maxWidth: 520),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Center(
                        child: Image.asset(
                          'assets/lumin-mark.png',
                          width: 72,
                          height: 72,
                        ),
                      ),
                      const SizedBox(height: 18),
                      Container(
                        padding: const EdgeInsets.all(24),
                        decoration: BoxDecoration(
                          gradient: LinearGradient(
                            colors: [
                              LuminColors.primary.withValues(alpha: 0.18),
                              LuminColors.accent.withValues(alpha: 0.18),
                            ],
                          ),
                          borderRadius: BorderRadius.circular(28),
                          border: Border.all(color: LuminColors.border),
                        ),
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            const LiveIndicator(live: true, label: 'Lumin mobile companion'),
                            const SizedBox(height: 20),
                            Text('Control your AI costs from your phone.', style: Theme.of(context).textTheme.headlineMedium),
                            const SizedBox(height: 10),
                            Text(
                              'Pair this phone with your self-hosted Lumin instance. After the first pairing, the app will use a mobile token while NanoClaw keeps running on your computer.',
                              style: Theme.of(context).textTheme.bodyMedium?.copyWith(color: LuminColors.muted),
                            ),
                          ],
                        ),
                      ),
                      const SizedBox(height: 24),
                      TextField(
                        controller: _urlController,
                        keyboardType: TextInputType.url,
                        decoration: const InputDecoration(
                          labelText: 'Lumin URL',
                          hintText: 'https://xyz.trycloudflare.com',
                        ),
                      ),
                      const SizedBox(height: 16),
                      TextField(
                        controller: _keyController,
                        obscureText: true,
                        decoration: const InputDecoration(
                          labelText: 'Pairing key',
                          hintText: 'Enter your desktop X-Lumin-Key once to pair',
                        ),
                      ),
                      const SizedBox(height: 16),
                      Container(
                        width: double.infinity,
                        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
                        decoration: BoxDecoration(
                          color: LuminColors.card,
                          borderRadius: BorderRadius.circular(16),
                          border: Border.all(color: LuminColors.border),
                        ),
                        child: Row(
                          children: [
                            LiveIndicator(live: provider.isOnline, label: provider.statusMessage),
                          ],
                        ),
                      ),
                      const SizedBox(height: 12),
                      Text(
                        'The phone never runs NanoClaw locally. It sends tasks to Lumin, and your computer executes them.',
                        style: Theme.of(context).textTheme.bodySmall?.copyWith(color: LuminColors.muted),
                      ),
                      const SizedBox(height: 20),
                      ElevatedButton(
                        onPressed: provider.isLoading
                            ? null
                            : () async {
                                await provider.connect(
                                  baseUrl: _urlController.text,
                                  apiKey: _keyController.text,
                                  deviceName: 'Lumin Mobile',
                                );
                              },
                        child: provider.isLoading
                            ? const SizedBox(
                                width: 24,
                                height: 24,
                                child: CircularProgressIndicator(strokeWidth: 2),
                              )
                            : const Text('Connect'),
                      ),
                    ],
                  ),
                ),
              ),
            ),
          ),
        );
      },
    );
  }
}
