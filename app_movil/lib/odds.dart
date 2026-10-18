/// Exact odds arithmetic: no floating point (a rule of the project, see CLAUDE.md).
///
/// ESPN publishes American odds ("+230", "-115"). They are converted to decimal odds,
/// rounded to 3 decimals exactly like the backend stores them (half-even), and the implied
/// probability is computed on those rounded prices.
library;

class Fraction implements Comparable<Fraction> {
  final BigInt n;
  final BigInt d;

  factory Fraction(BigInt n, BigInt d) {
    if (d == BigInt.zero) throw ArgumentError('denominator 0');
    if (d.isNegative) {
      n = -n;
      d = -d;
    }
    final g = n.gcd(d);
    return g == BigInt.zero || g == BigInt.one
        ? Fraction._(n, d)
        : Fraction._(n ~/ g, d ~/ g);
  }

  const Fraction._(this.n, this.d);

  factory Fraction.of(int n, [int d = 1]) =>
      Fraction(BigInt.from(n), BigInt.from(d));

  Fraction operator +(Fraction o) => Fraction(n * o.d + o.n * d, d * o.d);
  Fraction operator -(Fraction o) => Fraction(n * o.d - o.n * d, d * o.d);
  Fraction operator *(Fraction o) => Fraction(n * o.n, d * o.d);
  Fraction operator /(Fraction o) => Fraction(n * o.d, d * o.n);
  Fraction inverse() => Fraction(d, n);

  @override
  int compareTo(Fraction o) => (n * o.d).compareTo(o.n * d);
  bool operator >(Fraction o) => compareTo(o) > 0;
  bool operator <=(Fraction o) => compareTo(o) <= 0;

  @override
  bool operator ==(Object o) => o is Fraction && n == o.n && d == o.d;
  @override
  int get hashCode => Object.hash(n, d);

  /// Rounded to [digits] decimals, half to even (as Python's ROUND_HALF_EVEN).
  Fraction roundTo(int digits) {
    final scale = BigInt.from(10).pow(digits);
    final scaled = n * scale;
    var q = scaled ~/ d; // truncates toward zero
    final r = scaled - q * d;
    final twice = r.abs() * BigInt.two;
    final away = twice > d || (twice == d && q.isOdd);
    if (away) q += scaled.isNegative ? -BigInt.one : BigInt.one;
    return Fraction(q, scale);
  }

  /// "1.952" with exactly [digits] decimals (after rounding), '.' separator.
  String toFixed(int digits) {
    final scale = BigInt.from(10).pow(digits);
    final q = roundTo(digits);
    final units = q.n * scale ~/ q.d; // exact after rounding
    final neg = units.isNegative;
    final s = units.abs().toString().padLeft(digits + 1, '0');
    final intPart = s.substring(0, s.length - digits);
    final frac = s.substring(s.length - digits);
    return '${neg ? '-' : ''}$intPart${digits > 0 ? '.$frac' : ''}';
  }

  @override
  String toString() => '$n/$d';
}

final _hundred = Fraction.of(100);

/// '+230' -> 3.3, '-115' -> 1.87 (3 decimals, half-even). null when it is not a usable
/// American price ('OFF', '+50', '0', missing): refuse rather than guess.
Fraction? americanToDecimal(Object? value) {
  if (value == null) return null;
  final text = value.toString().trim();
  if (text.toUpperCase() == 'EVEN') return Fraction.of(2);
  final american = int.tryParse(text.startsWith('+') ? text.substring(1) : text);
  if (american == null) return null;
  final Fraction price;
  if (american >= 100) {
    price = Fraction.of(1) + Fraction.of(american) / _hundred;
  } else if (american <= -100) {
    price = Fraction.of(1) + _hundred / Fraction.of(-american);
  } else {
    return null;
  }
  return price.roundTo(3);
}

/// Implied probability of each outcome with the bookmaker margin removed
/// (inverse of each price, normalized to sum 1). What the price implies, not a forecast.
List<Fraction> impliedProbabilities(List<Fraction> prices) {
  final inverses = prices.map((p) => p.inverse()).toList();
  final total = inverses.reduce((a, b) => a + b);
  return inverses.map((i) => i / total).toList();
}

/// Decimal odds in Spanish format with every significant digit (min 2): 3.3 -> "3,30",
/// 1.952 -> "1,952".
String formatOdds(Fraction price) {
  var s = price.toFixed(3);
  while (s.endsWith('0') && s.split('.')[1].length > 2) {
    s = s.substring(0, s.length - 1);
  }
  return s.replaceAll('.', ',');
}

/// Probability as a percentage with 2 decimals: "47,23 %".
String formatPercent(Fraction p) =>
    '${(p * Fraction.of(100)).toFixed(2).replaceAll('.', ',')} %';
