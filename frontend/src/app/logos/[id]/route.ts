import type { NextRequest } from "next/server";

/**
 * Team crests served from our own origin, like /flags: the browser never talks to ESPN's
 * CDN, and each crest (80 px, ~7 KB) is fetched upstream once and cached for 30 days.
 * A team without a crest gets a neutral shield instead of a broken image.
 */
const ID = /^\d{1,10}$/;
const CACHE = "public, max-age=2592000, immutable";
const SHIELD = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 40"><path d="M20 3 6 8v11c0 9 6 15 14 18 8-3 14-9 14-18V8z" fill="#141f19" stroke="#22312a" stroke-width="2"/></svg>`;

export async function GET(_req: NextRequest, ctx: RouteContext<"/logos/[id]">) {
  const { id: raw } = await ctx.params;
  const id = raw.replace(/\.png$/, "");
  if (!ID.test(id)) return new Response("bad team id", { status: 400 });
  const upstream = await fetch(
    `https://a.espncdn.com/combiner/i?img=/i/teamlogos/soccer/500/${id}.png&h=80&w=80`,
    { cache: "force-cache" },
  ).catch(() => null);
  if (!upstream?.ok) {
    return new Response(SHIELD, {
      headers: { "Content-Type": "image/svg+xml", "Cache-Control": "public, max-age=86400" },
    });
  }
  return new Response(await upstream.arrayBuffer(), {
    headers: { "Content-Type": "image/png", "Cache-Control": CACHE },
  });
}
