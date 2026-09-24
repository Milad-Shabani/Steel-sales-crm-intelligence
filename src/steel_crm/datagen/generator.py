"""Generate a synthetic Microsoft Dynamics 365 Sales export for a steel trader.

The output mirrors what Azure Synapse Link for Dataverse (or the older Data
Export Service) lands in a data lake: one CSV per table, named and columned
with Dataverse logical names (`opportunity.estimatedvalue`,
`lead.leadsourcecode`, ...), option sets stored as integer codes, state and
status codes, GUID keys, UTC timestamps, and the metadata files that turn
codes back into labels (`OptionsetMetadata.csv`, `GlobalOptionsetMetadata.csv`,
`StateMetadata.csv`, `StatusMetadata.csv`). Custom columns and tables use
the publisher prefix `ahn_`, as a real customization would. One deliberate
difference: each CSV carries a header row, so the files are readable on
their own (Synapse Link keeps the schema in model.json instead).

The simulation runs the whole commercial cycle: campaigns produce
responses, some responses become leads, qualified leads become accounts and
opportunities, opportunities get short-lived quotes (steel prices move too
fast for long ones), won deals become sales orders, invoices and payments,
and customers come back for repeat business until they churn. Win chances
depend on things a real trader would recognise: how fast the quote went
out, the discount, the competitor, deal size, the rep, the channel, and
whether prices are about to rise.
"""

from __future__ import annotations

import math
import random
import uuid
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from . import catalog as C

TEHRAN_UTC_OFFSET = pd.Timedelta(hours=3, minutes=30)
IRR_CURRENCY_ID = "7f5b6f7e-1c2d-4a4e-9a51-3a3f1d3c0a01"
TON_UOM_ID = "3b0b0e6c-5a36-4b8e-8c1e-0d7c5e2f9a11"

STAGES = [(0, "1-Qualify", 20), (1, "2-Develop", 40), (2, "3-Propose", 60), (3, "4-Close", 80)]
FORECAST = {"Pipeline": 100000001, "Best case": 100000002, "Committed": 100000003,
            "Omitted": 100000004, "Won": 100000005, "Lost": 100000006}
LOSS_REASONS = {
    100000000: "Price",
    100000001: "Lost to Competitor",
    100000002: "Slow Quote / Price Expired",
    100000003: "Credit Terms",
    100000004: "Delivery Lead Time",
    100000005: "Spec / Quality",
    100000006: "Project Postponed",
    100000007: "Bought Direct from Mill",
}
LOSS_CODE = {label: code for code, label in LOSS_REASONS.items()}
JOB_TITLES = ["Purchasing Manager", "Procurement Lead", "Project Manager", "Managing Director",
              "Supply Chain Manager", "Site Engineer"]
ACTIVITY_TYPES = ["phonecall", "email", "appointment", "task"]
ACTIVITY_WEIGHTS = [0.55, 0.30, 0.10, 0.05]


def _sigmoid(z: float) -> float:
    return 1.0 / (1.0 + math.exp(-z))


def _logit(p: float) -> float:
    return math.log(p / (1 - p))


@dataclass
class _Account:
    id: str
    name: str
    industry: C.Industry
    province: str
    rep: int
    terms: int
    created: pd.Timestamp
    customer_since: pd.Timestamp | None = None


@dataclass
class _OppSpec:
    account: _Account
    created: pd.Timestamp
    rep: int
    group: str
    tons: float
    is_repeat: bool
    win_logit: float
    lead_id: str | None = None
    campaign_id: str | None = None
    lead_row: dict | None = None


class DataverseExportGenerator:
    def __init__(self, seed: int = 42, start: str = "2024-07-01", end: str = "2026-06-30"):
        self.rng = np.random.default_rng(seed)
        self._ids = random.Random(seed)
        self.start = pd.Timestamp(start)
        self.end = pd.Timestamp(end) + pd.Timedelta(hours=23, minutes=59)
        self.months = pd.period_range(self.start, self.end, freq="M")
        self.rows: dict[str, list[dict]] = defaultdict(list)
        self.accounts: list[_Account] = []
        self._names: set[str] = set()
        self._counters: dict[str, int] = defaultdict(int)

    # ------------------------------------------------------------------ helpers
    def uid(self) -> str:
        return str(uuid.UUID(int=self._ids.getrandbits(128), version=4))

    def _number(self, prefix: str) -> str:
        self._counters[prefix] += 1
        return f"{prefix}-{self._counters[prefix]:06d}"

    def _choice(self, options: dict, weights_key=None):
        keys = list(options)
        w = np.array([options[k] if weights_key is None else weights_key(options[k]) for k in keys],
                     dtype=float)
        return keys[int(self.rng.choice(len(keys), p=w / w.sum()))]

    def _workday(self, ts: pd.Timestamp) -> pd.Timestamp:
        # Friday is the weekend in Iran; business activity moves to Saturday.
        return ts + pd.Timedelta(days=1) if ts.weekday() == 4 else ts

    def _biz_time(self, day: pd.Timestamp) -> pd.Timestamp:
        day = self._workday(day.normalize())
        return day + pd.Timedelta(minutes=int(self.rng.integers(8 * 60, 17 * 60)))

    def _between(self, a: pd.Timestamp, b: pd.Timestamp) -> pd.Timestamp:
        if b <= a:
            return a
        span = (b - a).total_seconds()
        return self._workday(a + pd.Timedelta(seconds=float(self.rng.uniform(0, span))))

    def _season(self, ts: pd.Timestamp) -> float:
        return C.SEASONALITY[ts.month]

    # ------------------------------------------------------------------ prices
    def _build_prices(self) -> None:
        level, self.index = 1.0, {}
        periods = list(self.months) + [self.months[-1] + 1]
        for i, p in enumerate(periods):
            if i:
                level *= 1 + C.PRICE_DRIFT + C.PRICE_SHOCKS.get(str(p), 0) + self.rng.normal(0, C.PRICE_NOISE)
            self.index[p] = level
        self.products = []
        for number, name, group, grade, base in C.PRODUCTS:
            self.products.append(dict(id=self.uid(), number=number, name=name, group=group,
                                      grade=grade, base=base))
        self.by_group = defaultdict(list)
        for prod in self.products:
            self.by_group[prod["group"]].append(prod)
        # list price per product and month, IRR per ton
        self.list_price = {}
        for prod in self.products:
            for p in periods:
                noise = 1 + self.rng.normal(0, 0.008)
                self.list_price[(prod["number"], p)] = round(prod["base"] * self.index[p] * noise, 1) * 1e6

    def price(self, prod: dict, ts: pd.Timestamp) -> float:
        return self.list_price[(prod["number"], ts.to_period("M"))]

    def cost(self, prod: dict, ts: pd.Timestamp) -> float:
        # Stock is bought from the mills about a month ahead, so the cost of
        # goods follows last month's price: margins widen when prices jump.
        p = ts.to_period("M")
        prev = p - 1 if (prod["number"], p - 1) in self.list_price else p
        margin = C.PRODUCT_GROUPS[C.GROUP_CODE[prod["group"]]][1]
        return self.list_price[(prod["number"], prev)] * (1 - margin)

    def price_trend(self, ts: pd.Timestamp) -> float:
        p = ts.to_period("M")
        return self.index[p + 1] / self.index[p] - 1 if p + 1 in self.index else C.PRICE_DRIFT

    # ------------------------------------------------------------------ reference tables
    def _reference_tables(self) -> None:
        created = pd.Timestamp("2019-03-02 09:00")
        self.territory_ids = {t: self.uid() for t in C.TERRITORIES}
        self.manager_id = self.uid()
        first, last = C.SALES_MANAGER[0].split(" ", 1)
        self.rows["systemuser"].append(dict(
            systemuserid=self.manager_id, fullname=C.SALES_MANAGER[0], firstname=first, lastname=last,
            title=C.SALES_MANAGER[1], territoryid=None, isdisabled=False, createdon=created))
        for t, tid in self.territory_ids.items():
            self.rows["territory"].append(dict(territoryid=tid, name=t, managerid=self.manager_id,
                                               createdon=created))
        self.rep_ids = []
        for rep in C.REPS:
            rid = self.uid()
            self.rep_ids.append(rid)
            first, last = rep.name.split(" ", 1)
            self.rows["systemuser"].append(dict(
                systemuserid=rid, fullname=rep.name, firstname=first, lastname=last, title=rep.title,
                territoryid=self.territory_ids[rep.territory], isdisabled=False,
                createdon=created + pd.Timedelta(days=int(self.rng.integers(0, 900)))))
        self.rows["transactioncurrency"].append(dict(
            transactioncurrencyid=IRR_CURRENCY_ID, isocurrencycode="IRR", currencyname="Iranian Rial",
            currencysymbol="﷼", exchangerate=1.0))
        self.rows["uom"].append(dict(uomid=TON_UOM_ID, name="Ton", quantity=1.0, isschedulebaseuom=True))
        for prod in self.products:
            self.rows["product"].append(dict(
                productid=prod["id"], productnumber=prod["number"], name=prod["name"],
                ahn_productgroup=C.GROUP_CODE[prod["group"]], ahn_grade=prod["grade"],
                defaultuomid=TON_UOM_ID, quantitydecimal=2, statecode=0, statuscode=1,
                transactioncurrencyid=IRR_CURRENCY_ID, createdon=created))
        for p in self.months:
            plid = self.uid()
            begin = p.start_time
            self.rows["pricelevel"].append(dict(
                pricelevelid=plid, name=f"Steel Price List {p}", begindate=begin.date(),
                enddate=p.end_time.date(), transactioncurrencyid=IRR_CURRENCY_ID,
                statecode=0 if p == self.months[-1] else 1, createdon=begin - pd.Timedelta(days=1)))
            for prod in self.products:
                self.rows["productpricelevel"].append(dict(
                    productpricelevelid=self.uid(), pricelevelid=plid, productid=prod["id"],
                    uomid=TON_UOM_ID, amount=self.list_price[(prod["number"], p)],
                    transactioncurrencyid=IRR_CURRENCY_ID))
        self.competitor_ids = {}
        for name, _ in C.COMPETITORS:
            cid = self.uid()
            self.competitor_ids[name] = cid
            self.rows["competitor"].append(dict(competitorid=cid, name=name, createdon=created))

    # ------------------------------------------------------------------ accounts
    def _company_name(self, industry: C.Industry) -> str:
        cores = C.NAME_CORES[industry.label]
        for _ in range(60):
            core, suffix = self.rng.choice(cores), self.rng.choice(C.NAME_SUFFIXES)
            name = f"{self.rng.choice(C.NAME_PREFIXES)} {core}"
            if not core.endswith(("Industries", "Co.", "Group", "Mfg.")):
                name += f" {suffix}"
            if name not in self._names:
                self._names.add(name)
                return name
        while True:  # name space used up: pair two prefixes, as in "Arya-Zagros Steel Trading"
            name = f"{self.rng.choice(C.NAME_PREFIXES)}-{self.rng.choice(C.NAME_PREFIXES)} {self.rng.choice(cores)}"
            if name not in self._names:
                self._names.add(name)
                return name

    def _pick_rep(self, province: str, industry: C.Industry) -> int:
        territory = C.PROVINCES[province][0]
        big = industry.label in ("Government & Infrastructure Project", "Steel Distributor / Trader")
        if territory == "Tehran & North" and big and self.rng.random() < 0.7:
            return next(i for i, r in enumerate(C.REPS) if r.key_accounts)
        options = [i for i, r in enumerate(C.REPS) if r.territory == territory and not r.key_accounts]
        return int(self.rng.choice(options))

    def _pick_industry(self, skew: dict[str, float] | None = None) -> C.Industry:
        skew = skew or {}
        w = np.array([i.weight * skew.get(i.label, 1.0) for i in C.INDUSTRIES])
        return C.INDUSTRIES[int(self.rng.choice(len(w), p=w / w.sum()))]

    def _pick_province(self) -> str:
        return self._choice(C.PROVINCES, lambda v: v[1])

    def _new_account(self, industry, province, created, lead_id=None, rep=None) -> _Account:
        rep = self._pick_rep(province, industry) if rep is None else rep
        terms = self._choice(industry.terms)
        acc = _Account(self.uid(), self._company_name(industry), industry, province, rep, terms, created)
        self.accounts.append(acc)
        credit = industry.credit_limit_b * float(self.rng.lognormal(0, 0.35)) * 1e9
        self.rows["account"].append(dict(
            accountid=acc.id, name=acc.name, accountnumber=self._number("ACC"),
            industrycode=industry.code, customertypecode=8, address1_stateorprovince=province,
            address1_country="Iran", territoryid=self.territory_ids[C.PROVINCES[province][0]],
            ownerid=self.rep_ids[rep], paymenttermscode=terms, creditlimit=round(credit, -8),
            transactioncurrencyid=IRR_CURRENCY_ID, originatingleadid=lead_id, createdon=created,
            modifiedon=created, statecode=0, statuscode=1))
        return acc

    def _existing_accounts(self, n: int = 320) -> None:
        span = (self.start - pd.Timestamp("2019-06-01")).days - 30
        for _ in range(n):
            created = self._biz_time(pd.Timestamp("2019-06-01") + pd.Timedelta(days=int(self.rng.integers(0, span))))
            acc = self._new_account(self._pick_industry(), self._pick_province(), created)
            acc.customer_since = self.start - pd.Timedelta(days=1)

    # ------------------------------------------------------------------ activities
    def _activity(self, when, rep, regarding_id, regarding_type, subject) -> None:
        kind = ACTIVITY_TYPES[int(self.rng.choice(4, p=ACTIVITY_WEIGHTS))]
        done = when <= self.end
        self.rows["activitypointer"].append(dict(
            activityid=self.uid(), activitytypecode=kind, subject=subject,
            regardingobjectid=regarding_id, regardingobjecttypecode=regarding_type,
            ownerid=self.rep_ids[rep], createdon=when,
            actualend=when + pd.Timedelta(minutes=int(self.rng.integers(5, 90))) if done else None,
            statecode=1 if done else 0, statuscode=2 if done else 1))

    # ------------------------------------------------------------------ marketing
    def _campaigns(self) -> list[dict]:
        out = []
        for name, channel_label, start, days in C.CAMPAIGNS:
            ch = C.CHANNEL_BY_LABEL[channel_label]
            begin = pd.Timestamp(start)
            finish = min(begin + pd.Timedelta(days=days - 1), self.end.normalize())
            if begin > self.end or finish < self.start:
                continue
            begin = max(begin, self.start)
            budget = round(ch.cost_b * float(self.rng.lognormal(0, 0.15)) * 1e9, -7)
            done = finish < self.end.normalize()
            actual = round(budget * (1 + self.rng.normal(0.03, 0.08)), -6)
            if not done:  # spend so far on a running campaign
                actual = round(actual * ((finish - begin).days + 1) / days, -6)
            cid = self.uid()
            self.rows["campaign"].append(dict(
                campaignid=cid, name=name, codename=self._number("CMP"), typecode=ch.campaign_type,
                ahn_channel=ch.code, actualstart=begin.date(), actualend=finish.date(),
                budgetedcost=budget, totalactualcost=actual, ownerid=self.manager_id,
                createdon=self._biz_time(begin - pd.Timedelta(days=14)),
                statecode=0, statuscode=3 if done else 2, transactioncurrencyid=IRR_CURRENCY_ID))
            out.append(dict(id=cid, channel=ch, begin=begin, finish=finish, days=days))
        return out

    def _responses_and_leads(self, campaigns) -> list[_OppSpec]:
        specs = []
        for camp in campaigns:
            ch = camp["channel"]
            scale = ((camp["finish"] - camp["begin"]).days + 1) / camp["days"]
            n = int(self.rng.poisson(ch.responses * scale))
            for _ in range(n):
                received = self._biz_time(self._between(camp["begin"], camp["finish"] + pd.Timedelta(days=1)))
                if received > self.end:
                    continue
                code = int(self.rng.choice([1, 2, 3, 4], p=[0.55, 0.32, 0.09, 0.04]))
                industry = self._pick_industry(ch.industry_skew)
                rid = self.uid()
                row = dict(activityid=rid, regardingobjectid=camp["id"], responsecode=code,
                           channeltypecode=ch.response_channel, receivedon=received, createdon=received,
                           companyname=self._company_name(industry) if code == 1 else None,
                           ownerid=self.manager_id, statecode=1, statuscode=2)
                self.rows["campaignresponse"].append(row)
                if code == 1 and self.rng.random() < min(1.0, ch.lead_rate / 0.55):
                    created = self._biz_time(received + pd.Timedelta(days=int(self.rng.integers(0, 3))))
                    if created <= self.end:
                        spec = self._new_lead(created, ch.lead_source, industry, ch.qualify, ch.win_logit,
                                              ch.tons_mult, camp["id"], rid, row["companyname"])
                        if spec:
                            specs.append(spec)
        return specs

    def _organic_leads(self) -> list[_OppSpec]:
        specs = []
        for p in self.months:
            n = int(self.rng.poisson(15 * C.SEASONALITY[p.month]))
            for _ in range(n):
                code = self._choice(C.ORGANIC_SOURCES, lambda v: v[1])
                _, _, qualify, win_logit = C.ORGANIC_SOURCES[code]
                created = self._biz_time(p.start_time + pd.Timedelta(days=int(self.rng.integers(0, p.days_in_month))))
                if created > self.end:
                    continue
                industry = self._pick_industry()
                spec = self._new_lead(created, code, industry, qualify, win_logit, 1.0, None, None, None)
                if spec:
                    specs.append(spec)
        return specs

    def _new_lead(self, created, source, industry, qualify, win_logit, tons_mult,
                  campaign_id, response_id, company) -> _OppSpec | None:
        existing = None
        same = [a for a in self.accounts if a.industry is industry and a.created < created]
        if same and self.rng.random() < 0.12:
            existing = same[int(self.rng.integers(0, len(same)))]
        province = existing.province if existing else self._pick_province()
        rep = existing.rep if existing else self._pick_rep(province, industry)
        company = existing.name if existing else (company or self._company_name(industry))
        tons = max(5.0, round(float(self.rng.lognormal(math.log(industry.median_tons * tons_mult), 0.65)) / 5) * 5)
        group = self._choice(industry.product_mix)
        lead_id = self.uid()
        decided = self._biz_time(created + pd.Timedelta(days=1 + int(self.rng.poisson(3))))
        row = dict(
            leadid=lead_id, subject=f"{group}, {tons:.0f} t - {company}", companyname=company,
            jobtitle=str(self.rng.choice(JOB_TITLES)), industrycode=industry.code,
            address1_stateorprovince=province, leadsourcecode=source, campaignid=campaign_id,
            relatedobjectid=response_id, ahn_productgroup=C.GROUP_CODE[group],
            ahn_estimatedtonnage=max(5.0, round(tons * float(self.rng.lognormal(0, 0.25)) / 5) * 5),
            parentaccountid=existing.id if existing else None, ownerid=self.rep_ids[rep],
            createdon=created, modifiedon=created, statecode=0, statuscode=1,
            qualifyingopportunityid=None)
        self.rows["lead"].append(row)
        for _ in range(int(self.rng.integers(1, 4))):
            when = self._between(created, min(decided, self.end))
            self._activity(when, rep, lead_id, "lead", f"Follow up: {company}")
        if decided > self.end:
            row["statuscode"] = 2 if self.rng.random() < 0.5 else 1
            return None
        row["modifiedon"] = decided
        q = qualify * (1.1 if existing else 1.0)
        if self.rng.random() >= q:
            row["statecode"] = 2
            row["statuscode"] = int(self.rng.choice([4, 5, 6, 7], p=[0.35, 0.25, 0.30, 0.10]))
            return None
        account = existing or self._new_account(industry, province, decided, lead_id=lead_id, rep=rep)
        row["statecode"], row["statuscode"] = 1, 3
        if not existing:
            row["parentaccountid"] = account.id
        return _OppSpec(account, decided, rep, group, tons, False, win_logit, lead_id, campaign_id, row)

    # ------------------------------------------------------------------ opportunities
    def _quote_effect(self, hours: float, never_quoted: bool) -> float:
        if never_quoted:
            return -1.6
        if hours < 4:
            return 0.55
        if hours < 24:
            return 0.2
        if hours < 72:
            return -0.3
        return -0.9

    def _loss_reason(self, ind, disc, competitor, hours, never_quoted, trend, tons) -> str:
        w = {
            "Price": 0.8 + 2.5 * ind.price_sensitivity * (disc < 0.02),
            "Lost to Competitor": 2.2 if competitor else 0.3,
            "Slow Quote / Price Expired": 3.5 if (never_quoted or hours > 72) else (0.8 if hours > 24 else 0.1),
            "Credit Terms": 2.2 if ind.label in ("Government & Infrastructure Project",
                                                 "Construction Contractor") else 0.3,
            "Delivery Lead Time": 0.5,
            "Spec / Quality": 0.9 if ind.label == "Automotive & Appliance Parts" else 0.2,
            "Project Postponed": (1.6 if trend < 0 else 0.3) + (1.0 if "Government" in ind.label else 0),
            "Bought Direct from Mill": 1.4 if tons >= 300 else 0.15,
        }
        return self._choice(w)

    def _simulate(self, s: _OppSpec) -> dict:
        rng, ind, rep = self.rng, s.account.industry, C.REPS[s.rep]
        created = s.created
        trend = self.price_trend(created)
        competitor = rng.random() < ind.competitor_prob
        never_quoted = rng.random() < (0.05 if s.is_repeat else 0.09)
        hours = float(rng.lognormal(math.log(rep.quote_hours), 0.9))
        disc = (rep.discount * (0.5 + ind.price_sensitivity) * (1.35 if competitor else 1.0)
                * (0.8 if s.is_repeat else 1.0) + rng.normal(0, 0.005))
        disc = float(np.clip(disc, 0.0, 0.08))
        early = int(rng.poisson(rep.activity * 0.6))
        z = _logit(ind.base_win) + (0.55 if s.is_repeat else s.win_logit - 0.45) + rep.skill
        z += self._quote_effect(hours, never_quoted) + 9 * disc + 0.08 * min(early, 6)
        z -= 0.3 * math.log(s.tons / ind.median_tons) + (0.45 if competitor else 0.0)
        z += 5 * trend
        won = rng.random() < _sigmoid(z)
        if won and never_quoted:
            never_quoted, hours = False, min(hours, 20.0)
        base = (7 if s.is_repeat else 16) * (3 if "Government" in ind.label else 1)
        cycle = max(2.0, float(rng.lognormal(math.log(base), 0.5))) * (1.0 if won else 1.2)
        cycle *= 0.6 if never_quoted else 1.0
        close = self._biz_time(created + pd.Timedelta(days=cycle))
        is_open = close > self.end
        expected_close = (created + pd.Timedelta(days=base)).normalize()

        # product lines
        k = int(rng.choice([1, 2, 3], p=[0.55, 0.30, 0.15]))
        group_products = self.by_group[s.group]
        k = min(k, len(group_products))
        picks = [group_products[i] for i in rng.choice(len(group_products), size=k, replace=False)]
        shares = rng.dirichlet(np.ones(k) * 2)
        lines = [(p, max(0.5, round(s.tons * sh * 2) / 2)) for p, sh in zip(picks, shares)]
        tons = sum(t for _, t in lines)
        est_value = round(sum(t * self.price(p, created) for p, t in lines) * (1 - disc), -5)

        opp_id = self.uid()
        account = s.account
        # quotes
        quotes = []
        first_quote = created + pd.Timedelta(hours=hours)
        if won and first_quote >= close:  # a won deal was quoted before it closed
            first_quote = created + (close - created) / 2
        if not never_quoted and first_quote <= min(close, self.end):
            t, rev, qdisc = self._workday(first_quote), 0, disc
            while True:
                quotes.append((t, rev, qdisc))
                nxt = t + pd.Timedelta(days=float(rng.uniform(4, 8)))
                if nxt >= min(close, self.end):
                    break
                t, rev, qdisc = self._workday(nxt), rev + 1, min(0.09, qdisc + 0.002)
        quote_ids = []
        for i, (t, rev, qdisc) in enumerate(quotes):
            last = i == len(quotes) - 1
            if not last:
                state, status = 3, 7
            elif is_open:
                state, status = 1, 3
            elif won:
                state, status = 2, 4
            else:
                state, status = 3, 5
            total = round(sum(q * self.price(p, t) for p, q in lines) * (1 - qdisc), -5)
            qid = self.uid()
            quote_ids.append(qid)
            self.rows["quote"].append(dict(
                quoteid=qid, quotenumber=self._number("QUO"), revisionnumber=rev, opportunityid=opp_id,
                customerid=account.id, customeridtype="account", ownerid=self.rep_ids[s.rep],
                createdon=t, effectivefrom=t.date(), effectiveto=(t + pd.Timedelta(days=3)).date(),
                discountpercentage=round(qdisc * 100, 2), totalamount=total, statecode=state,
                statuscode=status, transactioncurrencyid=IRR_CURRENCY_ID))

        # activities on the deal
        until = min(close, self.end)
        for _ in range(early):
            self._activity(self._between(created, min(created + pd.Timedelta(days=3), until)),
                           s.rep, opp_id, "opportunity", f"Deal follow up: {account.name}")
        later = int(rng.poisson(rep.activity * 0.5 * max((until - created).days, 1) / 7))
        for _ in range(later):
            self._activity(self._between(created, until), s.rep, opp_id, "opportunity",
                           f"Deal follow up: {account.name}")

        # weekly pipeline snapshots (custom table fed by a scheduled flow)
        span = max((close - created).total_seconds(), 1.0)
        snap_days = [created.normalize()]
        d = created.normalize() + pd.Timedelta(days=(5 - created.weekday()) % 7 or 7)
        while d < min(close, self.end):
            snap_days.append(d)
            d += pd.Timedelta(days=7)
        prob, stage = 20, 0
        for d in snap_days:
            f = (d - created).total_seconds() / span
            quoted = bool(quotes) and d >= quotes[0][0].normalize()
            stage = (3 if f > 0.75 else 2) if quoted else (1 if f > 0.25 else 0)
            prob = STAGES[stage][2] + rep.optimism + (4 if won and stage >= 2 else 0) + rng.normal(0, 4)
            prob = int(np.clip(round(prob / 5) * 5, 5, 95))
            cat = "Committed" if prob >= 75 else "Best case" if prob >= 50 else "Pipeline"
            self.rows["ahn_pipelinesnapshot"].append(dict(
                ahn_pipelinesnapshotid=self.uid(), ahn_snapshotdate=d.date(), ahn_opportunityid=opp_id,
                ownerid=self.rep_ids[s.rep], ahn_stepname=STAGES[stage][1], ahn_closeprobability=prob,
                ahn_estimatedvalue=est_value, ahn_forecastcategory=FORECAST[cat],
                ahn_estimatedclosedate=expected_close.date()))

        # competitors
        comp_name = None
        if competitor:
            comp_name = self._choice(dict(C.COMPETITORS))
        reason = None
        if not is_open and not won:
            reason = self._loss_reason(ind, disc, competitor, hours, never_quoted, trend, tons)
            if reason == "Bought Direct from Mill":
                comp_name = "Mill Direct Sales"
            elif reason == "Lost to Competitor" and comp_name is None:
                comp_name = self._choice({n: w for n, w in C.COMPETITORS if n != "Mill Direct Sales"})
        if comp_name:
            self.rows["opportunitycompetitors"].append(dict(
                opportunitycompetitorid=self.uid(), opportunityid=opp_id,
                competitorid=self.competitor_ids[comp_name]))

        # the opportunity itself
        row = dict(
            opportunityid=opp_id, name=f"{tons:.0f} t {s.group} - {account.name}",
            customerid=account.id, customeridtype="account", originatingleadid=s.lead_id,
            campaignid=s.campaign_id, ownerid=self.rep_ids[s.rep], createdon=created,
            estimatedclosedate=expected_close.date(), estimatedvalue=est_value,
            ahn_productgroup=C.GROUP_CODE[s.group], ahn_tonnage=tons,
            ahn_isrepeatbusiness=s.is_repeat, transactioncurrencyid=IRR_CURRENCY_ID,
            actualclosedate=None, actualvalue=None, ahn_lossreason=None)
        category = "Committed" if prob >= 75 else "Best case" if prob >= 50 else "Pipeline"
        cancelled = reason in ("Project Postponed", "Credit Terms", "Delivery Lead Time", "Spec / Quality")
        if is_open:
            row.update(statecode=0, statuscode=1, closeprobability=prob, salesstage=stage,
                       stepname=STAGES[stage][1], msdyn_forecastcategory=FORECAST[category],
                       modifiedon=snap_days[-1] + pd.Timedelta(hours=10))
        elif won:
            row.update(statecode=1, statuscode=3, closeprobability=100, salesstage=3, stepname="4-Close",
                       msdyn_forecastcategory=FORECAST["Won"], actualclosedate=close.date(), modifiedon=close)
        else:
            row.update(statecode=2, statuscode=4 if cancelled else 5, closeprobability=0, salesstage=stage,
                       stepname=STAGES[stage][1], msdyn_forecastcategory=FORECAST["Lost"], actualclosedate=close.date(),
                       modifiedon=close, ahn_lossreason=LOSS_CODE[reason])
        if won and not is_open:
            row["actualvalue"] = self._order(s, opp_id, quote_ids[-1] if quote_ids else None, close, lines,
                                             quotes[-1][2] if quotes else disc)
            if account.customer_since is None or close < account.customer_since:
                account.customer_since = close
        self.rows["opportunity"].append(row)
        return row

    # ------------------------------------------------------------------ orders and invoices
    def _order(self, s, opp_id, quote_id, close, lines, disc) -> float:
        rng, account = self.rng, s.account
        fulfilled = self._biz_time(close + pd.Timedelta(days=int(rng.integers(1, 8))))
        so_id = self.uid()
        total = 0.0
        for seq, (prod, qty) in enumerate(lines, start=1):
            price = self.price(prod, close)
            base = qty * price
            discount = round(base * disc, -3)
            total += base - discount
            self.rows["salesorderdetail"].append(dict(
                salesorderdetailid=self.uid(), salesorderid=so_id, productid=prod["id"], uomid=TON_UOM_ID,
                quantity=qty, priceperunit=price, baseamount=base, manualdiscountamount=discount,
                extendedamount=base - discount, ahn_costperton=round(self.cost(prod, close), -3),
                sequencenumber=seq, transactioncurrencyid=IRR_CURRENCY_ID))
        invoiced = fulfilled <= self.end
        self.rows["salesorder"].append(dict(
            salesorderid=so_id, ordernumber=self._number("ORD"), opportunityid=opp_id, quoteid=quote_id,
            customerid=account.id, customeridtype="account", ownerid=self.rep_ids[s.rep],
            submitdate=close, datefulfilled=fulfilled if invoiced else None, totalamount=round(total),
            paymenttermscode=account.terms, statecode=4 if invoiced else 1,
            statuscode=100003 if invoiced else 3, createdon=close, transactioncurrencyid=IRR_CURRENCY_ID))
        if invoiced:
            days = C.PAYMENT_TERMS[account.terms][1]
            due = fulfilled.normalize() + pd.Timedelta(days=days)
            if days == 0:
                paid = fulfilled - pd.Timedelta(days=int(rng.integers(0, 4)))
            else:
                late = float(rng.exponential(account.industry.late_days)) - 3
                if rng.random() < 0.06:
                    late += float(rng.uniform(60, 200))
                paid = self._biz_time(due + pd.Timedelta(days=max(-5.0, late)))
            is_paid = paid <= self.end
            self.rows["invoice"].append(dict(
                invoiceid=self.uid(), invoicenumber=self._number("INV"), salesorderid=so_id,
                opportunityid=opp_id, customerid=account.id, customeridtype="account",
                ownerid=self.rep_ids[s.rep], createdon=fulfilled, duedate=due.date(),
                totalamount=round(total), paymenttermscode=account.terms,
                ahn_paidon=paid if is_paid else None, statecode=2 if is_paid else 0,
                statuscode=100001 if is_paid else 4, transactioncurrencyid=IRR_CURRENCY_ID))
        return round(total)

    # ------------------------------------------------------------------ repeat business
    def _repeat_business(self) -> None:
        for acc in list(self.accounts):
            if acc.customer_since is None:
                continue
            ind = acc.industry
            for p in self.months:
                if p.end_time <= acc.customer_since:
                    continue
                if self.rng.random() < ind.churn_hazard:
                    break
                trend = self.index[p + 1] / self.index[p] - 1 if p + 1 in self.index else C.PRICE_DRIFT
                demand = float(np.clip(1 + 1.5 * trend, 0.8, 1.4))
                for _ in range(int(self.rng.poisson(ind.repeat_rate * C.SEASONALITY[p.month] * demand))):
                    day = p.start_time + pd.Timedelta(days=int(self.rng.integers(0, p.days_in_month)))
                    created = self._biz_time(max(day, acc.customer_since + pd.Timedelta(days=1)))
                    if created > self.end:
                        continue
                    group = self._choice(ind.product_mix)
                    tons = max(5.0, round(float(self.rng.lognormal(math.log(ind.median_tons * 0.9), 0.55)) / 5) * 5)
                    self._simulate(_OppSpec(acc, created, acc.rep, group, tons, True, 0.0))
            # quarterly account reviews
            q = pd.date_range(max(acc.customer_since, self.start), self.end, freq="QS")
            for d in q:
                if self.rng.random() < 0.7:
                    self._activity(self._biz_time(d + pd.Timedelta(days=int(self.rng.integers(0, 60)))),
                                   acc.rep, acc.id, "account", f"Account review: {acc.name}")

    # ------------------------------------------------------------------ metadata
    def _metadata(self) -> dict[str, pd.DataFrame]:
        local = []

        def add(entity, column, options):
            for code, label in options.items():
                local.append(dict(EntityName=entity, OptionSetName=column, Option=code,
                                  IsUserLocalizedLabel=False, LocalizedLabelLanguageCode=1033,
                                  LocalizedLabel=label))

        industries = {i.code: i.label for i in C.INDUSTRIES}
        terms = {k: v[0] for k, v in C.PAYMENT_TERMS.items()}
        add("account", "industrycode", industries)
        add("account", "customertypecode", {3: "Customer", 8: "Prospect"})
        add("account", "paymenttermscode", terms)
        add("lead", "industrycode", industries)
        add("lead", "leadsourcecode", {1: "Advertisement", 2: "Employee Referral", 3: "External Referral",
                                       4: "Partner", 5: "Public Relations", 6: "Seminar", 7: "Trade Show",
                                       8: "Web", 9: "Word of Mouth", 10: "Other"})
        add("opportunity", "salesstage", {code: name.split("-")[1] for code, name, _ in STAGES})
        add("opportunity", "msdyn_forecastcategory", {v: k for k, v in FORECAST.items()})
        add("opportunity", "ahn_lossreason", LOSS_REASONS)
        add("ahn_pipelinesnapshot", "ahn_forecastcategory", {v: k for k, v in FORECAST.items()})
        add("campaign", "typecode", {1: "Advertisement", 2: "Direct Marketing", 3: "Event",
                                     4: "Co-branding", 5: "Other"})
        add("campaign", "ahn_channel", {c.code: c.label for c in C.CHANNELS})
        add("campaignresponse", "responsecode", {1: "Interested", 2: "Not Interested",
                                                 3: "Do Not Send Marketing Materials", 4: "Error"})
        add("campaignresponse", "channeltypecode", {1: "Phone", 2: "Email", 3: "Web form", 4: "In person",
                                                    5: "SMS reply", 6: "Social media"})
        add("salesorder", "paymenttermscode", terms)
        add("invoice", "paymenttermscode", terms)

        glob = []
        for entity in ("product", "lead", "opportunity"):
            for code, (label, _) in C.PRODUCT_GROUPS.items():
                glob.append(dict(OptionSetName="ahn_productgroup", Option=code, IsUserLocalizedLabel=False,
                                 LocalizedLabelLanguageCode=1033, LocalizedLabel=label,
                                 GlobalOptionSetName="ahn_productgroup", EntityName=entity))

        states = {
            "account": {0: "Active", 1: "Inactive"},
            "lead": {0: "Open", 1: "Qualified", 2: "Disqualified"},
            "opportunity": {0: "Open", 1: "Won", 2: "Lost"},
            "quote": {0: "Draft", 1: "Active", 2: "Won", 3: "Closed"},
            "salesorder": {0: "Active", 1: "Submitted", 2: "Canceled", 3: "Fulfilled", 4: "Invoiced"},
            "invoice": {0: "Active", 1: "Closed", 2: "Paid", 3: "Canceled"},
            "campaign": {0: "Active", 1: "Inactive"},
            "campaignresponse": {0: "Open", 1: "Closed", 2: "Canceled"},
            "activitypointer": {0: "Open", 1: "Completed", 2: "Canceled", 3: "Scheduled"},
            "product": {0: "Active", 1: "Retired"},
        }
        statuses = {
            "account": [(0, 1, "Active"), (1, 2, "Inactive")],
            "lead": [(0, 1, "New"), (0, 2, "Contacted"), (1, 3, "Qualified"), (2, 4, "Lost"),
                     (2, 5, "Cannot Contact"), (2, 6, "No Longer Interested"), (2, 7, "Canceled")],
            "opportunity": [(0, 1, "In Progress"), (0, 2, "On Hold"), (1, 3, "Won"), (2, 4, "Canceled"),
                            (2, 5, "Out-Sold")],
            "quote": [(0, 1, "In Progress"), (1, 2, "In Progress"), (1, 3, "Open"), (2, 4, "Won"),
                      (3, 5, "Lost"), (3, 6, "Canceled"), (3, 7, "Revised")],
            "salesorder": [(0, 1, "New"), (0, 2, "Pending"), (1, 3, "In Progress"), (2, 4, "No Money"),
                           (3, 100001, "Complete"), (3, 100002, "Partial"), (4, 100003, "Invoiced")],
            "invoice": [(0, 1, "New"), (0, 2, "Partially Shipped"), (0, 4, "Billed"),
                        (2, 100001, "Complete"), (2, 100002, "Partial"), (3, 100003, "Canceled")],
            "campaign": [(0, 0, "Proposed"), (0, 1, "Ready To Launch"), (0, 2, "Launched"),
                         (0, 3, "Completed"), (0, 4, "Canceled"), (0, 5, "Suspended"), (1, 6, "Inactive")],
            "campaignresponse": [(0, 1, "Open"), (1, 2, "Closed"), (2, 3, "Canceled")],
            "activitypointer": [(0, 1, "Open"), (1, 2, "Completed"), (2, 3, "Canceled"), (3, 4, "Scheduled")],
            "product": [(0, 1, "Active"), (1, 2, "Retired")],
        }
        state_rows = [dict(EntityName=e, State=s, IsUserLocalizedLabel=False, LocalizedLabelLanguageCode=1033,
                           LocalizedLabel=label) for e, m in states.items() for s, label in m.items()]
        status_rows = [dict(EntityName=e, State=s, Status=st, IsUserLocalizedLabel=False,
                            LocalizedLabelLanguageCode=1033, LocalizedLabel=label)
                       for e, m in statuses.items() for s, st, label in m]
        return {
            "OptionsetMetadata": pd.DataFrame(local),
            "GlobalOptionsetMetadata": pd.DataFrame(glob),
            "StateMetadata": pd.DataFrame(state_rows),
            "StatusMetadata": pd.DataFrame(status_rows),
        }

    # ------------------------------------------------------------------ run
    def generate(self) -> dict[str, pd.DataFrame]:
        self._build_prices()
        self._reference_tables()
        self._existing_accounts()
        campaigns = self._campaigns()
        specs = self._responses_and_leads(campaigns) + self._organic_leads()
        specs.sort(key=lambda s: s.created)
        for spec in specs:
            opp = self._simulate(spec)
            spec.lead_row["qualifyingopportunityid"] = opp["opportunityid"]
        self._repeat_business()
        customers = {a.id for a in self.accounts if a.customer_since is not None}
        for row in self.rows["account"]:
            if row["accountid"] in customers:
                row["customertypecode"] = 3
        tables = {name: pd.DataFrame(rows) for name, rows in self.rows.items()}
        tables.update(self._metadata())
        return tables


def _format(value):
    if isinstance(value, pd.Timestamp):
        return (value - TEHRAN_UTC_OFFSET).strftime("%Y-%m-%dT%H:%M:%SZ")
    return value


def write_export(tables: dict[str, pd.DataFrame], out_dir: Path) -> None:
    """Write every table as <logical name>.csv, timestamps in UTC like Dataverse."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, df in tables.items():
        df = df.copy()
        for col in df.columns:
            if df[col].map(lambda v: isinstance(v, pd.Timestamp)).any():
                df[col] = df[col].map(_format)
        df.to_csv(out_dir / f"{name}.csv", index=False)


def generate_export(out_dir: Path, seed: int = 42, start: str = "2024-07-01",
                    end: str = "2026-06-30") -> dict[str, pd.DataFrame]:
    tables = DataverseExportGenerator(seed, start, end).generate()
    write_export(tables, out_dir)
    return tables
