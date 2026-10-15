/**
 * Country display: the API sends the provider's English name ("Colombia", "England",
 * "World") and, when one exists, the ISO 3166-1 alpha-2 code. Spanish names come from the
 * runtime's Intl data; the UK nations and international competitions have no ISO code.
 */

const regionNames = new Intl.DisplayNames(["es"], { type: "region" });

const NO_ISO_NAMES: Record<string, string> = {
  World: "Internacional",
  England: "Inglaterra",
  Scotland: "Escocia",
  Wales: "Gales",
  "Northern-Ireland": "Irlanda del Norte",
};

export function countryName(name: string, code: string | null): string {
  if (code) {
    try {
      return regionNames.of(code) ?? name;
    } catch {
      return name;
    }
  }
  return NO_ISO_NAMES[name] ?? name.replaceAll("-", " ");
}

// Flag images (Windows has no flag emoji glyphs: a flag emoji renders as two letters
// there), proxied and cached by our own /flags route so the browser never calls the
// provider's CDN. UK nations use the provider's subdivision codes.
const FLAG_SLUGS: Record<string, string> = {
  England: "gb-eng",
  Scotland: "gb-sct",
  Wales: "gb-wls",
  "Northern-Ireland": "gb-nir",
};

/** Flag image URL, or null when there is none (international competitions). */
export function countryFlagUrl(name: string, code: string | null): string | null {
  const slug = code ? code.toLowerCase() : FLAG_SLUGS[name];
  return slug ? `/flags/${slug}.svg` : null;
}
