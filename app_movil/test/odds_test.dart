import 'package:apuestas_ia/odds.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('American to decimal, rounded half-even to 3 decimals like the backend', () {
    expect(americanToDecimal('+230')!.toFixed(3), '3.300');
    expect(americanToDecimal('+100')!.toFixed(3), '2.000');
    expect(americanToDecimal('-115')!.toFixed(3), '1.870'); // 1.8695...
    expect(americanToDecimal('-700')!.toFixed(3), '1.143');
    expect(americanToDecimal('-105')!.toFixed(3), '1.952');
    expect(americanToDecimal('+1300')!.toFixed(3), '14.000');
    expect(americanToDecimal(260)!.toFixed(3), '3.600');
    expect(americanToDecimal('EVEN')!.toFixed(3), '2.000');
  });

  test('values that are not a price are refused, never guessed', () {
    for (final bad in [null, '', 'OFF', '+50', '-99', '0', 'abc']) {
      expect(americanToDecimal(bad), isNull, reason: '$bad');
    }
  });

  test('half-even rounding', () {
    expect(Fraction.of(10005, 10000).toFixed(3), '1.000'); // 1.0005 -> even
    expect(Fraction.of(10015, 10000).toFixed(3), '1.002'); // 1.0015 -> even
    expect(Fraction.of(10016, 10000).toFixed(3), '1.002');
  });

  test('implied probabilities remove the margin and sum exactly 1', () {
    // Medellín - Once Caldas, 2026-10-17 (real line): -105 / +235 / +265.
    final prices = ['-105', '+235', '+265'].map((v) => americanToDecimal(v)!).toList();
    final p = impliedProbabilities(prices);
    expect(p.reduce((a, b) => a + b), Fraction.of(1));
    expect(p.map(formatPercent).toList(), ['47,23 %', '27,52 %', '25,26 %']);
  });

  test('Spanish formatting keeps every significant digit (min 2)', () {
    expect(formatOdds(americanToDecimal('+230')!), '3,30');
    expect(formatOdds(americanToDecimal('-105')!), '1,952');
    expect(formatOdds(americanToDecimal('+235')!), '3,35');
  });
}
