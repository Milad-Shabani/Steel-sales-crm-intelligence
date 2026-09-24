"""Ahanyar Steel's lead-to-cash processes on Dynamics 365, in BPMN 2.0.

Four processes cover a steel order end to end, and each step names the
Dataverse table it reads or writes, so the diagrams and the export describe
the same thing:

* Lead to order (Dynamics 365 Sales): capture and qualify the lead, open the
  opportunity, price the quote from this month's list, approve deep
  discounts, and wait at most three days for an answer before re-pricing.
* Order to delivery (warehouse and logistics, custom table ahn_shipment):
  credit check and promised date, stock or a mill-direct purchase on the
  Iran Mercantile Exchange, a truck, the weighbridge, the waybill and the
  signed proof of delivery.
* Invoice to cash (finance): the invoice, the due date, a reminder call from
  the rep, and credit hold at 30 days overdue.
* Case to resolution (Dynamics 365 Customer Service, incident): the case,
  its SLA, the investigation by the team that owns the problem, the credit
  note if the claim is upheld, and the satisfaction survey.
"""

from __future__ import annotations

from .bpmn import Flow, Lane, Node, Note, Pool, Process

COMPANY = "Ahanyar Steel Trading Co."


def lead_to_order() -> Process:
    lanes = [Lane("Lane_l2o_marketing", "Marketing", 140),
             Lane("Lane_l2o_rep", "Sales rep", 280),
             Lane("Lane_l2o_manager", "Head of Sales", 150)]
    mkt, rep, mgr = (lane.id for lane in lanes)
    nodes = [
        Node("l2o_start", "startEvent", "Enquiry or campaign response", mkt, 0, trigger="message",
             doc="campaignresponse (responsecode Interested) or a web form, a call or a referral",
             table="campaignresponse"),
        Node("l2o_capture", "userTask", "Capture the lead", mkt, 1,
             doc="lead: leadsourcecode, campaignid, ahn_productgroup, ahn_estimatedtonnage", table="lead"),
        Node("l2o_qualify", "userTask", "Qualify the lead", rep, 2,
             doc="lead.statecode Qualified or Disqualified, with the status reason", table="lead"),
        Node("l2o_gw_qualified", "exclusiveGateway", "Qualified?", rep, 3),
        Node("l2o_nurture", "endEvent", "Disqualified, back to nurture", mkt, 3, label="right",
             doc="lead.statuscode: Lost, Cannot Contact, No Longer Interested, Canceled", table="lead"),
        Node("l2o_open", "userTask", "Open the opportunity and account", rep, 4,
             doc="opportunity, account (originatingleadid); business process flow stage Qualify -> Develop",
             table="opportunity"),
        Node("l2o_merge_price", "exclusiveGateway", "", rep, 5),
        Node("l2o_price", "userTask", "Price the quote from this month's list", rep, 6,
             doc="quote with a revisionnumber per re-price; pricelevel of the month; productpricelevel.amount",
             table="quote"),
        Node("l2o_gw_discount", "exclusiveGateway", "Discount over 3%?", rep, 7, label="above"),
        Node("l2o_approve", "userTask", "Approve the discount", mgr, 8,
             doc="quote.discountpercentage above 3% needs the Head of Sales", table="quote"),
        Node("l2o_merge_discount", "exclusiveGateway", "", rep, 9),
        Node("l2o_send", "sendTask", "Send the quote, valid for 3 days", rep, 10,
             doc="quote.effectivefrom / effectiveto (3 days); opportunity stage Propose", table="quote"),
        Node("l2o_wait", "eventBasedGateway", "", rep, 11),
        Node("l2o_expired", "intermediateCatchEvent", "No answer in 3 days: re-price", rep, 12, dy=-80,
             trigger="timer", timer="P3D", label="right",
             doc="the quote expires; a new revision is priced from the current list", table="quote"),
        Node("l2o_accepts", "intermediateCatchEvent", "Customer accepts", rep, 12, trigger="message",
             doc="opportunity closed as Won; quote Won", table="opportunity"),
        Node("l2o_declines", "intermediateCatchEvent", "Customer declines", rep, 12, dy=95, trigger="message",
             label="above",
             doc="opportunity closed as Lost", table="opportunity"),
        Node("l2o_order", "userTask", "Create the sales order", rep, 13,
             doc="salesorder and salesorderdetail from the won quote; stage Close", table="salesorder"),
        Node("l2o_booked", "endEvent", "Order booked", rep, 14, doc="continues in Order to delivery",
             table="salesorder"),
        Node("l2o_loss", "userTask", "Record the loss reason", rep, 13, dy=95,
             doc="opportunity.ahn_lossreason and the competitor (opportunitycompetitors)", table="opportunity"),
        Node("l2o_lost", "endEvent", "Opportunity lost", rep, 14, dy=95, table="opportunity"),
    ]
    flows = [
        Flow("Pool_l2o_customer", "l2o_start", "Enquiry", kind="message"),
        Flow("l2o_start", "l2o_capture"),
        Flow("l2o_capture", "l2o_qualify"),
        Flow("l2o_qualify", "l2o_gw_qualified"),
        Flow("l2o_gw_qualified", "l2o_nurture", "No", route="v"),
        Flow("l2o_gw_qualified", "l2o_open", "Yes"),
        Flow("l2o_open", "l2o_merge_price"),
        Flow("l2o_merge_price", "l2o_price"),
        Flow("l2o_price", "l2o_gw_discount"),
        Flow("l2o_gw_discount", "l2o_approve", "Yes"),
        Flow("l2o_gw_discount", "l2o_merge_discount", "No"),
        Flow("l2o_approve", "l2o_merge_discount"),
        Flow("l2o_merge_discount", "l2o_send"),
        Flow("l2o_send", "Pool_l2o_customer", "Quote", kind="message"),
        Flow("l2o_send", "l2o_wait"),
        Flow("l2o_wait", "l2o_expired"),
        Flow("l2o_wait", "l2o_accepts"),
        Flow("l2o_wait", "l2o_declines"),
        Flow("l2o_expired", "l2o_merge_price", route="up:26"),
        Flow("l2o_accepts", "l2o_order"),
        Flow("l2o_order", "l2o_booked"),
        Flow("l2o_declines", "l2o_loss"),
        Flow("l2o_loss", "l2o_lost"),
    ]
    notes = [
        Note("l2o_note_bpf", "Business process flow on the opportunity: Qualify, Develop, Propose, Close "
             "(opportunity.stepname)", mgr, 4, 0, "l2o_open", 200),
        Note("l2o_note_price", "Steel prices move weekly: the list changes every month and a quote holds "
             "for three days", mgr, 6, 0, "l2o_price", 190),
    ]
    return Process("lead_to_order", "Lead to order",
                   "Dynamics 365 Sales: from an enquiry to a booked sales order at Ahanyar Steel.",
                   [Pool("Pool_l2o_customer", "Customer"), Pool("Pool_l2o_ahanyar", COMPANY, lanes)],
                   nodes, flows, notes)


def order_to_delivery() -> Process:
    lanes = [Lane("Lane_o2d_desk", "Order desk", 150),
             Lane("Lane_o2d_warehouse", "Warehouse and weighbridge", 150),
             Lane("Lane_o2d_logistics", "Logistics", 230),
             Lane("Lane_o2d_finance", "Finance", 130)]
    desk, wh, log, fin = (lane.id for lane in lanes)
    nodes = [
        Node("o2d_start", "startEvent", "Sales order submitted", desk, 0, doc="salesorder.submitdate",
             table="salesorder"),
        Node("o2d_confirm", "businessRuleTask", "Check credit, promise a delivery date", desk, 1,
             doc="account.creditlimit against open invoices; salesorder.requestdeliveryby", table="salesorder"),
        Node("o2d_gw_stock", "exclusiveGateway", "In stock at the serving warehouse?", desk, 2, label="above"),
        Node("o2d_reserve", "userTask", "Reserve the stock", wh, 3,
             doc="ahn_shipment.ahn_sourcing From stock; ahn_warehouse serves the customer's province; "
                 "ahn_stockreadyon", table="ahn_shipment"),
        Node("o2d_buy", "sendTask", "Buy the load on the Mercantile Exchange", desk, 3,
             doc="ahn_shipment.ahn_sourcing Mill direct (IME purchase)", table="ahn_shipment"),
        Node("o2d_released", "intermediateCatchEvent", "Mill releases the load", desk, 4, trigger="message",
             doc="ahn_shipment.ahn_stockreadyon: the load is ready at the mill", table="ahn_shipment"),
        Node("o2d_merge_source", "exclusiveGateway", "", wh, 5),
        Node("o2d_truck", "userTask", "Book a truck: own fleet or carrier", log, 6, dy=-45,
             doc="ahn_shipment.ahn_carrier; the load waits in the yard until the truck arrives",
             table="ahn_shipment"),
        Node("o2d_at_risk", "boundaryEvent", "Promised date at risk", log, 6, attached_to="o2d_truck",
             trigger="timer", timer="P4D", label="right",
             doc="salesorder.requestdeliveryby is close and the load has not left", table="salesorder"),
        Node("o2d_call", "sendTask", "Give the customer a new date", log, 7.3, dy=60,
             doc="phonecall activity on the account", table="activitypointer"),
        Node("o2d_informed", "endEvent", "Customer informed", log, 8.3, dy=60),
        Node("o2d_load", "userTask", "Load and weigh on the weighbridge", wh, 7,
             doc="ahn_shipment.ahn_loadedon, ahn_loadedtons against ahn_orderedtons", table="ahn_shipment"),
        Node("o2d_gw_weight", "exclusiveGateway", "Within 0.5% of the ordered tons?", wh, 8),
        Node("o2d_adjust", "userTask", "Adjust the order to the weighed tons", desk, 9,
             doc="salesorderdetail.quantity follows the weighbridge ticket", table="salesorderdetail"),
        Node("o2d_merge_weight", "exclusiveGateway", "", wh, 10),
        Node("o2d_dispatch", "userTask", "Issue the waybill and dispatch", log, 11, dy=-45,
             doc="ahn_shipment.ahn_waybillnumber, ahn_dispatchedon", table="ahn_shipment"),
        Node("o2d_deliver", "userTask", "Deliver and collect the signed POD", log, 12, dy=-45,
             doc="ahn_shipment.ahn_deliveredon, ahn_podreceived; salesorder.datefulfilled", table="ahn_shipment"),
        Node("o2d_invoice", "serviceTask", "Post the invoice", fin, 13,
             doc="invoice created on delivery with the account's payment terms", table="invoice"),
        Node("o2d_end", "endEvent", "Delivered and invoiced", fin, 14, doc="continues in Invoice to cash",
             table="invoice"),
    ]
    flows = [
        Flow("o2d_start", "o2d_confirm"),
        Flow("o2d_confirm", "o2d_gw_stock"),
        Flow("o2d_gw_stock", "o2d_reserve", "Yes"),
        Flow("o2d_gw_stock", "o2d_buy", "No"),
        Flow("o2d_buy", "Pool_o2d_mill", "Purchase", kind="message"),
        Flow("Pool_o2d_mill", "o2d_released", "Loading slip", kind="message"),
        Flow("o2d_buy", "o2d_released"),
        Flow("o2d_reserve", "o2d_merge_source"),
        Flow("o2d_released", "o2d_merge_source"),
        Flow("o2d_merge_source", "o2d_truck"),
        Flow("o2d_truck", "o2d_load"),
        Flow("o2d_at_risk", "o2d_call"),
        Flow("o2d_call", "o2d_informed"),
        Flow("o2d_call", "Pool_o2d_customer", "New date", kind="message"),
        Flow("o2d_load", "o2d_gw_weight"),
        Flow("o2d_gw_weight", "o2d_adjust", "No"),
        Flow("o2d_gw_weight", "o2d_merge_weight", "Yes"),
        Flow("o2d_adjust", "o2d_merge_weight"),
        Flow("o2d_merge_weight", "o2d_dispatch"),
        Flow("o2d_dispatch", "o2d_deliver"),
        Flow("o2d_deliver", "Pool_o2d_customer", "Delivery", kind="message", dx=-18),
        Flow("Pool_o2d_customer", "o2d_deliver", "Signed POD", kind="message", dx=18),
        Flow("o2d_deliver", "o2d_invoice", route="hv"),
        Flow("o2d_invoice", "o2d_end"),
    ]
    notes = [
        Note("o2d_note_promise", "Promise: 5 working days from stock, 6 from the mill, one more for far "
             "provinces", wh, 1, 0, "o2d_confirm", 190),
    ]
    return Process("order_to_delivery", "Order to delivery",
                   "Warehouse and logistics: from a submitted sales order to a signed delivery and an invoice.",
                   [Pool("Pool_o2d_mill", "Mill / Iran Mercantile Exchange"), Pool("Pool_o2d_ahanyar", COMPANY, lanes),
                    Pool("Pool_o2d_customer", "Customer")],
                   nodes, flows, notes)


def invoice_to_cash() -> Process:
    lanes = [Lane("Lane_i2c_finance", "Finance: receivables", 240),
             Lane("Lane_i2c_rep", "Sales rep", 220),
             Lane("Lane_i2c_credit", "Credit control", 150)]
    ar, rep, credit = (lane.id for lane in lanes)
    nodes = [
        Node("i2c_start", "startEvent", "Order delivered", ar, 0, table="invoice"),
        Node("i2c_send", "sendTask", "Send the invoice with its payment terms", ar, 1,
             doc="invoice.duedate from paymenttermscode: cash in advance, net 30, 60 or 90", table="invoice"),
        Node("i2c_wait", "eventBasedGateway", "", ar, 2),
        Node("i2c_paid", "intermediateCatchEvent", "Payment received", ar, 3, dy=-80, trigger="message",
             doc="invoice.ahn_paidon on or before the due date", table="invoice"),
        Node("i2c_due", "intermediateCatchEvent", "Due date passes unpaid", ar, 3, dy=80, trigger="timer",
             timer="P0D", label="right", doc="invoice.duedate", table="invoice"),
        Node("i2c_call", "userTask", "Call the customer about payment", rep, 4,
             doc="phonecall activity by the account owner", table="activitypointer"),
        Node("i2c_wait2", "eventBasedGateway", "", rep, 5),
        Node("i2c_paid2", "intermediateCatchEvent", "Payment received", rep, 6, dy=-60, trigger="message",
             doc="invoice.ahn_paidon up to 30 days late", table="invoice"),
        Node("i2c_overdue", "intermediateCatchEvent", "30 days overdue", rep, 6, dy=60, trigger="timer",
             timer="P30D", label="right", table="invoice"),
        Node("i2c_hold", "userTask", "Put the account on credit hold", credit, 7,
             doc="no new orders on credit until the account is clear", table="account"),
        Node("i2c_escalate", "userTask", "Escalate: guarantee cheque or legal", credit, 8, table="account"),
        Node("i2c_paid3", "intermediateCatchEvent", "Payment received", credit, 9, trigger="message",
             doc="invoice.ahn_paidon more than 30 days late", table="invoice"),
        Node("i2c_merge", "exclusiveGateway", "", ar, 10),
        Node("i2c_match", "serviceTask", "Match the payment to the invoice", ar, 11,
             doc="invoice statecode Paid, ahn_paidon", table="invoice"),
        Node("i2c_end", "endEvent", "Invoice paid", ar, 12, table="invoice"),
    ]
    flows = [
        Flow("i2c_start", "i2c_send"),
        Flow("i2c_send", "Pool_i2c_customer", "Invoice", kind="message"),
        Flow("i2c_send", "i2c_wait"),
        Flow("i2c_wait", "i2c_paid"),
        Flow("i2c_wait", "i2c_due"),
        Flow("Pool_i2c_customer", "i2c_paid", "Payment", kind="message"),
        Flow("i2c_due", "i2c_call", route="vh"),
        Flow("i2c_call", "i2c_wait2"),
        Flow("i2c_wait2", "i2c_paid2"),
        Flow("i2c_wait2", "i2c_overdue"),
        Flow("i2c_overdue", "i2c_hold", route="vh"),
        Flow("i2c_hold", "i2c_escalate"),
        Flow("i2c_escalate", "i2c_paid3"),
        Flow("i2c_paid", "i2c_merge", route="hv"),
        Flow("i2c_paid2", "i2c_merge", route="vh"),
        Flow("i2c_paid3", "i2c_merge", route="hv"),
        Flow("i2c_merge", "i2c_match"),
        Flow("i2c_match", "i2c_end"),
    ]
    notes = [
        Note("i2c_note_terms", "Cash-in-advance customers pay before the truck is loaded; the others get 30, "
             "60 or 90 days", rep, 1.2, 0, "i2c_send", 200),
    ]
    return Process("invoice_to_cash", "Invoice to cash",
                   "Finance: from the invoice to the payment, with the reminder and the credit hold.",
                   [Pool("Pool_i2c_customer", "Customer"), Pool("Pool_i2c_ahanyar", COMPANY, lanes)],
                   nodes, flows, notes)


def case_to_resolution() -> Process:
    lanes = [Lane("Lane_c2r_service", "Customer service", 250),
             Lane("Lane_c2r_logistics", "Warehouse and logistics", 140),
             Lane("Lane_c2r_quality", "Quality", 220),
             Lane("Lane_c2r_finance", "Finance", 140)]
    cs, wl, q, fin = (lane.id for lane in lanes)
    nodes = [
        Node("c2r_start", "startEvent", "Customer calls, emails or writes on the portal", cs, 0,
             trigger="message", doc="incident.caseorigincode: Phone, Email, Web", table="incident"),
        Node("c2r_create", "userTask", "Create the case, classify it, set the priority", cs, 1,
             doc="incident: ahn_casecategory, casetypecode, prioritycode; the SLA sets responseby and resolveby",
             table="incident"),
        Node("c2r_respond", "sendTask", "Send the first response", cs, 2,
             doc="incident.ahn_firstresponseon against responseby", table="incident"),
        Node("c2r_gw_type", "exclusiveGateway", "What is it?", cs, 3, label="right"),
        Node("c2r_request", "userTask", "Send the certificate or change the delivery", cs, 4, dy=-80,
             doc="Mill certificate request, Delivery change request (casetypecode Request)", table="incident"),
        Node("c2r_check", "userTask", "Check the weighbridge ticket and waybill", wl, 4,
             doc="Weight discrepancy, Late delivery: ahn_shipment weights and times", table="ahn_shipment"),
        Node("c2r_inspect", "userTask", "Inspect at site, claim against the mill", q, 4, dy=-40,
             doc="Quality / spec claim, Damaged or rusted material", table="incident"),
        Node("c2r_sla", "boundaryEvent", "75% of the resolution SLA used", q, 4, attached_to="c2r_inspect",
             trigger="timer", timer="PT36H", doc="incident.isescalated, escalatedon",
             table="incident"),
        Node("c2r_escalate", "userTask", "Escalate to the service lead", q, 5.7, dy=62,
             doc="incident.isescalated", table="incident"),
        Node("c2r_escalated", "endEvent", "Lead alerted", q, 6.7, dy=62),
        Node("c2r_review", "userTask", "Review the invoice and the POD", fin, 4,
             doc="Invoice dispute: invoice, ahn_shipment.ahn_podreceived", table="invoice"),
        Node("c2r_merge_claim", "exclusiveGateway", "", cs, 5),
        Node("c2r_gw_upheld", "exclusiveGateway", "Claim upheld?", cs, 6, label="above"),
        Node("c2r_credit", "userTask", "Issue a credit note or a replacement", fin, 7,
             doc="incident.ahn_claimupheld, ahn_compensationamount", table="incident"),
        Node("c2r_explain", "userTask", "Explain the finding to the customer", cs, 7, table="incident"),
        Node("c2r_merge_out", "exclusiveGateway", "", cs, 8),
        Node("c2r_resolve", "userTask", "Resolve the case", cs, 9,
             doc="incidentresolution.actualend against incident.resolveby", table="incidentresolution"),
        Node("c2r_survey", "sendTask", "Send the satisfaction survey", cs, 10,
             doc="incident.customersatisfactioncode (Customer Voice)", table="incident"),
        Node("c2r_end", "endEvent", "Case closed", cs, 11, table="incident"),
    ]
    flows = [
        Flow("Pool_c2r_customer", "c2r_start", "Complaint or request", kind="message"),
        Flow("c2r_start", "c2r_create"),
        Flow("c2r_create", "c2r_respond"),
        Flow("c2r_respond", "Pool_c2r_customer", "First response", kind="message"),
        Flow("c2r_respond", "c2r_gw_type"),
        Flow("c2r_gw_type", "c2r_request", "Request"),
        Flow("c2r_gw_type", "c2r_check", "Weight or late"),
        Flow("c2r_gw_type", "c2r_inspect", "Quality or damage"),
        Flow("c2r_gw_type", "c2r_review", "Invoice"),
        Flow("c2r_check", "c2r_merge_claim"),
        Flow("c2r_inspect", "c2r_merge_claim"),
        Flow("c2r_review", "c2r_merge_claim"),
        Flow("c2r_sla", "c2r_escalate", route="hv"),
        Flow("c2r_escalate", "c2r_escalated"),
        Flow("c2r_merge_claim", "c2r_gw_upheld"),
        Flow("c2r_gw_upheld", "c2r_credit", "Yes"),
        Flow("c2r_gw_upheld", "c2r_explain", "No"),
        Flow("c2r_credit", "c2r_merge_out"),
        Flow("c2r_explain", "c2r_merge_out"),
        Flow("c2r_request", "c2r_merge_out"),
        Flow("c2r_merge_out", "c2r_resolve"),
        Flow("c2r_resolve", "c2r_survey"),
        Flow("c2r_survey", "Pool_c2r_customer", "Survey", kind="message"),
        Flow("c2r_survey", "c2r_end"),
    ]
    notes = [
        Note("c2r_note_sla", "SLA by priority: first response in 2, 4 or 8 hours; resolved in 2, 5 or 10 days",
             wl, 1.3, 0, "c2r_create", 210),
    ]
    return Process("case_to_resolution", "Case to resolution",
                   "Dynamics 365 Customer Service: from a customer's complaint or request to a closed case.",
                   [Pool("Pool_c2r_customer", "Customer"), Pool("Pool_c2r_ahanyar", COMPANY, lanes)],
                   nodes, flows, notes)


PROCESSES = [lead_to_order, order_to_delivery, invoice_to_cash, case_to_resolution]


def all_processes() -> list[Process]:
    return [build() for build in PROCESSES]
