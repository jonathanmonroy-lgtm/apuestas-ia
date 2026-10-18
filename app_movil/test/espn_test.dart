// Real ESPN responses recorded 2026-10-17 (same fixtures as the backend's test_espn.py).
import 'dart:convert';
import 'dart:io';

import 'package:apuestas_ia/espn.dart';
import 'package:apuestas_ia/main.dart' show groupByCountry, dayLabel;
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

Object? load(String name) =>
    jsonDecode(File('test/fixtures/$name').readAsStringSync());

void main() {
  final scoreboard = load('scoreboard_all_2026-10-17_real.json');
  final leagues = parseLeagues(load('leagues_dropdown_real.json'));

  Match byId(List<Match> ms, String id) => ms.firstWhere((m) => m.id == id);

  test('league catalogue skips the real entry without id', () {
    expect(leagues['650']!.slug, 'col.1');
  });

  test('statuses, scores and countries', () {
    final ms = parseScoreboard(scoreboard, leagues);
    expect(ms.length, 22);
    final nacional = byId(ms, '401877851');
    expect(nacional.country.name, 'Colombia');
    expect(nacional.status, MatchStatus.finished);
    expect((nacional.homeScore, nacional.awayScore), (1, 4));
    expect(nacional.startsAt, DateTime.utc(2026, 10, 17, 20));
    final pens = byId(ms, '401923295');
    expect((pens.status, pens.statusNote), (MatchStatus.finished, 'penaltis'));
    expect(byId(ms, '401896749').status, MatchStatus.postponed);
    expect(byId(ms, '401878048').status, MatchStatus.live);
    expect(byId(ms, '401874057').country.isInternational, isTrue); // FIFA U-17
    expect(byId(ms, '401878771').country.name, 'Inglaterra');
  });

  test('odds only before kick-off', () {
    final ms = parseScoreboard(scoreboard, leagues);
    final withLine = ms.where((m) => m.line != null).map((m) => m.id).toSet();
    expect(withLine, {'761880', '761879', '761877', '761881', '761878', '761882', '401878030'});
    expect(byId(ms, '401878048').line, isNull); // live Millonarios: its line is not shown
    final atlanta = byId(ms, '761880').line!;
    expect(atlanta.prices.map((p) => p.toFixed(3)), ['3.300', '4.000', '1.870']);
  });

  test('unexpected payloads fail closed', () {
    expect(() => parseScoreboard([], leagues), throwsA(isA<EspnFormatException>()));
    expect(() => parseScoreboard({'events': [{'id': '1'}]}, leagues),
        throwsA(isA<EspnFormatException>())); // 1 of 1 unreadable
    expect(() => parseLeagues({'leagues': []}), throwsA(isA<EspnFormatException>()));
  });

  Map copyBoard() => jsonDecode(jsonEncode(scoreboard)) as Map;

  test('one unreadable match is skipped, not the whole day', () {
    final board = copyBoard();
    (board['events'] as List)[0]['uid'] = 's:600~e:1';
    final ms = parseScoreboard(board, leagues);
    expect(ms.length, 21);
  });

  test('kick-off time not confirmed unless ESPN says so', () {
    final board = copyBoard();
    final e = (board['events'] as List).firstWhere((e) => e['id'] == '761880');
    (e['competitions'][0] as Map).remove('timeValid');
    expect(byId(parseScoreboard(board, leagues), '761880').timeConfirmed, isFalse);
  });

  test('a projection provider is not a bookmaker', () {
    final board = copyBoard();
    final e = (board['events'] as List).firstWhere((e) => e['id'] == '761880');
    final odds = e['competitions'][0]['odds'] as List;
    (odds[0] as Map)['provider'] = {'id': '1004', 'name': 'consensus'};
    expect(byId(parseScoreboard(board, leagues), '761880').line, isNull);
  });

  test('a Colombian day reads two ESPN days and keeps only its own matches', () async {
    final asked = <String>[];
    final client = MockClient((req) async {
      asked.add(req.url.path.endsWith('dropdown') ? 'leagues' : req.url.queryParameters['dates']!);
      final body = req.url.path.endsWith('dropdown')
          ? File('test/fixtures/leagues_dropdown_real.json').readAsStringSync()
          : File('test/fixtures/scoreboard_all_2026-10-17_real.json').readAsStringSync();
      return http.Response.bytes(utf8.encode(body), 200);
    });
    final day = await EspnClient(client).colombianDay(DateTime.utc(2026, 10, 17));
    expect(asked, ['leagues', '20261017', '20261018']);
    // Medellín-Once Caldas (01:10Z on the 18th) is still the 17th in Colombia.
    expect(day.any((m) => m.id == '401878030'), isTrue);
    final colombia = groupByCountry(day).firstWhere((g) => g.country.name == 'Colombia');
    expect((colombia.matches.length, colombia.withOdds), (3, 1));
    expect(groupByCountry(day).last.country.isInternational, isTrue);
  });

  test('day labels and Colombian today', () {
    final today = DateTime.utc(2026, 10, 17);
    expect(dayLabel(today, today), 'Hoy');
    expect(dayLabel(DateTime.utc(2026, 10, 18), today), 'Mañana');
    expect(dayLabel(DateTime.utc(2026, 10, 20), today), 'mar 20');
    // 03:00Z on the 18th is still the 17th in Colombia.
    expect(colombianToday(DateTime.utc(2026, 10, 18, 3)), today);
  });
}
