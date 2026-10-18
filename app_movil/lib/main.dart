import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'countries.dart';
import 'espn.dart';
import 'odds.dart';

// The web app's palette (frontend/src/app/globals.css).
const bg = Color(0xFF0A0F0B);
const surface = Color(0xFF101713);
const surface2 = Color(0xFF141F19);
const border = Color(0xFF22312A);
const textColor = Color(0xFFE8EFE9);
const muted = Color(0xFF93A69A);
const gold = Color(0xFFC9A227);
const live = Color(0xFF3FA66E);
const awayBlue = Color(0xFF4AA3C8);

const daysBack = 3;
const daysAhead = 7;
const _ageKey = 'age_confirmed_v1';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  runApp(const ApuestasApp());
}

class ApuestasApp extends StatelessWidget {
  const ApuestasApp({super.key});

  @override
  Widget build(BuildContext context) {
    const scheme = ColorScheme.dark(primary: gold, surface: surface, onSurface: textColor);
    return MaterialApp(
      title: 'Apuestas IA',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        colorScheme: scheme,
        scaffoldBackgroundColor: bg,
        appBarTheme: const AppBarTheme(
          backgroundColor: surface,
          foregroundColor: textColor,
          elevation: 0,
          scrolledUnderElevation: 0,
        ),
        dividerColor: border,
        chipTheme: const ChipThemeData(
          backgroundColor: bg,
          selectedColor: surface2,
          side: BorderSide(color: border),
          labelStyle: TextStyle(color: textColor),
          showCheckmark: false,
        ),
        useMaterial3: true,
      ),
      home: const AgeGate(),
    );
  }
}

/// +18 self-declaration on first launch. Real age verification depends on the
/// jurisdiction (still undecided): this is the minimum for an internal test build.
class AgeGate extends StatefulWidget {
  const AgeGate({super.key});

  @override
  State<AgeGate> createState() => _AgeGateState();
}

class _AgeGateState extends State<AgeGate> {
  bool? _confirmed;

  @override
  void initState() {
    super.initState();
    SharedPreferences.getInstance().then((p) {
      if (mounted) setState(() => _confirmed = p.getBool(_ageKey) ?? false);
    });
  }

  Future<void> _confirm() async {
    final p = await SharedPreferences.getInstance();
    await p.setBool(_ageKey, true);
    if (mounted) setState(() => _confirmed = true);
  }

  @override
  Widget build(BuildContext context) {
    if (_confirmed == null) {
      return const Scaffold(body: Center(child: CircularProgressIndicator()));
    }
    if (_confirmed!) return const DaysScreen();
    return Scaffold(
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              const Text('+18',
                  style: TextStyle(color: gold, fontSize: 48, fontWeight: FontWeight.w700)),
              const SizedBox(height: 12),
              const Text(
                'Esta app es sólo para mayores de 18 años.',
                style: TextStyle(fontSize: 20, fontWeight: FontWeight.w600),
              ),
              const SizedBox(height: 12),
              const Text(
                'Ofrece información y estimaciones sobre partidos de fútbol, nunca '
                'garantías de ganancia. Apostar conlleva riesgo de perder dinero. '
                'Es una versión de prueba interna.',
                style: TextStyle(color: muted, height: 1.4),
              ),
              const SizedBox(height: 32),
              FilledButton(
                onPressed: _confirm,
                style: FilledButton.styleFrom(
                  backgroundColor: gold,
                  foregroundColor: bg,
                  minimumSize: const Size.fromHeight(52),
                ),
                child: const Text('Tengo 18 años o más'),
              ),
              const SizedBox(height: 12),
              OutlinedButton(
                onPressed: () => SystemNavigator.pop(),
                style: OutlinedButton.styleFrom(minimumSize: const Size.fromHeight(52)),
                child: const Text('Salir'),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

const responsibleText =
    'Esta app ofrece información y estimaciones, nunca garantías de ganancia. '
    '"Dato" es lo que informa el proveedor; "Análisis" es un cálculo nuestro, no un '
    'pronóstico. Fija límites de gasto y, si sientes que pierdes el control, busca ayuda '
    'profesional.';

/// Compact responsible gambling notice: a "+18" badge in every app bar that opens the
/// full text (the user asked to drop the full-width banners; the notice stays present).
class ResponsibleBadge extends StatelessWidget {
  const ResponsibleBadge({super.key});

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(right: 12),
      child: InkWell(
        borderRadius: BorderRadius.circular(999),
        onTap: () => showDialog<void>(
          context: context,
          builder: (context) => AlertDialog(
            backgroundColor: surface2,
            title: const Text('Juega con responsabilidad', style: TextStyle(color: gold)),
            content: const Text(responsibleText, style: TextStyle(height: 1.4)),
            actions: [
              TextButton(
                onPressed: () => Navigator.of(context).pop(),
                child: const Text('Entendido'),
              ),
            ],
          ),
        ),
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
          decoration: BoxDecoration(color: gold, borderRadius: BorderRadius.circular(999)),
          child: const Text('+18',
              style: TextStyle(color: bg, fontWeight: FontWeight.w800, fontSize: 12)),
        ),
      ),
    );
  }
}

const _weekdays = ['lun', 'mar', 'mié', 'jue', 'vie', 'sáb', 'dom'];

String dayLabel(DateTime day, DateTime today) {
  final diff = day.difference(today).inDays;
  if (diff == 0) return 'Hoy';
  if (diff == -1) return 'Ayer';
  if (diff == 1) return 'Mañana';
  return '${_weekdays[day.weekday - 1]} ${day.day}';
}

String hhmm(DateTime utc) {
  final t = utc.add(colombiaOffset);
  return '${t.hour.toString().padLeft(2, '0')}:${t.minute.toString().padLeft(2, '0')}';
}

class CountryGroup {
  final Country country;
  final List<Match> matches;
  CountryGroup(this.country, this.matches);
  int get withOdds => matches.where((m) => m.line != null).length;
  int get liveNow => matches.where((m) => m.status == MatchStatus.live).length;
}

List<CountryGroup> groupByCountry(List<Match> matches) {
  final byName = <String, CountryGroup>{};
  for (final m in matches) {
    byName.putIfAbsent(m.country.name, () => CountryGroup(m.country, [])).matches.add(m);
  }
  final groups = byName.values.toList()
    ..sort((a, b) {
      if (a.country.isInternational != b.country.isInternational) {
        return a.country.isInternational ? 1 : -1; // international last
      }
      return a.country.name.compareTo(b.country.name);
    });
  return groups;
}

enum MatchFilter {
  all('Todos'),
  live('En vivo'),
  upcoming('Por jugar'),
  withOdds('Con cuotas');

  final String label;
  const MatchFilter(this.label);

  bool keeps(Match m) => switch (this) {
        MatchFilter.all => true,
        MatchFilter.live => m.status == MatchStatus.live,
        MatchFilter.upcoming => m.status == MatchStatus.scheduled,
        MatchFilter.withOdds => m.line != null,
      };
}

class DaysScreen extends StatefulWidget {
  const DaysScreen({super.key});

  @override
  State<DaysScreen> createState() => _DaysScreenState();
}

class _DaysScreenState extends State<DaysScreen> {
  final _espn = EspnClient();
  late final DateTime _today = colombianToday();
  late DateTime _day = _today;
  late Future<List<Match>> _future = _espn.colombianDay(_day);

  @override
  void dispose() {
    _espn.close();
    super.dispose();
  }

  void _select(DateTime day) {
    setState(() {
      _day = day;
      _future = _espn.colombianDay(day);
    });
  }

  Future<void> _refresh() async {
    final f = _espn.colombianDay(_day, refresh: true);
    setState(() => _future = f);
    await f.catchError((_) => <Match>[]);
  }

  @override
  Widget build(BuildContext context) {
    final days = [
      for (var i = -daysBack; i <= daysAhead; i++) _today.add(Duration(days: i)),
    ];
    return Scaffold(
      appBar: AppBar(
        title: const Text.rich(TextSpan(children: [
          TextSpan(text: 'Apuestas '),
          TextSpan(text: 'IA', style: TextStyle(color: gold)),
        ])),
        actions: const [ResponsibleBadge()],
      ),
      body: Column(
        children: [
          SizedBox(
            height: 56,
            child: ListView(
              scrollDirection: Axis.horizontal,
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
              children: [
                for (final d in days)
                  Padding(
                    padding: const EdgeInsets.only(right: 8),
                    child: ChoiceChip(
                      label: Text(dayLabel(d, _today)),
                      selected: d == _day,
                      side: BorderSide(color: d == _day ? gold : border),
                      onSelected: (_) => _select(d),
                    ),
                  ),
              ],
            ),
          ),
          Expanded(
            child: FutureBuilder<List<Match>>(
              future: _future,
              builder: (context, snap) {
                if (snap.connectionState != ConnectionState.done) {
                  return const Center(child: CircularProgressIndicator());
                }
                if (snap.hasError) {
                  return _ErrorView(error: snap.error!, onRetry: _refresh);
                }
                final matches = snap.data!;
                final groups = groupByCountry(matches);
                return RefreshIndicator(
                  onRefresh: _refresh,
                  child: ListView(
                    children: [
                      _StatCards(
                        matches: matches.length,
                        countries: groups.length,
                        liveNow: matches.where((m) => m.status == MatchStatus.live).length,
                        withOdds: matches.where((m) => m.line != null).length,
                      ),
                      if (groups.isEmpty)
                        const Padding(
                          padding: EdgeInsets.all(24),
                          child: Text('No hay partidos este día.', style: TextStyle(color: muted)),
                        ),
                      for (final g in groups) _CountryTile(group: g, dayText: dayLabel(_day, _today)),
                      const _SourceNote(),
                    ],
                  ),
                );
              },
            ),
          ),
        ],
      ),
    );
  }
}

class _StatCards extends StatelessWidget {
  final int matches, countries, liveNow, withOdds;
  const _StatCards({
    required this.matches,
    required this.countries,
    required this.liveNow,
    required this.withOdds,
  });

  @override
  Widget build(BuildContext context) {
    Widget card(String value, String label, {bool isLive = false}) => Expanded(
          child: Container(
            margin: const EdgeInsets.symmetric(horizontal: 4),
            padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 10),
            decoration: BoxDecoration(
              gradient: const LinearGradient(
                begin: Alignment.topLeft,
                end: Alignment.bottomRight,
                colors: [surface2, surface],
              ),
              border: Border.all(color: border),
              borderRadius: BorderRadius.circular(10),
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(children: [
                  if (isLive) ...[const LiveDot(), const SizedBox(width: 6)],
                  Text(value,
                      style: const TextStyle(fontSize: 22, fontWeight: FontWeight.w700)),
                ]),
                Text(label, style: const TextStyle(color: muted, fontSize: 12)),
              ],
            ),
          ),
        );
    return Padding(
      padding: const EdgeInsets.fromLTRB(12, 4, 12, 12),
      child: Row(children: [
        card('$matches', 'partidos'),
        card('$countries', 'países'),
        card('$liveNow', 'en vivo', isLive: liveNow > 0),
        card('$withOdds', 'con cuotas'),
      ]),
    );
  }
}

class _CountryTile extends StatelessWidget {
  final CountryGroup group;
  final String dayText;
  const _CountryTile({required this.group, required this.dayText});

  @override
  Widget build(BuildContext context) {
    final g = group;
    return Container(
      decoration: const BoxDecoration(border: Border(top: BorderSide(color: border))),
      child: ListTile(
        leading: Text(g.country.flag, style: const TextStyle(fontSize: 26)),
        title: Text(g.country.name, style: const TextStyle(fontWeight: FontWeight.w600)),
        subtitle: Text(
          '${g.matches.length} partidos · ${g.withOdds} con cuotas',
          style: const TextStyle(color: muted),
        ),
        trailing: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            if (g.liveNow > 0) ...[
              const LiveDot(),
              const SizedBox(width: 4),
              Text('${g.liveNow}', style: const TextStyle(color: live, fontSize: 12)),
              const SizedBox(width: 8),
            ],
            const Icon(Icons.chevron_right, color: muted),
          ],
        ),
        onTap: () => Navigator.of(context).push(MaterialPageRoute(
          builder: (_) => CountryScreen(group: g, dayLabel: dayText),
        )),
      ),
    );
  }
}

/// Green dot that pulses (live matches).
class LiveDot extends StatefulWidget {
  const LiveDot({super.key});

  @override
  State<LiveDot> createState() => _LiveDotState();
}

class _LiveDotState extends State<LiveDot> with SingleTickerProviderStateMixin {
  late final AnimationController _c = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 1400),
  )..repeat(reverse: true);

  @override
  void dispose() {
    _c.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return FadeTransition(
      opacity: Tween<double>(begin: 0.35, end: 1).animate(_c),
      child: Container(
        width: 8,
        height: 8,
        decoration: const BoxDecoration(color: live, shape: BoxShape.circle),
      ),
    );
  }
}

class _ErrorView extends StatelessWidget {
  final Object error;
  final Future<void> Function() onRetry;
  const _ErrorView({required this.error, required this.onRetry});

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Icon(Icons.wifi_off, color: muted, size: 40),
            const SizedBox(height: 12),
            const Text('No se pudieron cargar los partidos.',
                style: TextStyle(fontSize: 16, fontWeight: FontWeight.w600)),
            const SizedBox(height: 8),
            Text('Comprueba la conexión a internet. Detalle: $error',
                textAlign: TextAlign.center, style: const TextStyle(color: muted)),
            const SizedBox(height: 16),
            OutlinedButton(onPressed: onRetry, child: const Text('Reintentar')),
          ],
        ),
      ),
    );
  }
}

class _SourceNote extends StatelessWidget {
  const _SourceNote();

  @override
  Widget build(BuildContext context) {
    return const Padding(
      padding: EdgeInsets.fromLTRB(16, 16, 16, 32),
      child: Text(
        'Fuente: ESPN (no oficial). Horas de Colombia (UTC-5). Desliza hacia abajo para '
        'actualizar.',
        style: TextStyle(color: muted, fontSize: 12),
      ),
    );
  }
}

class CountryScreen extends StatefulWidget {
  final CountryGroup group;
  final String dayLabel;
  const CountryScreen({super.key, required this.group, required this.dayLabel});

  @override
  State<CountryScreen> createState() => _CountryScreenState();
}

class _CountryScreenState extends State<CountryScreen> {
  MatchFilter _filter = MatchFilter.all;

  @override
  Widget build(BuildContext context) {
    final g = widget.group;
    final byLeague = <String, List<Match>>{};
    for (final m in g.matches.where(_filter.keeps)) {
      byLeague.putIfAbsent(m.league.name, () => []).add(m);
    }
    return Scaffold(
      appBar: AppBar(
        title: Text('${g.country.flag}  ${g.country.name} · ${widget.dayLabel}'),
        actions: const [ResponsibleBadge()],
      ),
      body: ListView(
        padding: const EdgeInsets.only(bottom: 24),
        children: [
          SizedBox(
            height: 52,
            child: ListView(
              scrollDirection: Axis.horizontal,
              padding: const EdgeInsets.fromLTRB(12, 8, 12, 4),
              children: [
                for (final f in MatchFilter.values)
                  Padding(
                    padding: const EdgeInsets.only(right: 8),
                    child: ChoiceChip(
                      label: Text(f.label),
                      selected: f == _filter,
                      side: BorderSide(color: f == _filter ? gold : border),
                      onSelected: (_) => setState(() => _filter = f),
                    ),
                  ),
              ],
            ),
          ),
          const _Explainer(),
          if (byLeague.isEmpty)
            const Padding(
              padding: EdgeInsets.all(24),
              child: Text('Ningún partido con este filtro.', style: TextStyle(color: muted)),
            ),
          for (final entry in byLeague.entries)
            Container(
              margin: const EdgeInsets.fromLTRB(12, 8, 12, 4),
              decoration: BoxDecoration(
                color: surface,
                border: Border.all(color: border),
                borderRadius: BorderRadius.circular(10),
              ),
              clipBehavior: Clip.antiAlias,
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Container(
                    color: surface2,
                    padding: const EdgeInsets.fromLTRB(14, 10, 14, 10),
                    child: Text(entry.key, style: const TextStyle(fontWeight: FontWeight.w700)),
                  ),
                  for (final m in entry.value) MatchTile(match: m),
                ],
              ),
            ),
        ],
      ),
    );
  }
}

class _Badge extends StatelessWidget {
  final String text;
  final Color color;
  const _Badge(this.text, this.color);

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 1),
        decoration: BoxDecoration(
          border: Border.all(color: color),
          borderRadius: BorderRadius.circular(999),
        ),
        child: Text(text,
            style: TextStyle(color: color, fontSize: 11, fontWeight: FontWeight.w600)),
      );
}

/// Data vs analysis, labelled (CLAUDE.md: the user must tell them apart).
class _Explainer extends StatelessWidget {
  const _Explainer();

  @override
  Widget build(BuildContext context) {
    return const Padding(
      padding: EdgeInsets.fromLTRB(16, 6, 16, 6),
      child: Wrap(
        crossAxisAlignment: WrapCrossAlignment.center,
        spacing: 6,
        runSpacing: 6,
        children: [
          _Badge('Dato', textColor),
          Text('hora, estado y marcador ·', style: TextStyle(color: muted, fontSize: 12)),
          _Badge('Análisis', gold),
          Text('cuota de 1 casa y su probabilidad sin margen: no es un pronóstico.',
              style: TextStyle(color: muted, fontSize: 12)),
        ],
      ),
    );
  }
}

class Crest extends StatelessWidget {
  final String? teamId;
  final String name;
  const Crest({super.key, required this.teamId, required this.name});

  @override
  Widget build(BuildContext context) {
    final fallback = Container(
      width: 26,
      height: 26,
      alignment: Alignment.center,
      decoration: BoxDecoration(
        color: surface2,
        shape: BoxShape.circle,
        border: Border.all(color: border),
      ),
      child: Text(name.isEmpty ? '?' : name.substring(0, 1),
          style: const TextStyle(color: muted, fontSize: 12, fontWeight: FontWeight.w600)),
    );
    final url = crestUrl(teamId);
    if (url == null) return fallback;
    return Image.network(
      url,
      width: 26,
      height: 26,
      fit: BoxFit.contain,
      errorBuilder: (context, error, stack) => fallback,
    );
  }
}

class MatchTile extends StatelessWidget {
  final Match match;
  const MatchTile({super.key, required this.match});

  String? get _statusText => switch (match.status) {
        MatchStatus.live => 'En vivo',
        MatchStatus.finished =>
          match.statusNote == null ? 'Final' : 'Final (${match.statusNote})',
        MatchStatus.postponed => 'Aplazado',
        MatchStatus.cancelled => 'Cancelado',
        MatchStatus.unknown => 'Estado desconocido',
        MatchStatus.scheduled => null,
      };

  @override
  Widget build(BuildContext context) {
    final m = match;
    final isLive = m.status == MatchStatus.live;
    final hasScore = m.homeScore != null && m.awayScore != null;
    final status = _statusText;
    return Container(
      decoration: BoxDecoration(
        color: isLive ? live.withValues(alpha: 0.06) : null,
        border: const Border(top: BorderSide(color: border)),
      ),
      padding: const EdgeInsets.fromLTRB(14, 12, 14, 12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              SizedBox(
                width: 62,
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(m.timeConfirmed ? hhmm(m.startsAt) : 'por conf.',
                        style: const TextStyle(fontWeight: FontWeight.w600)),
                    if (status != null)
                      Row(children: [
                        if (isLive) ...[const LiveDot(), const SizedBox(width: 4)],
                        Flexible(
                          child: Text(status,
                              overflow: TextOverflow.ellipsis,
                              style: TextStyle(fontSize: 11, color: isLive ? live : muted)),
                        ),
                      ]),
                  ],
                ),
              ),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    _TeamRow(id: m.homeId, name: m.home, score: hasScore ? m.homeScore : null),
                    const SizedBox(height: 6),
                    _TeamRow(id: m.awayId, name: m.away, score: hasScore ? m.awayScore : null),
                  ],
                ),
              ),
            ],
          ),
          if (m.line != null) ...[
            const SizedBox(height: 10),
            _LineView(line: m.line!),
          ] else if (m.status == MatchStatus.scheduled)
            const Padding(
              padding: EdgeInsets.only(top: 6, left: 62),
              child: Text('sin cuotas', style: TextStyle(color: muted, fontSize: 12)),
            ),
        ],
      ),
    );
  }
}

class _TeamRow extends StatelessWidget {
  final String? id;
  final String name;
  final int? score;
  const _TeamRow({required this.id, required this.name, required this.score});

  @override
  Widget build(BuildContext context) => Row(
        children: [
          Crest(teamId: id, name: name),
          const SizedBox(width: 10),
          Expanded(child: Text(name, overflow: TextOverflow.ellipsis)),
          if (score != null)
            Text('$score',
                style: const TextStyle(color: gold, fontWeight: FontWeight.w700, fontSize: 16)),
        ],
      );
}

class _LineView extends StatelessWidget {
  final Line line;
  const _LineView({required this.line});

  @override
  Widget build(BuildContext context) {
    final probs = impliedProbabilities(line.prices);
    const labels = ['1', 'X', '2'];
    // Bar widths in per-mille from the exact probabilities (display only).
    int permille(Fraction p) =>
        int.parse((p * Fraction.of(1000)).toFixed(0)).clamp(1, 1000);
    return Padding(
      padding: const EdgeInsets.only(left: 62),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            children: [
              for (var i = 0; i < 3; i++)
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(labels[i], style: const TextStyle(color: muted, fontSize: 11)),
                      Text(formatOdds(line.prices[i]),
                          style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 16)),
                      Text(formatPercent(probs[i]),
                          style: const TextStyle(color: muted, fontSize: 11)),
                    ],
                  ),
                ),
            ],
          ),
          const SizedBox(height: 8),
          ClipRRect(
            borderRadius: BorderRadius.circular(999),
            child: SizedBox(
              height: 6,
              child: Row(
                children: [
                  for (var i = 0; i < 3; i++) ...[
                    if (i > 0) const SizedBox(width: 2),
                    Expanded(
                      flex: permille(probs[i]),
                      child: ColoredBox(color: [gold, muted, awayBlue][i]),
                    ),
                  ],
                ],
              ),
            ),
          ),
          const SizedBox(height: 6),
          const Text('1 casa', style: TextStyle(color: muted, fontSize: 11)),
        ],
      ),
    );
  }
}
