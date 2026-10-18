// Own smoke test (kept so `flutter create` in CI does not add its counter-app sample).
import 'dart:convert';
import 'dart:io';

import 'package:apuestas_ia/espn.dart';
import 'package:apuestas_ia/main.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

void main() {
  testWidgets('opens straight on the matches of the day and loads them', (tester) async {
    final client = MockClient((req) async {
      final file = req.url.path.endsWith('dropdown')
          ? 'test/fixtures/leagues_dropdown_real.json'
          : 'test/fixtures/scoreboard_all_2026-10-17_real.json';
      return http.Response.bytes(utf8.encode(File(file).readAsStringSync()), 200);
    });
    await tester.runAsync(() async {
      await tester.pumpWidget(ApuestasApp(client: EspnClient(client)));
      await Future<void>.delayed(const Duration(seconds: 2)); // real isolates
    });
    await tester.pump();
    expect(find.text('Hoy'), findsOneWidget); // the day strip, nothing before it
    expect(find.textContaining('No se pudieron cargar'), findsNothing);
  });
}
