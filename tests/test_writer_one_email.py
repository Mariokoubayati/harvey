"""Who the Writer writes a first email to, and what happens when it doesn't.

- No personal first email means no email: the campaign's shared template is
  generic, and must never fill the gap for someone the writer skipped.
- One first email per business: two near-identical cold emails to the same
  company read as a mass mailing.
- Nobody on the do-not-contact list is drafted for: the sender would refuse
  them anyway.

Companies and people here are invented.
"""

import json

import pytest

from sellbird.agents.writer import (COLLEAGUE_STATUS, DO_NOT_CONTACT_STATUS, WRITE_ATTEMPTS_KEY,
                                    Writer)
from sellbird.config import ComplianceConfig
from sellbird.models.campaign import Campaign
from sellbird.models.company import Company
from sellbird.models.prospect import Prospect
from tests.test_writer_feedback import RecordingBrain, prospect, state, writer  # noqa: F401


async def campaign_of(state, cid):
    for status in ("draft", "cancelled", "active"):
        for c in await state.get_campaigns_by_status(status):
            if c.id == cid:
                return c
    raise AssertionError(f"no campaign {cid}")


async def person(state, first, company_id="", email_status="verified", status="new", score=0):
    return await state.add_prospect(Prospect(
        first_name=first, last_name="Doe", title="Owner", company=f"{first} Co",
        company_id=company_id, industry="Landscaping", email=f"{first.lower()}@{first.lower()}.example",
        email_status=email_status, status=status, score=score))


# ── no personal first email, no email ──


@pytest.mark.asyncio
async def test_a_skipped_person_is_taken_out_of_the_campaign(state):
    kept, skipped = await person(state, "Ana"), await person(state, "Ben")
    cid = await state.add_campaign(Campaign(id="", name="Tampa", prospect_ids=[kept, skipped]))
    campaign = await campaign_of(state, cid)
    w = writer(RecordingBrain({}), state, "gmail")
    await w._take_out_of_campaign(campaign, cid, [await state.get_prospect(skipped)], "Tampa")
    after = await campaign_of(state, cid)
    assert after.prospect_ids == [kept] and after.status == "draft"
    assert (await state.get_prospect(skipped)).status == "new"
    assert await state.get_setting(WRITE_ATTEMPTS_KEY + skipped) == "1"


@pytest.mark.asyncio
async def test_a_campaign_nobody_got_an_email_from_is_cancelled(state):
    only = await person(state, "Ana")
    cid = await state.add_campaign(Campaign(id="", name="Tampa", prospect_ids=[only]))
    w = writer(RecordingBrain({}), state, "gmail")
    await w._take_out_of_campaign(await campaign_of(state, cid), cid,
                                  [await state.get_prospect(only)], "Tampa")
    after = await campaign_of(state, cid)
    assert after.prospect_ids == [] and after.status == "cancelled"


@pytest.mark.asyncio
async def test_no_attempt_is_counted_when_the_model_was_unavailable(state):
    only = await person(state, "Ana")
    cid = await state.add_campaign(Campaign(id="", name="Tampa", prospect_ids=[only]))
    brain = RecordingBrain({})
    brain.paused_reason = "Codex login expired"
    await writer(brain, state, "gmail")._take_out_of_campaign(
        await campaign_of(state, cid), cid, [await state.get_prospect(only)], "Tampa")
    assert await state.get_setting(WRITE_ATTEMPTS_KEY + only) in (None, "")


@pytest.mark.asyncio
async def test_personalizing_reports_who_got_no_draft(state):
    pid = await person(state, "Ana")
    w = writer(RecordingBrain({"subject": "", "body": ""}), state, "gmail")
    undrafted = await w._personalize_first_emails("c1", [await state.get_prospect(pid)])
    assert [p.id for p in undrafted] == [pid]
    assert await state.get_outbox() == []


# ── one first email per business ──


@pytest.mark.asyncio
async def test_one_person_per_company_verified_first(state):
    co = await state.add_company(Company(name="Pine Landscaping", domain="pine.example"))
    risky = await person(state, "Ana", co, email_status="risky", score=90)
    verified = await person(state, "Ben", co, score=10)
    w = writer(RecordingBrain({}), state, "gmail")
    kept = await w._one_per_company([await state.get_prospect(risky), await state.get_prospect(verified)])
    assert [p.id for p in kept] == [verified]
    assert (await state.get_prospect(risky)).status == COLLEAGUE_STATUS


@pytest.mark.asyncio
async def test_no_first_email_when_a_colleague_is_already_written_to(state):
    co = await state.add_company(Company(name="Oak Tree Care", domain="oak.example"))
    await person(state, "Ana", co, status="queued")
    ben = await person(state, "Ben", co)
    w = writer(RecordingBrain({}), state, "gmail")
    assert await w._one_per_company([await state.get_prospect(ben)]) == []
    assert (await state.get_prospect(ben)).status == COLLEAGUE_STATUS


@pytest.mark.asyncio
async def test_different_companies_are_all_kept_in_order(state):
    a = await person(state, "Ana", await state.add_company(Company(name="A", domain="a.example")))
    b = await person(state, "Ben", await state.add_company(Company(name="B", domain="b.example")))
    w = writer(RecordingBrain({}), state, "gmail")
    kept = await w._one_per_company([await state.get_prospect(a), await state.get_prospect(b)])
    assert [p.id for p in kept] == [a, b]


# ── the do-not-contact list ──


@pytest.mark.asyncio
async def test_nobody_on_the_do_not_contact_list_is_drafted_for(state):
    blocked, fine = await person(state, "Ana"), await person(state, "Ben")
    w = writer(RecordingBrain({}), state, "gmail")
    w.config.compliance = ComplianceConfig(do_not_contact=["ana.example"])
    kept = await w._drop_do_not_contact([await state.get_prospect(blocked), await state.get_prospect(fine)])
    assert [p.id for p in kept] == [fine]
    assert (await state.get_prospect(blocked)).status == DO_NOT_CONTACT_STATUS


@pytest.mark.asyncio
async def test_an_empty_list_keeps_everyone(state):
    pid = await person(state, "Ana")
    w = writer(RecordingBrain({}), state, "gmail")
    assert [p.id for p in await w._drop_do_not_contact([await state.get_prospect(pid)])] == [pid]
