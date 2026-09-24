# Methodology

## Time windows

The export date is the latest `createdon` in the data (30 June 2026). "Last 12 months" (L12) is the 365 days up to and including it, compared with the 365 days before (P12). Revenue is counted when the order is submitted (`salesorder.submitdate`), in Tehran time.

## Data quality

`quality/checks.py` runs before anything is modelled. Errors stop the pipeline; warnings are reported in `data/processed/data_quality_report.csv`.

- **Keys:** every primary key is present and unique.
- **Lookups:** 29 checks that a GUID points at an existing row: 26 lookups such as `opportunity.customerid → account` and `invoice.salesorderid → salesorder` (optional lookups may be empty), plus the polymorphic `activitypointer.regardingobjectid` checked separately for leads, opportunities and accounts.
- **Labels:** every option-set code has a label in the metadata (warning).
- **Sales-process rules:** a won deal has a close date, a positive value and a sales order; a lost deal has a loss reason (warning); a close date is not before creation; a qualified lead points at its opportunity; a quote's valid-to date is not before its valid-from; `extendedamount = baseamount − manualdiscountamount` on every order line; order totals match their lines (warning); a paid invoice has a payment date.

## Sales funnel

The funnel follows the Dynamics 365 record types. Each step moves on or drops out as follows:

| Step | Dataverse table | Moves on when | Drops out as |
|---|---|---|---|
| Lead (سرنخ فروش) | `lead` | it is qualified (statecode Qualified) | its disqualification status reason: Lost, Cannot Contact, No Longer Interested, Canceled; or still open |
| Opportunity (فرصت فروش) | `opportunity` | a `quote` exists for it | lost before any quote (its `ahn_lossreason`), or open and not quoted yet |
| Quote (پیش‌فاکتور) | `quote` | the deal is won | lost after quoting (its `ahn_lossreason`), or still being negotiated |
| Order (سفارش) | `salesorder` | it is delivered and invoiced | awaiting delivery |
| Invoice (فاکتور) | `invoice` | — | split into paid, open and not yet due, and overdue |

Won / Lost (فروش موفق / از دست‌رفته) is the opportunity's final state. The new-business view covers every lead in the data window and the opportunities they became. The all-opportunities view adds repeat business from existing customers; those deals have no lead, so it starts at the opportunity. Every drop-out is counted exactly once, so each step's count equals the next step's count plus its drop-outs.

## Marketing attribution

First touch, through the lead: a deal belongs to the campaign of the lead it was qualified from (`opportunity.originatingleadid → lead.campaignid`). Leads without a campaign are grouped by their lead source (Web, Word of Mouth, Employee Referral). Two returns are measured against a campaign's actual cost (`totalactualcost`), as gross margin earned per rial spent:

- **First-deal return:** gross margin of the won deals its leads produced.
- **Customer-lifetime return:** gross margin of every order, repeat business included, from the accounts the campaign created (`account.originatingleadid`), over the whole data window.

1x means the campaign earned its cost back in gross margin. It does not account for the sales team's time.

## Win-probability model

**Question:** at the time a deal is being worked, how likely is it to be won?

**Features** (known early in a deal's life):

| Feature | Source |
|---|---|
| Customer industry, product group, sales rep | account, opportunity |
| How the deal came in: the campaign channel or lead source, or repeat business | lead, campaign, `ahn_isrepeatbusiness` |
| Deal size (log tons) | `ahn_tonnage` |
| Hours from opportunity to first quote; never quoted | first `quote.createdon` |
| Discount on the first quote | `quote.discountpercentage` |
| Activities in the first 3 days | `activitypointer` |
| Competitor involved | `opportunitycompetitors` |
| Steel price trend: this month's price index vs last month's | `productpricelevel` |

Close probability, stage and forecast category are deliberately left out: they are the rep's judgment, which the model is compared against.

**Model:** scikit-learn `HistGradientBoostingClassifier` with native categorical features (250 iterations, learning rate 0.05, 15 leaves, at least 30 deals per leaf, L2 1.0).

**Evaluation (out of time):** trained on deals closed before 31 Dec 2025, tested on the 1,106 deals closed after. The baseline is the rep's own probability on the same deals: the last weekly snapshot before the deal closed (`ahn_pipelinesnapshot`), because Dynamics overwrites `closeprobability` with 100 or 0 at close. Reported: AUC (how well each ranks winners above losers), Brier score (accuracy of the probabilities themselves), and calibration by probability band. Driver importance is permutation importance on the test set: how much AUC drops when one input is shuffled.

**Scoring the open pipeline:** the model is refit on every closed deal. For an open deal that has not been quoted yet, the hours-to-quote feature is the time it has waited so far, which amounts to assuming the quote goes out today. The model-weighted pipeline is `Σ estimatedvalue × p_model`; the CRM-weighted one is `Σ estimatedvalue × closeprobability`.

## Pricing and margin

- List price per ton: `salesorderdetail.priceperunit`, the month's price list.
- Realized price per ton: `extendedamount / quantity`, after the manual discount.
- Cost per ton: `ahn_costperton`, the mill purchase cost. Stock is bought about a month ahead, so cost follows the previous month's price; margins widen when prices jump and narrow when they fall.
- Price index: the average of every product's list price relative to its first month, × 100.

## Receivables

- Aging buckets on open invoices at the export date, by days past `duedate`: not yet due, 1-30, 31-60, 61-90, over 90.
- DSO = open receivables / L12 sales × 365. A 90-day window is too noisy for segments that order a few times a year.
- Days late = `ahn_paidon − duedate` on invoices paid in the last 12 months.

## Customers gone quiet

A regular customer (3 or more orders) is on the win-back list when its days since the last order exceed 2.5 times its own median gap between orders, with a floor of 90 days. A distributor that reorders every two weeks is flagged after 90 days, the floor; a fabricator that orders every two months after about five months; a public project that orders twice a year only after well over a year.

## What the synthetic data does and doesn't show

The generator builds in the relationships a steel trader would expect: quote speed, discount, competitor and deal size affect the win chance; reps differ in speed, discounting, skill and optimism; public projects pay late; prices move in steps. The analysis recovers them from the export without being told. A real export will be messier. Expect missing loss reasons, merged accounts and probabilities that are never updated. The data-quality layer exists for that.
