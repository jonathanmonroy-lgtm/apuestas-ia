import type { NextRequest } from "next/server";

/**
 * Flag images served from our own origin: the browser never talks to the provider's CDN
 * (no third-party request carrying the user's IP), and each flag is fetched upstream
 * once and then cached (here and in the browser) for 30 days.
 */
const SLUG = /^[a-z]{2}(-[a-z]{3})?$/; // "co", "gb-eng"
const CACHE = "public, max-age=2592000, immutable";

export async function GET(_req: NextRequest, ctx: RouteContext<"/flags/[slug]">) {
  const { slug } = await ctx.params;
  const code = slug.replace(/\.svg$/, "");
  if (!SLUG.test(code)) return new Response("bad flag code", { status: 400 });
  const upstream = await fetch(`https://media.api-sports.io/flags/${code}.svg`, {
    cache: "force-cache",
  }).catch(() => null);
  if (!upstream?.ok) return new Response(null, { status: 404 });
  return new Response(await upstream.arrayBuffer(), {
    headers: { "Content-Type": "image/svg+xml", "Cache-Control": CACHE },
  });
}
