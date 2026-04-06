import 'package:flutter/material.dart';

class LuminColors {
  static const background = Color(0xFF0A1A0F);
  static const primary = Color(0xFF25D366);
  static const accent = Color(0xFF005A4E);
  static const text = Color(0xFFE8F5E9);
  static const card = Color(0xFF0D2118);
  static const border = Color(0xFF1A3D2B);
  static const muted = Color(0xFF91B79E);
  static const warning = Color(0xFFF3C969);
  static const danger = Color(0xFFE56C6C);
}

ThemeData buildLuminTheme() {
  final scheme = ColorScheme.fromSeed(
    seedColor: LuminColors.primary,
    brightness: Brightness.dark,
  ).copyWith(
    primary: LuminColors.primary,
    secondary: LuminColors.accent,
    surface: LuminColors.card,
    onSurface: LuminColors.text,
    onPrimary: LuminColors.background,
  );

  return ThemeData(
    useMaterial3: true,
    brightness: Brightness.dark,
    colorScheme: scheme,
    scaffoldBackgroundColor: LuminColors.background,
    cardColor: LuminColors.card,
    dividerColor: LuminColors.border,
    textTheme: const TextTheme(
      headlineLarge: TextStyle(fontSize: 36, fontWeight: FontWeight.w800, color: LuminColors.text),
      headlineMedium: TextStyle(fontSize: 28, fontWeight: FontWeight.w700, color: LuminColors.text),
      titleLarge: TextStyle(fontSize: 18, fontWeight: FontWeight.w700, color: LuminColors.text),
      titleMedium: TextStyle(fontSize: 15, fontWeight: FontWeight.w600, color: LuminColors.text),
      bodyLarge: TextStyle(fontSize: 16, color: LuminColors.text),
      bodyMedium: TextStyle(fontSize: 14, color: LuminColors.text),
      bodySmall: TextStyle(fontSize: 12, color: LuminColors.muted),
    ),
    appBarTheme: const AppBarTheme(
      backgroundColor: Colors.transparent,
      foregroundColor: LuminColors.text,
      elevation: 0,
      centerTitle: false,
    ),
    inputDecorationTheme: InputDecorationTheme(
      filled: true,
      fillColor: const Color(0xFF0B1610),
      border: OutlineInputBorder(
        borderRadius: BorderRadius.circular(18),
        borderSide: const BorderSide(color: LuminColors.border),
      ),
      enabledBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(18),
        borderSide: const BorderSide(color: LuminColors.border),
      ),
      focusedBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(18),
        borderSide: const BorderSide(color: LuminColors.primary, width: 1.4),
      ),
      hintStyle: const TextStyle(color: LuminColors.muted),
    ),
    elevatedButtonTheme: ElevatedButtonThemeData(
      style: ElevatedButton.styleFrom(
        backgroundColor: LuminColors.primary,
        foregroundColor: LuminColors.background,
        minimumSize: const Size.fromHeight(56),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(18)),
        textStyle: const TextStyle(fontSize: 16, fontWeight: FontWeight.w700),
      ),
    ),
    bottomNavigationBarTheme: const BottomNavigationBarThemeData(
      backgroundColor: LuminColors.card,
      selectedItemColor: LuminColors.primary,
      unselectedItemColor: LuminColors.muted,
      type: BottomNavigationBarType.fixed,
      elevation: 0,
    ),
  );
}
