// Own smoke test (kept so `flutter create` in CI does not add its counter-app sample).
import 'package:apuestas_ia/main.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

void main() {
  testWidgets('the +18 notice comes first', (tester) async {
    SharedPreferences.setMockInitialValues({});
    await tester.pumpWidget(const ApuestasApp());
    await tester.pumpAndSettle();
    expect(find.text('Tengo 18 años o más'), findsOneWidget);
    expect(find.text('Salir'), findsOneWidget);
  });

  testWidgets('the +18 badge opens the responsible gambling text', (tester) async {
    await tester.pumpWidget(const MaterialApp(
      home: Scaffold(body: Center(child: ResponsibleBadge())),
    ));
    await tester.tap(find.text('+18'));
    await tester.pumpAndSettle();
    expect(find.text('Juega con responsabilidad'), findsOneWidget);
    expect(find.textContaining('nunca garantías de ganancia'), findsOneWidget);
  });
}
