/// League slug prefix ('col' in 'col.1') -> country, in Spanish, with its flag.
/// Same table as the backend (backend/app/services/fixtures_provider/espn.py).
library;

class Country {
  final String name; // Spanish
  final String? iso2; // ISO 3166-1 alpha-2, null for UK nations and international play
  final String flag; // emoji

  const Country(this.name, this.iso2, this.flag);

  bool get isInternational => this == international;

  static const international = Country('Internacional', null, '🌍');
}

String _flagOf(String iso2) => String.fromCharCodes(
      iso2.toUpperCase().codeUnits.map((c) => 0x1F1E6 + c - 0x41),
    );

// UK nations have no ISO alpha-2 code: subdivision flags (tag sequences).
const _england = '🏴\u{E0067}\u{E0062}\u{E0065}\u{E006E}\u{E0067}\u{E007F}';
const _scotland = '🏴\u{E0067}\u{E0062}\u{E0073}\u{E0063}\u{E0074}\u{E007F}';
const _wales = '🏴\u{E0067}\u{E0062}\u{E0077}\u{E006C}\u{E0073}\u{E007F}';

const Map<String, (String, String?)> _byPrefix = {
  'arg': ('Argentina', 'AR'), 'aus': ('Australia', 'AU'), 'aut': ('Austria', 'AT'),
  'bel': ('Bélgica', 'BE'), 'bol': ('Bolivia', 'BO'), 'bra': ('Brasil', 'BR'),
  'can': ('Canadá', 'CA'), 'chi': ('Chile', 'CL'), 'chn': ('China', 'CN'),
  'col': ('Colombia', 'CO'), 'crc': ('Costa Rica', 'CR'), 'cyp': ('Chipre', 'CY'),
  'den': ('Dinamarca', 'DK'), 'ecu': ('Ecuador', 'EC'), 'eng': ('Inglaterra', null),
  'esp': ('España', 'ES'), 'fra': ('Francia', 'FR'), 'ger': ('Alemania', 'DE'),
  'gre': ('Grecia', 'GR'), 'gua': ('Guatemala', 'GT'), 'hon': ('Honduras', 'HN'),
  'idn': ('Indonesia', 'ID'), 'ind': ('India', 'IN'), 'irl': ('Irlanda', 'IE'),
  'isr': ('Israel', 'IL'), 'ita': ('Italia', 'IT'), 'jam': ('Jamaica', 'JM'),
  'jpn': ('Japón', 'JP'), 'kor': ('Corea del Sur', 'KR'), 'ksa': ('Arabia Saudí', 'SA'),
  'mex': ('México', 'MX'), 'mys': ('Malasia', 'MY'), 'ned': ('Países Bajos', 'NL'),
  'nir': ('Irlanda del Norte', null), 'nor': ('Noruega', 'NO'), 'pan': ('Panamá', 'PA'),
  'par': ('Paraguay', 'PY'), 'per': ('Perú', 'PE'), 'por': ('Portugal', 'PT'),
  'rsa': ('Sudáfrica', 'ZA'), 'rus': ('Rusia', 'RU'), 'sco': ('Escocia', null),
  'slv': ('El Salvador', 'SV'), 'sui': ('Suiza', 'CH'), 'swe': ('Suecia', 'SE'),
  'tha': ('Tailandia', 'TH'), 'tur': ('Turquía', 'TR'), 'uru': ('Uruguay', 'UY'),
  'usa': ('Estados Unidos', 'US'), 'ven': ('Venezuela', 'VE'), 'wal': ('Gales', null),
};

const _ukFlags = {'eng': _england, 'sco': _scotland, 'wal': _wales, 'nir': '🇬🇧'};

final Map<String, Country> _cache = {};

/// 'col.1' -> Colombia; confederations, FIFA, friendlies or unknown -> Internacional.
Country countryOfSlug(String slug) {
  final prefix = slug.split('.').first;
  return _cache.putIfAbsent(prefix, () {
    final entry = _byPrefix[prefix];
    if (entry == null) return Country.international;
    final (name, iso2) = entry;
    return Country(name, iso2, iso2 != null ? _flagOf(iso2) : _ukFlags[prefix] ?? '🏳');
  });
}
