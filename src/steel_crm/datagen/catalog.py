"""Business reference data for the synthetic Dynamics 365 export.

Everything here describes the fictional Ahanyar Steel Trading Co.: what it
sells, who it sells to, where, through which marketing channels, and the
sales team. The generator turns these profiles into Dataverse rows; the
numbers are chosen so the patterns a real steel trader lives with show up
in the data (commodity price swings, thin margins on rebar, slow-paying
public projects, price-driven distributors, quotes that expire in days).
"""

from __future__ import annotations

from dataclasses import dataclass, field

# --------------------------------------------------------------------------
# Products (price per ton, millions of IRR, at the start of the horizon)
# --------------------------------------------------------------------------
PRODUCT_GROUPS = {
    # option value: (label, trader margin over mill cost)
    100000000: ("Rebar", 0.065),
    100000001: ("Beams & Sections", 0.085),
    100000002: ("Hot-Rolled Sheet", 0.075),
    100000003: ("Cold-Rolled Sheet", 0.095),
    100000004: ("Galvanized Sheet", 0.115),
    100000005: ("Pipes & Profiles", 0.105),
    100000006: ("Wire Rod", 0.06),
}
GROUP_CODE = {label: code for code, (label, _) in PRODUCT_GROUPS.items()}

PRODUCTS = [
    # productnumber, name, group, grade, base price (M IRR / ton)
    ("RB-A3-10", "Rebar A3 Ø10", "Rebar", "A3", 268),
    ("RB-A3-12", "Rebar A3 Ø12", "Rebar", "A3", 262),
    ("RB-A3-16", "Rebar A3 Ø14-16", "Rebar", "A3", 258),
    ("RB-A3-22", "Rebar A3 Ø18-22", "Rebar", "A3", 255),
    ("RB-A3-32", "Rebar A3 Ø25-32", "Rebar", "A3", 256),
    ("RB-A2-12", "Rebar A2 Ø12", "Rebar", "A2", 250),
    ("BM-IPE14", "Beam IPE 140", "Beams & Sections", "ST37", 318),
    ("BM-IPE18", "Beam IPE 180", "Beams & Sections", "ST37", 312),
    ("BM-IPE22", "Beam IPE 220", "Beams & Sections", "ST37", 315),
    ("BM-HEA20", "Beam HEA 200", "Beams & Sections", "ST37", 335),
    ("SC-ANG50", "Angle 50x5", "Beams & Sections", "ST37", 305),
    ("SC-UNP10", "Channel UNP 100", "Beams & Sections", "ST37", 309),
    ("HR-2", "Hot-rolled sheet 2 mm", "Hot-Rolled Sheet", "ST37", 332),
    ("HR-3", "Hot-rolled sheet 3 mm", "Hot-Rolled Sheet", "ST37", 326),
    ("HR-8", "Hot-rolled plate 5-10 mm", "Hot-Rolled Sheet", "ST52", 340),
    ("CR-07", "Cold-rolled sheet 0.7 mm", "Cold-Rolled Sheet", "ST12", 392),
    ("CR-10", "Cold-rolled sheet 1.0 mm", "Cold-Rolled Sheet", "ST12", 385),
    ("GV-05", "Galvanized sheet 0.5 mm", "Galvanized Sheet", "DX51D", 455),
    ("GV-08", "Galvanized sheet 0.8 mm", "Galvanized Sheet", "DX51D", 446),
    ("PP-SQ40", "Square profile 40x40", "Pipes & Profiles", "ST37", 356),
    ("PP-RC48", "Rectangular profile 40x80", "Pipes & Profiles", "ST37", 352),
    ("PP-PIPE2", "Pipe 2 inch", "Pipes & Profiles", "ST37", 364),
    ("WR-55", "Wire rod Ø5.5", "Wire Rod", "SAE1008", 248),
]

# Month-over-month change of the steel price index. Steady inflation-driven
# drift plus the shocks a trader remembers: a currency jump in March 2025,
# a summer correction, and another step up in January 2026.
PRICE_SHOCKS = {"2025-03": 0.15, "2025-07": -0.06, "2025-08": -0.02, "2026-01": 0.08}
PRICE_DRIFT = 0.018
PRICE_NOISE = 0.012

# --------------------------------------------------------------------------
# Customer industries
# --------------------------------------------------------------------------
PAYMENT_TERMS = {
    # option value: (label, days)
    100000000: ("Cash in advance", 0),
    1: ("Net 30", 30),
    4: ("Net 60", 60),
    100000001: ("Net 90", 90),
}


@dataclass(frozen=True)
class Industry:
    code: int
    label: str
    weight: float  # share of accounts
    product_mix: dict[str, float]
    median_tons: float
    base_win: float  # win probability of an average new-business deal
    price_sensitivity: float  # 0..1, drives discounts and "Price" losses
    terms: dict[int, float]  # payment terms code -> probability
    late_days: float  # mean days paid after the due date
    repeat_rate: float  # repeat opportunities per month once a customer
    churn_hazard: float  # monthly probability a customer stops buying
    credit_limit_b: float  # typical credit limit, billions of IRR
    competitor_prob: float


INDUSTRIES = [
    Industry(100000000, "Construction Contractor", 0.30,
             {"Rebar": 0.6, "Beams & Sections": 0.25, "Hot-Rolled Sheet": 0.1, "Pipes & Profiles": 0.05},
             60, 0.44, 0.6, {100000000: 0.3, 1: 0.4, 4: 0.3}, 14, 0.24, 0.012, 20, 0.40),
    Industry(100000001, "Structural Steel Fabricator", 0.16,
             {"Beams & Sections": 0.45, "Hot-Rolled Sheet": 0.35, "Pipes & Profiles": 0.1, "Rebar": 0.1},
             45, 0.47, 0.5, {100000000: 0.3, 1: 0.5, 4: 0.2}, 8, 0.28, 0.010, 15, 0.40),
    Industry(100000002, "Pipe & Profile Manufacturer", 0.10,
             {"Hot-Rolled Sheet": 0.7, "Cold-Rolled Sheet": 0.1, "Galvanized Sheet": 0.2},
             120, 0.42, 0.7, {1: 0.5, 4: 0.5}, 10, 0.42, 0.010, 40, 0.45),
    Industry(100000003, "Automotive & Appliance Parts", 0.09,
             {"Cold-Rolled Sheet": 0.55, "Galvanized Sheet": 0.35, "Hot-Rolled Sheet": 0.1},
             50, 0.50, 0.4, {1: 0.3, 4: 0.5, 100000001: 0.2}, 6, 0.34, 0.008, 25, 0.35),
    Industry(100000004, "Steel Distributor / Trader", 0.20,
             {"Rebar": 0.35, "Beams & Sections": 0.2, "Hot-Rolled Sheet": 0.15, "Wire Rod": 0.15,
              "Pipes & Profiles": 0.15},
             150, 0.34, 0.9, {100000000: 0.7, 1: 0.3}, 3, 0.62, 0.020, 60, 0.70),
    Industry(100000005, "Government & Infrastructure Project", 0.07,
             {"Rebar": 0.55, "Beams & Sections": 0.35, "Pipes & Profiles": 0.1},
             250, 0.30, 0.5, {4: 0.3, 100000001: 0.7}, 48, 0.09, 0.030, 120, 0.45),
    Industry(100000006, "Agricultural & Greenhouse", 0.08,
             {"Galvanized Sheet": 0.45, "Pipes & Profiles": 0.45, "Wire Rod": 0.1},
             30, 0.50, 0.5, {100000000: 0.5, 1: 0.5}, 10, 0.16, 0.012, 8, 0.30),
]
INDUSTRY_BY_LABEL = {i.label: i for i in INDUSTRIES}

# Name parts for fictional companies, per industry
NAME_PREFIXES = [
    "Arya", "Sepehr", "Tous", "Zagros", "Alvand", "Persis", "Mehr", "Sahand", "Sabalan",
    "Karun", "Arvand", "Kowsar", "Omid", "Rayan", "Taban", "Atlas", "Behsaz", "Pishgam",
    "Navid", "Farazan", "Hirad", "Kian", "Mahan", "Nikan", "Parsian", "Raman", "Saba",
    "Tarava", "Vista", "Yekta", "Diba", "Golestan", "Helia", "Iranmehr", "Jahan", "Kavir Sabz",
    "Laleh", "Mahtab", "Negin", "Ofogh", "Pouya", "Rastin", "Shahab", "Tina", "Ava",
    "Aban", "Bamdad", "Chakad", "Dena", "Elham", "Fardis", "Gohar", "Hamoon", "Iman", "Javid",
    "Kimia", "Mehrgan", "Nasim", "Orang", "Pardis", "Rahyab", "Sadaf", "Tirdad", "Varna", "Zarrin",
    "Arman", "Borna", "Darya", "Espadana", "Farnam", "Gilan", "Hoda", "Ilia", "Kaspian", "Nima",
]
NAME_CORES = {
    "Construction Contractor": ["Omran", "Sazeh", "Construction", "Builders", "Abadgaran", "Contracting",
                                "Sakhteman"],
    "Structural Steel Fabricator": ["Steel Structures", "Fabrication Works", "Eskelet Felezi"],
    "Pipe & Profile Manufacturer": ["Pipe & Profile Industries", "Profile Works", "Loleh Sazi", "Tube Mills",
                                    "Pipe Works", "Profile Sazan"],
    "Automotive & Appliance Parts": ["Auto Parts Mfg.", "Appliance Components", "Press Works"],
    "Steel Distributor / Trader": ["Steel Trading", "Metals Supply", "Ahan Bazar", "Steel Depot", "Iron & Steel Supply",
                                   "Foolad Trading", "Metal Center", "Steel Merchants", "Ahan Forooshi"],
    "Government & Infrastructure Project": ["Rail & Road Projects", "Dam & Water Works",
                                            "Urban Infrastructure", "Housing Development"],
    "Agricultural & Greenhouse": ["Greenhouse Structures", "Agri-Steel", "Golkhaneh Sazan"],
}
NAME_SUFFIXES = ["Co.", "Ltd.", "Group", "Industries", "Co."]

# --------------------------------------------------------------------------
# Geography and sales team
# --------------------------------------------------------------------------
PROVINCES = {
    # province: (territory, share of accounts)
    "Tehran": ("Tehran & North", 0.30),
    "Alborz": ("Tehran & North", 0.07),
    "Mazandaran": ("Tehran & North", 0.05),
    "Isfahan": ("Central", 0.15),
    "Khorasan Razavi": ("East", 0.10),
    "East Azerbaijan": ("North-West", 0.09),
    "Khuzestan": ("South-West", 0.10),
    "Fars": ("South", 0.08),
    "Hormozgan": ("South", 0.06),
}
TERRITORIES = ["Tehran & North", "Central", "East", "North-West", "South-West", "South"]


@dataclass(frozen=True)
class Rep:
    name: str
    title: str
    territory: str
    skill: float  # logit uplift on win probability
    discount: float  # typical discount given
    optimism: float  # points added to the CRM close probability
    quote_hours: float  # typical hours from opportunity to first quote
    activity: float  # activities per week on an open deal
    key_accounts: bool = False


REPS = [
    Rep("Reza Karimi", "Senior Account Manager", "Tehran & North", 0.30, 0.018, 10, 5, 3.2),
    Rep("Maryam Hosseini", "Account Manager", "Tehran & North", 0.20, 0.020, 6, 3, 3.6),
    Rep("Ali Rezaei", "Account Manager", "Tehran & North", -0.10, 0.046, 15, 22, 2.4),
    Rep("Zahra Kazemi", "Key Account Manager", "Tehran & North", 0.15, 0.014, 8, 7, 3.0, True),
    Rep("Sara Moradi", "Senior Account Manager", "Central", 0.25, 0.019, 8, 6, 3.3),
    Rep("Hamed Jafari", "Account Manager", "Central", -0.20, 0.030, 25, 44, 1.8),
    Rep("Niloofar Ahmadi", "Account Manager", "South-West", 0.05, 0.022, 11, 14, 2.8),
    Rep("Mehdi Sadeghi", "Account Manager", "South", 0.00, 0.026, 17, 24, 2.5),
    Rep("Parisa Nazari", "Account Manager", "East", 0.10, 0.020, 9, 10, 3.0),
    Rep("Amir Ghasemi", "Account Manager", "North-West", -0.05, 0.028, 19, 32, 2.2),
]
SALES_MANAGER = ("Kaveh Mohammadi", "Head of Sales")

# --------------------------------------------------------------------------
# Marketing channels (custom option set ahn_channel on campaign)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Channel:
    code: int
    label: str
    campaign_type: int  # campaign.typecode
    lead_source: int  # lead.leadsourcecode used for its leads
    response_channel: int  # campaignresponse.channeltypecode
    responses: float  # mean responses per campaign
    lead_rate: float  # leads per response
    qualify: float  # probability a lead is qualified
    win_logit: float  # effect on the win probability of its deals
    tons_mult: float
    cost_b: float  # typical campaign cost, billions of IRR
    industry_skew: dict[str, float] = field(default_factory=dict)


CHANNELS = [
    Channel(100000000, "Trade Fair", 3, 7, 4, 330, 0.34, 0.55, 0.20, 1.3, 26.0,
            {"Government & Infrastructure Project": 1.6, "Pipe & Profile Manufacturer": 1.5,
             "Structural Steel Fabricator": 1.3}),
    Channel(100000001, "SMS Price Bulletin", 2, 10, 5, 440, 0.07, 0.24, -0.30, 0.8, 1.3,
            {"Steel Distributor / Trader": 3.0, "Construction Contractor": 1.5,
             "Agricultural & Greenhouse": 1.5, "Government & Infrastructure Project": 0.2}),
    Channel(100000002, "Email Newsletter", 2, 8, 2, 290, 0.08, 0.34, 0.0, 1.0, 0.45, {}),
    Channel(100000003, "Instagram Ads", 1, 1, 6, 550, 0.08, 0.17, -0.40, 0.5, 5.8,
            {"Agricultural & Greenhouse": 2.5, "Construction Contractor": 1.5,
             "Steel Distributor / Trader": 1.2, "Government & Infrastructure Project": 0.1}),
    Channel(100000004, "LinkedIn Ads", 1, 1, 6, 140, 0.25, 0.46, 0.10, 1.2, 7.0,
            {"Structural Steel Fabricator": 2.0, "Automotive & Appliance Parts": 2.5,
             "Pipe & Profile Manufacturer": 2.0, "Steel Distributor / Trader": 0.5}),
    Channel(100000005, "Google Ads", 1, 8, 3, 260, 0.15, 0.30, -0.10, 0.8, 4.2,
            {"Construction Contractor": 1.5, "Agricultural & Greenhouse": 1.3}),
    Channel(100000006, "Telesales Outreach", 2, 10, 1, 340, 0.12, 0.40, 0.0, 1.1, 8.5,
            {"Construction Contractor": 1.3, "Structural Steel Fabricator": 1.3}),
    Channel(100000007, "Customer Referral Program", 5, 3, 1, 80, 0.60, 0.70, 0.60, 1.1, 3.2, {}),
    Channel(100000008, "Mill Partner Webinar", 4, 6, 3, 130, 0.20, 0.45, 0.15, 1.0, 2.4,
            {"Automotive & Appliance Parts": 2.0, "Pipe & Profile Manufacturer": 1.8,
             "Structural Steel Fabricator": 1.5}),
]
CHANNEL_BY_LABEL = {c.label: c for c in CHANNELS}

# campaign name, channel, start (YYYY-MM-DD), days
CAMPAIGNS = [
    ("Steel & Construction Expo 2024", "Trade Fair", "2024-10-20", 4),
    ("Isfahan Metals Fair 2025", "Trade Fair", "2025-05-11", 4),
    ("Steel & Construction Expo 2025", "Trade Fair", "2025-10-19", 4),
    ("Isfahan Metals Fair 2026", "Trade Fair", "2026-05-10", 4),
    ("Referral Program 2024-25", "Customer Referral Program", "2024-09-01", 300),
    ("Referral Program 2025-26", "Customer Referral Program", "2025-08-01", 330),
    ("Mill Partner Webinar: Cold-Rolled Specs", "Mill Partner Webinar", "2025-01-15", 3),
    ("Mill Partner Webinar: Galvanized Coatings", "Mill Partner Webinar", "2025-11-12", 3),
]
for _q, _start in enumerate(["2024-07-06", "2024-10-05", "2025-01-04", "2025-04-05",
                             "2025-07-05", "2025-10-04", "2026-01-03", "2026-04-04"]):
    CAMPAIGNS.append((f"SMS Price Bulletin Q{_q % 4 + 1} {_start[:4]}", "SMS Price Bulletin", _start, 88))
    CAMPAIGNS.append((f"Weekly Price Newsletter Q{_q % 4 + 1} {_start[:4]}", "Email Newsletter", _start, 88))
for _i, _start in enumerate(["2024-08-10", "2024-11-16", "2025-04-19", "2025-08-09",
                             "2025-11-15", "2026-04-18"]):
    CAMPAIGNS.append((f"Instagram Ads Burst {_i + 1}", "Instagram Ads", _start, 21))
    CAMPAIGNS.append((f"Google Ads: Steel Prices {_i + 1}", "Google Ads", _start, 30))
for _i, _start in enumerate(["2024-09-14", "2025-02-08", "2025-06-14", "2026-02-07"]):
    CAMPAIGNS.append((f"LinkedIn B2B Campaign {_i + 1}", "LinkedIn Ads", _start, 30))
    CAMPAIGNS.append((f"Telesales Blitz {_i + 1}", "Telesales Outreach", _start, 21))

# Leads that don't come from a campaign
ORGANIC_SOURCES = {
    # leadsourcecode: (label, share, qualify, win logit)
    8: ("Web", 0.45, 0.30, -0.10),
    9: ("Word of Mouth", 0.35, 0.50, 0.30),
    2: ("Employee Referral", 0.20, 0.55, 0.25),
}

COMPETITORS = [
    ("Sina Metals Supply", 0.30),
    ("Pouya Steel Trading", 0.25),
    ("Setareh Foolad Co.", 0.20),
    ("Tak Profile Co.", 0.10),
    ("Mill Direct Sales", 0.15),
]

# Construction slows in winter and around Nowruz (late March / early April)
SEASONALITY = {1: 0.75, 2: 0.80, 3: 0.70, 4: 0.85, 5: 1.15, 6: 1.20,
               7: 1.20, 8: 1.15, 9: 1.10, 10: 1.05, 11: 0.95, 12: 0.85}

# --------------------------------------------------------------------------
# Warehouses and delivery (custom table ahn_shipment)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Warehouse:
    code: int  # ahn_shipment.ahn_warehouse option value
    label: str
    weighbridge_sd: float  # spread of loaded weight around the ordered tons
    humid: bool = False  # coastal yard: sheet rusts if it waits


WAREHOUSES = [
    Warehouse(100000000, "Tehran - Shadabad", 0.0025),
    Warehouse(100000001, "Isfahan", 0.0025),
    Warehouse(100000002, "Ahvaz", 0.0055),  # older weighbridge
    Warehouse(100000003, "Mashhad", 0.0030),
    Warehouse(100000004, "Tabriz", 0.0030),
    Warehouse(100000005, "Bandar Abbas", 0.0035, humid=True),
]
WAREHOUSE_BY_LABEL = {w.label: w for w in WAREHOUSES}

# province: (serving warehouse, typical road hours from it)
DELIVERY_ROUTES = {
    "Tehran": ("Tehran - Shadabad", 5),
    "Alborz": ("Tehran - Shadabad", 6),
    "Mazandaran": ("Tehran - Shadabad", 10),
    "Isfahan": ("Isfahan", 5),
    "Khorasan Razavi": ("Mashhad", 6),
    "East Azerbaijan": ("Tabriz", 6),
    "Khuzestan": ("Ahvaz", 6),
    "Fars": ("Isfahan", 14),
    "Hormozgan": ("Bandar Abbas", 5),
}
SOURCING = {100000000: "From stock", 100000001: "Mill direct (IME purchase)"}
CARRIERS = {100000000: "Own fleet", 100000001: "Barbari Sepehr", 100000002: "Tarabar Zagros",
            100000003: "Customer pickup"}
TRUCK_TONS = 24  # a loaded trailer
WEIGHT_TOLERANCE = 0.005  # +/- 0.5% of ordered tons is accepted without a claim
# promised delivery, working days after the order, by sourcing
PROMISE_DAYS = {100000000: 5, 100000001: 6}

# --------------------------------------------------------------------------
# Customer service (Dynamics 365 Customer Service: incident)
# --------------------------------------------------------------------------
SERVICE_AGENTS = [("Leila Rahimi", "Customer Service Lead", 1.0), ("Babak Tavakoli", "Customer Service Agent", 1.3),
                  ("Shirin Kamali", "Customer Service Agent", 0.9), ("Omid Farahani", "Customer Service Agent", 1.6)]

# priority code: (label, first response hours, resolve hours) - the SLA
PRIORITY_SLA = {1: ("High", 2, 48), 2: ("Normal", 4, 120), 3: ("Low", 8, 240)}


@dataclass(frozen=True)
class CaseCategory:
    code: int  # incident.ahn_casecategory option value
    label: str
    casetype: int  # incident.casetypecode: 1 Question, 2 Problem, 3 Request
    priority: int
    queue: str  # who investigates
    resolve_hours: float  # median hours to resolve
    upheld: float  # probability the claim is accepted


CASE_CATEGORIES = [
    CaseCategory(100000000, "Weight discrepancy", 2, 1, "Warehouse", 40, 0.70),
    CaseCategory(100000001, "Late delivery", 2, 2, "Logistics", 14, 0.35),
    CaseCategory(100000002, "Quality / spec claim", 2, 2, "Quality", 80, 0.45),
    CaseCategory(100000003, "Damaged or rusted material", 2, 1, "Quality", 32, 0.60),
    CaseCategory(100000004, "Invoice dispute", 2, 2, "Finance", 80, 0.50),
    CaseCategory(100000005, "Mill certificate request", 3, 3, "Quality", 14, 1.0),
    CaseCategory(100000006, "Delivery change request", 3, 2, "Logistics", 6, 1.0),
]
CASE_BY_LABEL = {c.label: c for c in CASE_CATEGORIES}
CASE_ORIGINS = {1: ("Phone", 0.55), 2: ("Email", 0.25), 3: ("Web", 0.20)}
# incident.ahn_investigatingteam option values
TEAMS = {"Customer Service": 100000000, "Warehouse": 100000001, "Logistics": 100000002, "Quality": 100000003,
         "Finance": 100000004}
