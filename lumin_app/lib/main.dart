import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import 'providers/lumin_provider.dart';
import 'screens/connect_screen.dart';
import 'screens/dashboard_screen.dart';
import 'theme.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  runApp(const LuminApp());
}

class LuminApp extends StatelessWidget {
  const LuminApp({super.key});

  @override
  Widget build(BuildContext context) {
    return ChangeNotifierProvider<LuminProvider>(
      create: (_) => LuminProvider()..initialize(),
      child: Consumer<LuminProvider>(
        builder: (context, provider, _) {
          return MaterialApp(
            title: 'Lumin',
            debugShowCheckedModeBanner: false,
            theme: buildLuminTheme(),
            home: provider.isConfigured ? const DashboardScreen() : const ConnectScreen(),
          );
        },
      ),
    );
  }
}
