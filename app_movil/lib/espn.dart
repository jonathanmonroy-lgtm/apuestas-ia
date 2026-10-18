/// ESPN client: matches of a Colombian day, by country, straight from the phone.
///
/// UNOFFICIAL: site.api.espn.com is the public JSON behind espn.com, without terms for
/// third parties. It may change or vanish without notice: anything unexpected fails
/// closed (EspnFormatException) instead of guessing. Verified 2026-10-17 (see the
/// backend's espn.py): one request per ESPN day returns every league, ESPN days are US
/// Eastern, odds come from ONE bookmaker on some matches, also while live.
library;

import 'dart:convert';
import 'dart:isolate';

import 'package:flutter/foundation.dart' show ValueNotifier;

import 'package:http/http.dart' as http;

import 'countries.dart';
import 'odds.dart';

const _base = 'https://site.api.espn.com/apis/site/v2';
const _limit = 1000;
const colombiaOffset = Duration(hours: -5); // the product's day: Colombia (UTC-5, no DST)

class EspnFormatException implements Exception {
  final String message;
  EspnFormatException(this.message);
  @override
  String toString() => 'Formato inesperado de ESPN: $message';
}

enum MatchStatus { scheduled, live, finished, postponed, cancelled, unknown }

// name -> (status, label shown next to a finished score)
const Map<String, (MatchStatus, String?)> _statusMap = {
  'STATUS_SCHEDULED': (MatchStatus.scheduled, null),
  'STATUS_DELAYED': (MatchStatus.scheduled, null),
  'STATUS_FIRST_HALF': (MatchStatus.live, null),
  'STATUS_HALFTIME': (MatchStatus.live, null),
  'STATUS_SECOND_HALF': (MatchStatus.live, null),
  'STATUS_IN_PROGRESS': (MatchStatus.live, null),
  'STATUS_END_OF_REGULATION': (MatchStatus.live, null),
  'STATUS_OVERTIME': (MatchStatus.live, null),
  'STATUS_EXTRA_TIME': (MatchStatus.live, null),
  'STATUS_SHOOTOUT': (MatchStatus.live, null),
  'STATUS_FULL_TIME': (MatchStatus.finished, null),
  'STATUS_FINAL': (MatchStatus.finished, null),
  'STATUS_FINAL_AET': (MatchStatus.finished, 'prórroga'),
  'STATUS_FINAL_PEN': (MatchStatus.finished, 'penaltis'),
  'STATUS_POSTPONED': (MatchStatus.postponed, null),
  // Suspended may be mid-match or for days: unknown, not "postponed".
  'STATUS_SUSPENDED': (MatchStatus.unknown, null),
  'STATUS_CANCELED': (MatchStatus.cancelled, null),
  'STATUS_ABANDONED': (MatchStatus.cancelled, null),
  'STATUS_FORFEIT': (MatchStatus.finished, 'administrativo'),
};
const _stateFallback = {'pre': MatchStatus.scheduled, 'in': MatchStatus.live};
// Only real bookmakers' lines are prices (ESPN also publishes consensus/projections).
const _bookmakerProviderIds = {'100'}; // DraftKings; its name is never shown
const _maxSkippedShare = 0.5;

class League {
  final String id;
  final String slug;
  final String name;
  const League(this.id, this.slug, this.name);
}

/// 1X2 line of ONE bookmaker (its name is never shown: internal mode), pre-match only.
class Line {
  final Fraction home;
  final Fraction draw;
  final Fraction away;
  const Line(this.home, this.draw, this.away);

  List<Fraction> get prices => [home, draw, away];
}

class Match {
  final String id;
  final League league;
  final Country country;
  final DateTime startsAt; // UTC
  final bool timeConfirmed;
  final MatchStatus status;
  final String? statusNote; // 'penaltis', 'prórroga'...
  final String home;
  final String away;
  final String? homeId; // ESPN team id: its crest
  final String? awayId;
  final int? homeScore; // shown while live and when finished
  final int? awayScore;
  final Line? line;

  const Match({
    required this.id,
    required this.league,
    required this.country,
    required this.startsAt,
    required this.timeConfirmed,
    required this.status,
    required this.statusNote,
    required this.home,
    required this.away,
    this.homeId,
    this.awayId,
    required this.homeScore,
    required this.awayScore,
    required this.line,
  });
}

/// A Colombian calendar day as a UTC interval: 2026-10-17 -> [17 05:00Z, 18 05:00Z).
(DateTime, DateTime) colombianDayBounds(DateTime day) {
  final start = DateTime.utc(day.year, day.month, day.day).subtract(colombiaOffset);
  return (start, start.add(const Duration(days: 1)));
}

DateTime colombianToday([DateTime? now]) {
  final local = (now ?? DateTime.now()).toUtc().add(colombiaOffset);
  return DateTime.utc(local.year, local.month, local.day);
}

String _espnDate(DateTime d) =>
    '${d.year.toString().padLeft(4, '0')}${d.month.toString().padLeft(2, '0')}'
    '${d.day.toString().padLeft(2, '0')}';

Map<String, League> parseLeagues(Object? payload) {
  final list = payload is Map ? payload['leagues'] : null;
  if (list is! List) throw EspnFormatException("sin lista 'leagues'");
  final out = <String, League>{};
  for (final item in list) {
    if (item is! Map) continue;
    final id = item['id'], slug = item['slug'], name = item['name'];
    if (id is String && slug is String && name is String && id.isNotEmpty) {
      out[id] = League(id, slug, name); // one real entry has no id: skipped
    }
  }
  if (out.isEmpty) throw EspnFormatException('catálogo de ligas vacío');
  return out;
}

final _leagueId = RegExp(r'~l:(\d+)~');

/// Matches of one scoreboard response. Leagues missing from the catalogue are skipped
/// (no slug -> no country): never filed under a made-up country.
List<Match> parseScoreboard(Object? payload, Map<String, League> leagues) {
  final events = payload is Map ? payload['events'] : null;
  if (events is! List) throw EspnFormatException("sin lista 'events'");
  // Event by event: one odd match (a cup tie still "TBD") is skipped, not the whole day.
  final out = <Match>[];
  var skipped = 0;
  for (final e in events) {
    try {
      final m = _parseEvent(e as Map, leagues);
      if (m != null) out.add(m);
    } on TypeError {
      skipped++;
    } on FormatException {
      skipped++;
    } on EspnFormatException {
      skipped++;
    }
  }
  if (events.isNotEmpty && skipped / events.length > _maxSkippedShare) {
    throw EspnFormatException('$skipped de ${events.length} partidos ilegibles');
  }
  return out;
}

Match? _parseEvent(Map e, Map<String, League> leagues) {
  final leagueId = _leagueId.firstMatch(e['uid'] as String)?.group(1);
  if (leagueId == null) throw EspnFormatException('evento ${e['id']} sin liga');
  final league = leagues[leagueId];
  if (league == null) return null;

  final type = (e['status'] as Map)['type'] as Map;
  final name = type['name'] as String;
  final state = type['state'] as String;
  final (status, note) =
      _statusMap[name] ?? (_stateFallback[state] ?? MatchStatus.unknown, null);

  final comp = (e['competitions'] as List).first as Map;
  final sides = <String, Map>{
    for (final c in comp['competitors'] as List) (c as Map)['homeAway'] as String: c,
  };
  final home = sides['home'], away = sides['away'];
  if (home == null || away == null) {
    throw EspnFormatException('evento ${e['id']} sin local/visitante');
  }
  String? teamId(Map c) => (c['team'] as Map)['id']?.toString();
  String teamName(Map c) {
    final t = c['team'] as Map;
    return (t['shortDisplayName'] ?? t['displayName']) as String;
  }

  final showScore = status == MatchStatus.live || status == MatchStatus.finished;
  final startsAt = DateTime.parse(e['date'] as String).toUtc();
  return Match(
    id: e['id'] as String,
    league: league,
    country: countryOfSlug(league.slug),
    startsAt: startsAt,
    // Missing = not confirmed: never invent a confirmed kick-off time.
    timeConfirmed: comp['timeValid'] == true,
    status: status,
    statusNote: note,
    home: teamName(home),
    away: teamName(away),
    homeId: teamId(home),
    awayId: teamId(away),
    homeScore: showScore ? int.tryParse('${home['score']}') : null,
    awayScore: showScore ? int.tryParse('${away['score']}') : null,
    // Pre-match odds only: ESPN keeps a (moving) line during the match too.
    line: state == 'pre' ? _line(comp['odds']) : null,
  );
}

Line? _line(Object? odds) {
  if (odds is! List) return null;
  for (final o in odds) {
    if (o is! Map) continue;
    final provider = o['provider'];
    if (provider is! Map || !_bookmakerProviderIds.contains('${provider['id']}')) continue;
    final ml = o['moneyline'];
    if (ml is! Map) continue;
    Fraction? side(String key) {
      final s = ml[key];
      final close = s is Map ? s['close'] : null;
      return americanToDecimal(close is Map ? close['odds'] : null);
    }

    final h = side('home'), d = side('draw'), a = side('away');
    final one = Fraction.of(1);
    if (h != null && d != null && a != null && h > one && d > one && a > one) {
      return Line(h, d, a); // the first clean 1X2 line; incomplete ones are skipped
    }
  }
  return null;
}

/// Crest image of an ESPN team (80 px, ~7 KB), or null without an id.
String? crestUrl(String? teamId) => teamId == null || int.tryParse(teamId) == null
    ? null
    : 'https://a.espncdn.com/combiner/i?img=/i/teamlogos/soccer/500/$teamId.png&h=80&w=80';

class EspnClient {
  final http.Client _http;
  Map<String, League>? _leagues;
  DateTime? _leaguesAt;
  final Map<String, (DateTime, List<Match>)> _days = {};
  static const _dayTtl = Duration(minutes: 3);

  /// What is being done right now, for the loading screen.
  final progress = ValueNotifier<String>('');

  EspnClient([http.Client? client]) : _http = client ?? http.Client();

  /// Raw body: a day of play is ~3.5 MB of JSON, decoded OFF the UI thread (below).
  Future<List<int>> _get(String path, Map<String, String> query) async {
    final uri = Uri.parse('$_base$path').replace(queryParameters: query);
    final r = await _http
        .get(uri, headers: {'Accept': 'application/json'})
        .timeout(const Duration(seconds: 25));
    if (r.statusCode != 200) {
      throw http.ClientException('ESPN respondió ${r.statusCode}', uri);
    }
    return r.bodyBytes;
  }

  Future<Map<String, League>> leagues() async {
    final fresh = _leaguesAt != null &&
        DateTime.now().difference(_leaguesAt!) < const Duration(hours: 12);
    if (_leagues != null && fresh) return _leagues!;
    progress.value = 'Cargando ligas…';
    final body = await _get('/leagues/dropdown', {'sport': 'soccer', 'limit': '$_limit'});
    _leagues = await Isolate.run(() => parseLeagues(jsonDecode(utf8.decode(body))));
    _leaguesAt = DateTime.now();
    return _leagues!;
  }

  /// Every match of a Colombian day. ESPN days are US Eastern (start 04:00Z in summer):
  /// the last hour of a Colombian day is in the next ESPN day, so both are read.
  Future<List<Match>> colombianDay(DateTime day, {bool refresh = false}) async {
    final key = _espnDate(day);
    final cached = _days[key];
    if (!refresh && cached != null && DateTime.now().difference(cached.$1) < _dayTtl) {
      return cached.$2;
    }
    // The catalogue and both ESPN days are requested at the same time.
    final leaguesF = this.leagues();
    progress.value = 'Descargando partidos…';
    final bodies = await Future.wait([
      for (final d in [day, day.add(const Duration(days: 1))])
        _get('/sports/soccer/all/scoreboard', {'dates': _espnDate(d), 'limit': '$_limit'}),
    ]);
    final leagues = await leaguesF;
    progress.value = 'Ordenando partidos…';
    final (start, end) = colombianDayBounds(day);
    final byId = <String, Match>{};
    for (final body in bodies) {
      final parsed = await Isolate.run(
        () => parseScoreboard(jsonDecode(utf8.decode(body)), leagues),
      );
      for (final m in parsed) {
        byId[m.id] = m;
      }
    }
    final matches = byId.values
        .where((m) => !m.startsAt.isBefore(start) && m.startsAt.isBefore(end))
        .toList()
      ..sort((a, b) {
        final t = a.startsAt.compareTo(b.startsAt);
        return t != 0 ? t : a.id.compareTo(b.id);
      });
    _days[key] = (DateTime.now(), matches);
    progress.value = '';
    return matches;
  }

  void close() {
    progress.dispose();
    _http.close();
  }
}
