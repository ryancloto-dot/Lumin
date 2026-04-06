import 'package:flutter_test/flutter_test.dart';

import 'package:lumin_app/main.dart';

void main() {
  testWidgets('renders connect screen shell on launch', (tester) async {
    await tester.pumpWidget(const LuminApp());
    expect(find.text('Control your AI costs from your phone.'), findsOneWidget);
  });
}
