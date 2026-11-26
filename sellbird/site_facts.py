"""What Sellbird has already read off a company's own website, for the writer.

The free homepage signals (missing structured data, a site nobody has
touched in months, ...) sit in ``observations`` and show up
in cohorts and the dashboard, but the writer's prompt only carried the
company's description, detected tools and a few other signals. An email that
should name one concrete thing the sender would change had nothing true to
name, so it said it hadn't looked.

This turns the findings that are actually true into fact lines. Every line
says how it was found and that the check can miss things, so the writer
hedges instead of presenting a scan as an audit. A signal checked and found
absent is not a fact and is left out.

NO_ONLINE_BOOKING is deliberately not here. A booking widget that loads late,
or a "book" button on another page, is invisible to a read of the homepage
HTML: against real sites it called a bookable business unbookable in about
half of the cases checked, and an email telling an owner to add what they
already have is the worst first impression. It stays a signal for cohorts and
the dashboard, where a human reads it as a lead to check, not as a claim.
"""

from datetime import datetime, timedelta, timezone

# A site nobody has changed for this long reads as neglected; a few weeks doesn't.
STALE_DAYS = 180
# Pages in sitemap.xml at or below this make a very small site.
THIN_PAGES = 5
# A finding read longer ago than this may no longer be true (the owner may
# have updated the site since), so it isn't stated to them as current.
FRESH_DAYS = 30

HEADER = (
    "- Site checks (an automated read of their public pages; it can miss things). "
    "Use at most ONE, only if it fits this kind of business, put it as what you "
    "would do in their place, and never call it an audit or say you went through "
    "the site:"
)


async def site_facts(state, company_id: str) -> list[str]:
    """Fact lines for the writer's prompt, or [] when nothing true is known.

    A failed read is just "nothing known", never a reason not to write.
    """
    if not company_id or state is None:
        return []
    lookup = getattr(state, "get_observations", None)
    if lookup is None:
        return []
    try:
        rows = await lookup(company_id=company_id, latest_only=True, limit=500)
    except Exception:
        return []
    newest = {r.get("signal_code"): r for r in rows}
    latest = {code: r for code, r in newest.items() if _fresh(r)}
    current = {code: r.get("value_num") for code, r in latest.items()}

    facts: list[str] = []
    # Where they rank: a fact only with the search that produced it, and only
    # when it's a problem (4th or lower). Top three is not something to pitch.
    facts.extend(await _rank_facts(lookup, company_id))
    # Problems the owner can see for themselves come first: they are what
    # makes a redesign mockup worth opening.
    if current.get("NOT_MOBILE_FRIENDLY") == 1:
        facts.append(
            "  - Their homepage has no mobile viewport tag, so on a phone it shows "
            "as a shrunken desktop page that visitors have to pinch and zoom")
    if current.get("NO_HTTPS") == 1:
        facts.append(
            "  - Their site only loaded over plain http, so browsers show "
            "\"Not secure\" next to their address")
    year = current.get("OLD_COPYRIGHT")
    if year:
        facts.append(
            f"  - The copyright line on their homepage still says {int(year)}, "
            "which can make visitors wonder whether the business is still active")
    if current.get("RUNNING_GOOGLE_ADS") == 1:
        facts.append(
            "  - Their homepage carries Google Ads conversion tracking, so they are "
            "probably paying for Google Ads that send visitors to this site")
    elif current.get("RUNNING_META_ADS") == 1:
        facts.append(
            "  - Their homepage carries the Meta ad pixel, so they probably run "
            "Facebook or Instagram ads that send visitors to this site")
    if current.get("NO_SCHEMA_MARKUP") == 1:
        facts.append(
            "  - Their homepage has no JSON-LD structured data (schema.org markup), "
            "so search engines get less to show about them, such as opening hours, "
            "contact details or reviews")
    age = current.get("BLOG_STALE")
    if age is not None and age >= STALE_DAYS:
        facts.append(
            f"  - The newest date in their sitemap.xml is about {int(age)} days old")
    pages = current.get("SITE_PAGE_COUNT")
    if pages is not None and 0 < pages <= THIN_PAGES:
        facts.append(f"  - Their sitemap.xml lists only {int(pages)} page(s)")
    if current.get("BLOCKS_AI_CRAWLERS") == 1:
        facts.append(
            "  - Their robots.txt blocks AI crawlers, so AI search answers can't "
            "read their site")
    out = [HEADER] + facts if facts else []
    listing = _review_facts(newest)
    return out + [LISTING_HEADER] + listing if listing else out


LISTING_HEADER = (
    "- From their Google listing. Use it to make the offer about them (what the "
    "mockup would show), never as flattery or as the problem:"
)
# A good reputation worth putting on a homepage; a low one is never named.
MIN_GOOD_RATING = 4.0
MIN_REVIEWS = 5
# Ratings move slowly, so a reading stays usable longer than a site check.
REVIEW_FRESH_DAYS = 90


def _review_facts(newest: dict) -> list[str]:
    rating_row, count_row = newest.get("REVIEW_RATING"), newest.get("REVIEW_COUNT")
    if not rating_row or not count_row:
        return []
    if not (_fresh(rating_row, REVIEW_FRESH_DAYS) and _fresh(count_row, REVIEW_FRESH_DAYS)):
        return []
    try:
        rating = float(rating_row.get("value_num") or 0)
        count = int(float(count_row.get("value_num") or 0))
    except (TypeError, ValueError):
        return []
    if rating < MIN_GOOD_RATING or count < MIN_REVIEWS:
        return []
    return [f"  - They have a {rating:.1f}-star rating from about {count} Google reviews"]


# Organic positions beyond this are "not found" territory: below it the
# results are mostly directories, and the number says little.
MAX_PITCH_RANK = 30


RANK_CODES = ("SERP_RANK", "MAPS_RANK", "IN_LOCAL_PACK")


async def _best_ranks(lookup, company_id: str) -> dict[str, tuple[int, str]]:
    """{code: (best position, its phrase)} over every fresh search.

    A business is searched under several phrases ("tree trimming Atlanta",
    "landscaping Atlanta"); the newest reading per phrase counts, and the
    best of those is the one named. That is the search it is most known for,
    so a landscaper is never told where it ranks for tree trimming when it
    holds 2nd for landscaping."""
    best: dict[str, tuple[int, str]] = {}
    for code in RANK_CODES:
        try:
            rows = await lookup(company_id=company_id, signal_code=code, latest_only=False, limit=500)
        except Exception:
            continue
        seen: set[str] = set()
        for row in rows:                       # newest first
            rank, phrase = _rank(row)
            key = phrase.lower()
            if key in seen:
                continue
            seen.add(key)
            if rank <= 0 or not phrase or not _fresh(row):
                continue
            if code not in best or rank < best[code][0]:
                best[code] = (rank, phrase)
    return best


async def _rank_facts(lookup, company_id: str) -> list[str]:
    """Google search and Maps positions worth naming, as bands, never an
    exact place: a position moves with where and when the search is made,
    and a number the owner can't reproduce reads as a made-up claim."""
    best = await _best_ranks(lookup, company_id)
    facts = []
    rank, phrase = best.get("SERP_RANK", (0, ""))
    if 4 <= rank <= MAX_PITCH_RANK:
        where = ("on page one but below the top three" if rank <= 10
                 else "on page two" if rank <= 20 else "on page three")
        facts.append(
            f"  - Searching Google for \"{phrase}\" from the city centre, they came up "
            f"{where} of the results, where few people click")
    rank, phrase = best.get("MAPS_RANK", (0, ""))
    if rank >= 4 and "IN_LOCAL_PACK" not in best:
        where = ("outside the top three" if rank <= 10
                 else "outside the top ten" if rank <= 20 else "outside the top twenty")
        facts.append(
            f"  - On Google Maps for \"{phrase}\", searched from the city centre, they "
            f"came up {where}; most people only look at the top three")
    return facts


def _rank(row) -> tuple[int, str]:
    if not row:
        return 0, ""
    try:
        rank = int(float(row.get("value_num") or 0))
    except (TypeError, ValueError):
        return 0, ""
    return rank, str(row.get("value_text") or "").strip()


def _fresh(row, days: int = FRESH_DAYS) -> bool:
    """Was this observation made within ``days``? One with no readable
    date is kept: the age can't be judged, and dropping it changes nothing
    that was true before."""
    raw = str(row.get("observed_at") or "").strip()
    if not raw:
        return True
    try:
        seen = datetime.fromisoformat(raw.replace("Z", "+00:00").replace(" ", "T", 1))
    except ValueError:
        return True
    if seen.tzinfo is None:
        seen = seen.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - seen <= timedelta(days=days)
