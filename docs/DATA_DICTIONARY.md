# Data dictionary

The export in `data/dynamics_export/` follows Dataverse naming: one CSV per table, named by its logical name, columns by their logical names. Date-time columns are UTC (`2025-03-04T09:12:00Z`); date-only columns are `YYYY-MM-DD`. Money is in IRR (`transactioncurrencyid` points at the IRR row of `transactioncurrency`), quantities are in tons (`uom` = Ton). Keys are GUIDs.

Option sets are stored as integer codes. Their labels are in the four metadata files, in the same layout Azure Synapse Link for Dataverse writes:

| File | Columns | Used for |
|---|---|---|
| `OptionsetMetadata.csv` | EntityName, OptionSetName, Option, IsUserLocalizedLabel, LocalizedLabelLanguageCode, LocalizedLabel | Local option sets (`lead.leadsourcecode`, `opportunity.ahn_lossreason`, ...) |
| `GlobalOptionsetMetadata.csv` | OptionSetName, Option, IsUserLocalizedLabel, LocalizedLabelLanguageCode, LocalizedLabel, GlobalOptionSetName, EntityName | Global option sets (`ahn_productgroup`) |
| `StateMetadata.csv` | EntityName, State, IsUserLocalizedLabel, LocalizedLabelLanguageCode, LocalizedLabel | `statecode` of every table |
| `StatusMetadata.csv` | EntityName, State, Status, IsUserLocalizedLabel, LocalizedLabelLanguageCode, LocalizedLabel | `statuscode` of every table |

After ingest every coded column has a `<column>_label` next to it, and every time is in Tehran local time.

## Tables

### Marketing

| Table | Key columns | Notes |
|---|---|---|
| `campaign` | campaignid, name, codename, typecode, **ahn_channel**, actualstart, actualend, budgetedcost, totalactualcost | `typecode`: 1 Advertisement, 2 Direct Marketing, 3 Event, 4 Co-branding, 5 Other. `ahn_channel` (custom): Trade Fair, SMS Price Bulletin, Email Newsletter, Instagram Ads, LinkedIn Ads, Google Ads, Telesales Outreach, Customer Referral Program, Mill Partner Webinar |
| `campaignresponse` | activityid, regardingobjectid → campaign, responsecode, channeltypecode, receivedon, companyname | `responsecode`: 1 Interested, 2 Not Interested, 3 Do Not Send Marketing Materials, 4 Error |
| `lead` | leadid, companyname, industrycode, address1_stateorprovince, leadsourcecode, campaignid, relatedobjectid → campaignresponse, **ahn_productgroup**, **ahn_estimatedtonnage**, parentaccountid, qualifyingopportunityid, ownerid, createdon, modifiedon, statecode, statuscode | `leadsourcecode` uses the standard Dynamics values (7 Trade Show, 8 Web, 9 Word of Mouth, ...). State 0 Open / 1 Qualified / 2 Disqualified; status 4 Lost, 5 Cannot Contact, 6 No Longer Interested, 7 Canceled. `modifiedon` is the qualification or disqualification time |

### Sales

| Table | Key columns | Notes |
|---|---|---|
| `account` | accountid, name, accountnumber, industrycode, customertypecode, address1_stateorprovince, territoryid, ownerid, paymenttermscode, creditlimit, originatingleadid, createdon | `industrycode` (custom values): Construction Contractor, Structural Steel Fabricator, Pipe & Profile Manufacturer, Automotive & Appliance Parts, Steel Distributor / Trader, Government & Infrastructure Project, Agricultural & Greenhouse. `customertypecode` 3 Customer / 8 Prospect. Payment terms: Cash in advance, Net 30, Net 60, Net 90 |
| `opportunity` | opportunityid, customerid → account, originatingleadid, campaignid, ownerid, createdon, estimatedclosedate, actualclosedate, estimatedvalue, actualvalue, closeprobability, salesstage, stepname, msdyn_forecastcategory, statecode, statuscode, **ahn_productgroup**, **ahn_tonnage**, **ahn_lossreason**, **ahn_isrepeatbusiness** | State 0 Open / 1 Won / 2 Lost; status 4 Canceled, 5 Out-Sold. `closeprobability` is the rep's number (100 / 0 once closed). `ahn_lossreason`: Price, Lost to Competitor, Slow Quote / Price Expired, Credit Terms, Delivery Lead Time, Spec / Quality, Project Postponed, Bought Direct from Mill |
| `opportunitycompetitors` | opportunitycompetitorid, opportunityid, competitorid | N:N between opportunity and competitor |
| `competitor` | competitorid, name | Includes "Mill Direct Sales", a mill selling to the customer directly |
| `quote` | quoteid, quotenumber, revisionnumber, opportunityid, createdon, effectivefrom, effectiveto, discountpercentage, totalamount, statecode, statuscode | Valid for three days (`effectiveto`). A new revision is issued when the price moves; old ones are status 7 Revised |
| `salesorder` | salesorderid, ordernumber, opportunityid, quoteid, customerid, ownerid, submitdate, datefulfilled, totalamount, paymenttermscode, statecode | State 4 Invoiced once delivered |
| `salesorderdetail` | salesorderdetailid, salesorderid, productid, quantity (tons), priceperunit (list price per ton), baseamount, manualdiscountamount, extendedamount, **ahn_costperton** | `extendedamount = baseamount - manualdiscountamount`. `ahn_costperton` is the mill purchase cost per ton |
| `invoice` | invoiceid, invoicenumber, salesorderid, customerid, createdon, duedate, totalamount, paymenttermscode, **ahn_paidon**, statecode | State 2 Paid when `ahn_paidon` is set; open invoices are state 0 |

### Catalog, people and activity

| Table | Key columns | Notes |
|---|---|---|
| `product` | productid, productnumber, name, **ahn_productgroup**, **ahn_grade**, defaultuomid | 23 products in 7 groups: Rebar, Beams & Sections, Hot-Rolled Sheet, Cold-Rolled Sheet, Galvanized Sheet, Pipes & Profiles, Wire Rod |
| `pricelevel` / `productpricelevel` | pricelevelid, name, begindate, enddate / productid, amount | One price list per month; `amount` is the list price per ton |
| `systemuser` | systemuserid, fullname, title, territoryid | 10 account managers and the head of sales |
| `territory` | territoryid, name | Tehran & North, Central, East, North-West, South-West, South |
| `activitypointer` | activityid, activitytypecode (phonecall, email, appointment, task), regardingobjectid, regardingobjecttypecode (lead, opportunity, account), ownerid, createdon, actualend | |
| `ahn_pipelinesnapshot` (custom) | ahn_snapshotdate, ahn_opportunityid, ownerid, ahn_stepname, ahn_closeprobability, ahn_estimatedvalue, ahn_forecastcategory, ahn_estimatedclosedate | One row per open deal when it is created and every Saturday after, as a scheduled flow would keep. Dynamics overwrites `closeprobability` on close, so this is what the rep's forecast looked like before the outcome |

## Star schema (`data/processed/steel_crm.db`, `data/processed/powerbi/`)

| Table | Grain | Main columns |
|---|---|---|
| `dim_date` | day | date, year, quarter, month, month_name, weekday |
| `dim_account` | account | name, industry, province, territory, owner, payment_terms, credit_limit, acquisition_channel |
| `dim_product` | product | productnumber, name, product_group, grade |
| `dim_user` | rep | fullname, title, territory |
| `dim_campaign` | campaign | name, channel, campaign_type, start, end, budgetedcost, cost |
| `fact_lead` | lead | created, decided, status, source, channel, industry, estimated_tons, opportunityid |
| `fact_opportunity` | deal | channel, industry, product_group, tons, is_repeat, created, close_date, state, won, loss_reason, stage, estimated_value, actual_value, crm_probability, rep_prob_last, hours_to_first_quote, first_quote_discount, n_quotes, activities_first_3d, competitor, price_trend, cycle_days, **p_model** |
| `fact_sales_line` | order line | order_date, product, product_group, tons, list_price, gross, discount, net, cost, margin, industry, province |
| `fact_invoice` | invoice | invoice_date, due_date, paid_on, amount, is_open, days_to_pay, days_late |
| `fact_campaign_response` | response | campaignid, received, response, channel |
| `fact_activity` | activity | activitytypecode, regardingobjecttypecode, regardingobjectid, ownerid, created |
| `fact_pipeline_snapshot` | deal × week | snapshot_date, stage, close_probability, estimated_value, forecast_category |
| `price_index` | month | price_index (100 = first month), avg_list_price |
