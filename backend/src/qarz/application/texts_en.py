"""English texts of the chat, the exports, the API's refusals and the permission names.

A key that is absent here reads Uzbek at run time.
"""

CHAT: dict[str, str] = {
    "welcome_new": (
        "Hello! Qarz Daftari keeps track of credit sales in your shop.\nOpen a shop to start. If you are a staff "
        "member, come in through the link the shop owner sent."
    ),
    "welcome_staff": (
        "Active shop: {shop}\n\nRecord a credit sale: Ali 45000\nRecord a payment: Ali -20000 or Ali 20000 "
        "berdi\nAdd a note: Ali 45000 non, sut"
    ),
    "help": (
        "Record a credit sale: Ali 45000\nRecord a payment: Ali -20000 or Ali 20000 berdi\nYou can write the "
        "amount as 45 000, 45.000 or 45k.\n\n/dokon — switch the active shop\n/til — change the "
        "language\n/yordam — this help"
    ),
    "soon": "This command is not ready yet.",
    "ombor_low": "📦 {shop}: items running low",
    "ombor_line": "• {name}: {qty} {unit} (low stock level {low})",
    "ombor_more": "…and others. The full list is in the “Stock” section of the app.",
    "ombor_none": "📦 {shop}: no items are running low.",
    "cash_help": "/kassa — today's cash book: income, expense and balance",
    "cash_today": "💰 {shop}\nCash book, {date}",
    "cash_line": "{method}: income {income}, expense {expense}, balance {closing}",
    "cash_total": "Total: income {income}, expense {expense}, balance {closing}",
    "cash_method_cash": "Cash",
    "cash_method_card": "Card",
    "cash_method_transfer": "Transfer",
    "cash_forbidden": "You have no permission to view the cash book. Contact the shop owner.",
    "only_text": "For now I understand text messages only. Example: Ali 45000",
    "open_shop": "🏪 Open a shop",
    "new_shop": "➕ New shop",
    "ask_shop_name": "Type the name of your shop (up to 80 characters).",
    "shop_name_invalid": "The shop name must be 1 to 80 characters long. Type it again.",
    "shop_created_limited": (
        "✅ The shop “{shop}” is open.\n\nThe free trial is given to the first shop only. Pay for a subscription "
        "to record credit sales in this shop."
    ),
    "shop_created_free": (
        "✅ The shop “{shop}” is open.\n\nThe free trial is given to the first shop only. This shop works on the "
        "free plan: you can record credit sales, for example: Ali 45000. About the plan: /obuna"
    ),
    "shop_created": "✅ The shop “{shop}” is open.\n\nNow you can record credit sales, for example: Ali 45000",
    "lang_prompt": "Choose a language:",
    "lang_set": "Language changed: English.",
    "no_shops": "You are not a member of any shop yet.",
    "choose_shop": "Which shop will you work with?",
    "shop_switched": "Active shop: {shop}",
    "joined_shop": "✅ You joined the shop “{shop}”.\n\nRecord a credit sale: Ali 45000",
    "invitation_invalid": "This invitation link is not valid or has expired. Ask the shop owner for a new one.",
    "already_member": "You are already on the staff of this shop.",
    "credit_saved": "✅ {shop}\n{name}: +{amount}\nTotal debt: {balance}\nDue date: {date}",
    "payment_saved": "✅ {shop}\n{name}: payment {amount}\nDebt left: {balance}",
    "unknown_customer_credit": (
        "{shop}\nNo customer called “{name}” was found.\nAdd a new customer and record a credit sale of {amount}?"
    ),
    "unknown_customer_payment": "{shop}\nNo customer called “{name}” was found. The payment was not recorded.",
    "pick_customer": "{shop}\n“{name}” — which customer? ({amount})",
    "add_and_record": "➕ Add and record",
    "new_customer_option": "➕ New customer: {name}",
    "cancel": "Cancel",
    "cancelled": "Cancelled. Nothing was recorded.",
    "expired": "This button is out of date. Type the message again.",
    "tomorrow": "Tomorrow",
    "end_of_week": "End of week",
    "in_two_weeks": "2 weeks",
    "in_a_month": "1 month",
    "other_date": "📅 Another date",
    "reverse": "↩️ Reverse",
    "reverse_yes": "Yes, reverse it",
    "reverse_no": "No",
    "ask_date": "Type the due date as day.month, for example 25.10",
    "promise_closed": "The due date of this entry is already set. Now a manager or the shop owner changes it.",
    "reversed": "↩️ {shop}\n{name}: the {amount} entry was reversed.\nDebt: {balance}",
    "forbidden": "Your role does not allow this action.",
    "forbidden_permission": "You have no permission for this action. Contact the shop owner.",
    "not_found": "Entry not found.",
    "parse_hint": "I did not understand. Examples:\nAli 45000\nAli -20000\nAli 20000 berdi",
    "parse_amount_not_whole": "The amount is written in whole soum, without tiyin. Example: Ali 45000",
    "parse_ambiguous": "I could not tell which number is the amount. Write the name first, then one amount: Ali 45000",
    "parse_too_long": "The message is too long. Write it shorter: Ali 45000 izoh",
    "amount_range": "The amount must be from 100 soum to 100 000 000 soum.",
    # Only a shop that works in dollars is ever told these.
    "amount_range_usd": "An amount in dollars must be from 0.01 $ to 10 000 $.",
    "parse_amount_too_precise": "A dollar amount has at most two digits after the point. Example: Ali 50.25$",
    "parse_ambiguous_usd": (
        "I could not read the dollar amount for sure. Write one amount and one currency: Ali 50$ or Ali 1250.50$"
    ),
    "notice_amount_invalid_usd": (
        "Write the amount only. In soum: 50000. In dollars, put the $ sign after the amount: 50$ or 50.25$"
    ),
    "two_amounts": "{first} and {second}",
    "PROMISE_BEFORE_SALE": "The due date cannot be before the day of the sale.",
    "PROMISE_TOO_FAR": "The due date can be at most 365 days after the day of the sale.",
    "SUBSCRIPTION_LIMITED": (
        "The subscription has ended: no new credit sales can be recorded. Taking payments keeps working. /obuna"
    ),
    "FREE_PLAN_FULL": (
        "The free plan covers up to {limit} customers; the new customer was not added. Pay for a subscription to "
        "have more customers: /obuna"
    ),
    "SHOP_SUSPENDED": "The shop is suspended for a time.",
    "EXCEEDS_BALANCE": "A payment cannot be larger than the customer's debt.",
    "CUSTOMER_ARCHIVED": "This customer is in the archive. Unarchive them first.",
    "ALREADY_REVERSED": "This entry is already reversed.",
    "ENTRY_OF_DOCUMENT": (
        "This entry was created by a goods return document. To cancel it, cancel the document itself in the "
        "“Stock” section of the app."
    ),
    "CANNOT_REVERSE_REVERSAL": "A reversal entry cannot be reversed.",
    "WOULD_GO_NEGATIVE": "Reversing this would make the debt negative. Reverse the later payment first.",
    "error": "Something went wrong. Try again.",
    "TIMEOUT": "It took too long and was stopped. Nothing was recorded. Try again.",
    "consent_v2": (
        "The shop {shop} keeps your credit purchases and payments through the Qarz Daftari service. Data that is "
        "kept: the name the shop gave you, your phone number (if you gave it), the identifier of your Telegram "
        "account, credit sale and payment entries, the products you took. Purpose: so that you too can see the "
        "debt account, and to send reminders. The data is seen only by you and by the staff of this shop, and is "
        "not given to other shops. At any time you can disconnect with /uzish or ask to delete your data with "
        "/ochirish. Do you agree?"
    ),
    "consent_yes": "✅ I agree",
    "consent_no": "No",
    "consent_declined": "Consent was not given. Nothing about you was kept.",
    "linked": (
        "✅ You are connected to your account at the shop “{shop}”. From now on you will get a message here about "
        "every entry.\nSee your debt: /qarzim"
    ),
    "waiting_ok": (
        "✅ Your request was sent to the shop “{shop}”. A seller will connect you to your record in the ledger."
    ),
    "waiting_full": (
        "The list of people waiting to connect at the shop “{shop}” is full right now. Ask a seller to go "
        "through the list, or try again later."
    ),
    "link_invalid": "This link is not valid or has expired. Ask the shop for a new one.",
    "link_taken": "This account is connected to another Telegram account. Contact the shop.",
    "link_already": "You are already connected to this shop or waiting to be connected. Your debt: /qarzim",
    "accounts_header": "Your debts:",
    "account_line": "{shop}: {balance}",
    "no_accounts": "You are not connected to an account at any shop yet. Ask the shop for a link or a QR code.",
    "unlink_choose": (
        "Which shop do you want to disconnect from? Messages will stop; the entries in the shop's ledger stay."
    ),
    "unlink_button": "Disconnect: {shop}",
    "unlinked": "You are disconnected from the shop “{shop}”. You will get no more messages from this shop.",
    "n_credit": (
        "{shop}\n{name}, a credit sale was recorded for you: {amount}\n{goods}Due date: {date}\nYour total debt: "
        "{balance}"
    ),
    "n_payment": "{shop}\n{name}, your payment was taken: {amount}\nYour debt left: {balance}",
    "n_reversed_credit": "{shop}\n{name}, the credit sale entry of {amount} was reversed.\nYour total debt: {balance}",
    "n_reversed_payment": "{shop}\n{name}, the payment entry of {amount} was reversed.\nYour total debt: {balance}",
    "n_promise": "{shop}\n{name}, due date of the {amount} credit sale: {date}\nYour total debt: {balance}",
    "n_line": "• {name} — {qty} {unit}: {total}",
    "n_more_lines": "… and {count} more",
    "removal_choose": "At which shop should your data be deleted?",
    "removal_button": "Delete: {shop}",
    "removal_confirm": (
        "Your name, your phone number and the connection to your Telegram account at the shop “{shop}” will be "
        "deleted. The debt and payment amounts stay in the shop's ledger without a name. If you have a debt, the "
        "data is deleted when it is paid in full. This cannot be undone. Continue?"
    ),
    "removal_yes": "Yes, delete it",
    "removal_done": "Your data at the shop “{shop}” was deleted.",
    "removal_waiting": (
        "Your request was accepted. Your data will be deleted once your debt at the shop “{shop}” ({balance}) is "
        "paid in full."
    ),
    "dispute_button": "⚠️ Dispute",
    "ask_dispute_reason": "Write the reason for your dispute in a few words (3 to 300 characters).",
    "reason_invalid": "The reason must be 3 to 300 characters long. Press the button again and write it once more.",
    "dispute_sent": "Your dispute was sent to the shop “{shop}”. The entry stays in your debt until it is reviewed.",
    "DISPUTE_NOT_ALLOWED": "This entry cannot be disputed, or the dispute was already reviewed.",
    "s_dispute": "⚠️ {shop}\n{name} disputed the {amount} entry:\n“{reason}”",
    "decline_button": "Decline",
    "ask_decline_reason": "Write the reason for declining. It is sent to the customer.",
    "dispute_declined_staff": "Dispute declined. The customer was sent a message with the reason.",
    "n_dispute_declined": "{shop}\nYour dispute about the {amount} entry was declined.\nReason: {reason}",
    "s_dispute_withdrawn": "{shop}\n{name} withdrew the dispute about the {amount} entry.",
    "r1_due_today": "Hello, {name}! A reminder from the shop “{shop}”: {amount} is due today. Thank you!",
    "r1_overdue": (
        "Hello, {name}! Your debt of {amount} at the shop “{shop}” is overdue. We would be grateful if you could "
        "pay it."
    ),
    "r2_due_today": "“{shop}”: {name}, {amount} is due today.",
    "r2_overdue": "“{shop}”: {name}, your debt of {amount} is overdue. Please pay it.",
    "r3_due_today": "Dear {name}, the shop “{shop}” values you. We remind you that {amount} is due today.",
    "r3_overdue": (
        "Dear {name}, we remind you that your debt of {amount} at the shop “{shop}” is overdue. We would be glad "
        "if you paid it at a time that suits you."
    ),
    "shop_suspended": (
        "The shop “{shop}” was suspended by the service administration. Reason: {reason}\nNow only the shop "
        "owner can see and export the data."
    ),
    "shop_unsuspended": "The shop “{shop}” is working again: the suspension was lifted. Note: {reason}",
    # To an account that may be in someone else's hands: the bare fact and nothing else.
    "owner_reassigned_old": "The ownership of the shop “{shop}” was changed by the service administration.",
    "owner_reassigned_new": (
        "By decision of the service administration you are now the owner of the shop “{shop}”. The shop opens in "
        "the panel."
    ),
    "owner_reassigned_new_deletion": (
        "By decision of the service administration you are now the owner of the shop “{shop}”. The shop opens in "
        "the panel.\nNote: the shop is waiting for deletion and will be deleted for good on {due}. You can "
        "cancel this in the panel."
    ),
    "support_opened": (
        "“{shop}”: a service administrator opened access to view the shop's data in order to help. Reason: "
        "{reason}\nThe access is valid until {until} and ends by itself. It is for viewing only: nothing is "
        "changed. You can close it in the panel at any time."
    ),
    "support_closed": "“{shop}”: the service administrator closed the access to view the shop's data.",
    "LIMIT_REACHED": "This sale goes over the customer's credit limit. A manager or the shop owner can record it.",
    "USD_BALANCE_OPEN": (
        "Dollars cannot be turned off: customers have debts in dollars. All debts in dollars must be closed first."
    ),
    "limit_warning": "⚠️ The debt went over the limit: limit {limit}, debt {balance}.",
    "sub_header": "“{shop}” — subscription",
    "sub_state_trial": "Trial: until {date} (days left: {days}).",
    "sub_state_active": "Paid: until {date} (days left: {days}).",
    "sub_state_limited": (
        "The paid period has ended: no new credit sales can be recorded. Taking payments, viewing and messages "
        "to customers keep working."
    ),
    "sub_state_suspended": "The shop is suspended for a time. Contact support.",
    "sub_state_free": "Free plan: no end date, the shop works in full.",
    "sub_customers": "Customers: {used}. The free plan covers up to {limit} customers.",
    "sub_then_free": "If you do not pay, the shop keeps working in full on the free plan (up to {limit} customers).",
    "sub_then_limited": (
        "If you do not pay, no new credit sales can be recorded: you have more customers ({used}) than the free "
        "plan covers ({limit})."
    ),
    "sub_quota_left": "SMS reminders come with the subscription: {left} left this month ({quota} a month).",
    "sub_paying_adds": "If you pay, the number of customers is not limited.",
    "sub_paying_adds_quota": (
        "If you pay, the number of customers is not limited and up to {quota} SMS reminders a month are sent."
    ),
    "sub_price": "Price: {price} a month.",
    "sub_pay_to": "Card to pay to — {label}: {card}. After the transfer, send the receipt here.",
    "sub_other_cards_button": "Other cards: {count}",
    "sub_choose_card": "Which card will you pay to? Choose.",
    "sub_cards_back": "⬅️ Back",
    "receipt_card": "Card: {card}",
    "sub_no_card": "Payment details have not been entered yet.",
    "sub_paid_online": "“{shop}”: payment of {amount} taken. The subscription is paid until {date}.",
    "sub_choose_months": "After paying, choose how many months you paid for and send the receipt.",
    "sub_months_button": "Months: {months} — {amount}",
    "ask_sub_receipt": "“{shop}”: {amount}, months: {months}. Now send a photo or a PDF file of the receipt here.",
    "sub_receipt_invalid": (
        "I could not accept this file. A receipt must be a JPEG, PNG or WebP image or a PDF, and not larger than "
        "5 MB. Send it again or cancel."
    ),
    "sub_receipt_sent": (
        "✅ “{shop}”: receipt accepted (months: {months}, {amount}). We will tell you once an administrator has "
        "reviewed it."
    ),
    "sub_receipt_approved": "✅ “{shop}”: payment approved (months: {months}). The subscription is paid until {date}.",
    "sub_receipt_rejected": "“{shop}”: the payment receipt for {amount} was rejected. Reason: {reason}",
    "a_receipt_new": "New subscription receipt: “{shop}”, {amount}, months: {months}. Review it in the admin panel.",
    "a_receipt_copies": "⚠️ This exact file was sent before. Receipts with it: {count}.",
    "receipt_approve_button": "✅ Approve",
    "receipt_reject_button": "Reject",
    "a_sign_in_first": "First sign in to the admin panel and confirm your code. After that these buttons will work.",
    "a_receipt_approved": "✅ “{shop}”: receipt approved (months: {months}). The subscription is paid until {date}.",
    "a_receipt_rejected": "“{shop}”: receipt rejected. Reason: {reason}",
    "a_receipt_decided": "A decision on this receipt has already been made.",
    "a_receipt_use_panel": "Review this receipt in the admin panel: the number of months must be entered.",
    # Shown over the button to a Telegram administrator of the review group (at most 200 characters).
    "g_reason_in_private": (
        "Write the reason in a private message to the bot. If you have not chatted with the bot yet, open it, "
        "press “Start”, then press “Reject” again."
    ),
    "g_receipt_needs_panel": (
        "This receipt does not state the number of months. A platform administrator handles it in the panel."
    ),
    "ask_receipt_reject_reason": "Write the reason for rejecting (3–500 characters). It is sent to the shop owner.",
    "a_reason_invalid": "The reason must be 3 to 500 characters long. Write it again or cancel.",
    "sub_trial_ending": "“{shop}”: the trial ends on {date} (days left: {days}). To continue: /obuna",
    "sub_paid_ending": "“{shop}”: the paid period ends on {date} (days left: {days}). To extend: /obuna",
    "sub_limited": (
        "“{shop}”: the subscription has ended. No new credit sales can be recorded now; taking payments, viewing "
        "and messages to customers keep working. To pay: /obuna"
    ),
    "sub_free_now": (
        "“{shop}”: the paid period has ended. The shop moved to the free plan and keeps working in full: up to "
        "{limit} customers. If you need more customers: /obuna"
    ),
    "move_date_button": "📅 Move due date",
    "ask_move_date": "What date should the due date be moved to? Write it as day.month, for example 25.10",
    "move_date_invalid": "I did not understand the date. Write it as day.month, for example 25.10",
    "date_request_sent": (
        "Your request was sent to the shop “{shop}”: move the due date to {date}. The answer will come here."
    ),
    "REQUEST_ALREADY_OPEN": "A request to move the due date of this entry is already being looked at.",
    "DATE_REQUEST_NOT_ALLOWED": "A request to move the due date of this entry cannot be accepted right now.",
    "PROMISE_NOT_CHANGEABLE": "This entry has no due date, or the entry was reversed.",
    "date_not_later": "The new date must be after the current due date.",
    "date_fully_paid": "This entry is paid in full or reversed: there is no need to move its due date.",
    "date_declined_recently": (
        "Your request about this entry was declined recently. You can ask again 7 days after the decline."
    ),
    "date_request_closed": "This request was already reviewed or no longer applies.",
    "s_date_request": "📅 {shop}\n{name} asks to move the due date of the {amount} credit sale from {old} to {date}.",
    "accept_button": "✅ Accept",
    "reason_line": "Reason: {reason}",
    "date_accepted_staff": "✅ {name}: the due date of the {amount} credit sale was moved to {date}.",
    "date_declined_staff": "Request declined: {name}, {amount}. The due date did not change.",
    "n_date_accepted": (
        "{shop}\nYour request to move the due date of the {amount} credit sale was accepted.\nNew due date: {date}"
    ),
    "n_date_declined": "{shop}\nYour request to move the due date of the {amount} credit sale to {date} was declined.",
    "n_date_changed": "{shop}\n{name}, the due date of the {amount} credit sale was changed: {old} → {date}",
    "n_opening": (
        "{shop}\n{name}, your earlier debt was entered in the ledger: {amount}\nDue date: {date}\nYour total "
        "debt: {balance}"
    ),
    "s_import_applied": (
        "📥 {shop}\nImport applied. Debt entries: {entries}, total {amount}. New customers: {customers}.\nIt can "
        "be undone in full within 24 hours."
    ),
    "s_import_undone": "↩️ {shop}\nImport undone. Entries reversed: {entries}. Import amount: {amount}.",
    "import_checked": (
        "📥 {shop}\nThe import file was checked: rows: {rows}, no mistakes. You can review and apply it in the app."
    ),
    "import_rejected": (
        "📥 {shop}\nMistakes were found in the import file. Rows with mistakes: {errors}. See the list in the app "
        "and upload the corrected file again."
    ),
    "import_unreadable": "📥 {shop}\nThe import file could not be read as a sheet. The reason is shown in the app.",
    "import_refused": (
        "📥 {shop}\nThe import was not applied: the data changed after the check. Review it again in the app."
    ),
    "import_refused_free_plan": (
        "📥 {shop}\nThe import was not applied: the free plan covers up to {limit} customers. Pay for a "
        "subscription to have more customers: /obuna"
    ),
    "import_undo_refused": (
        "↩️ {shop}\nThe import could not be undone: payments were made on its entries. Nothing changed."
    ),
    "import_failed": "📥 {shop}\nThe requested import action did not finish. Nothing changed; try again.",
    "shop_deletion_requested": (
        "Deletion of the shop “{shop}” was requested. The data will be deleted for good on {date}. Until then "
        "you can export it or cancel."
    ),
    "shop_deletion_cancelled": "Deletion of the shop “{shop}” was cancelled. The shop works as before.",
    "shop_erased": "The shop “{shop}” and all its data were deleted.",
    # --- payment notices (REQ-060, REQ-061) ---
    "notice_choose_shop": "Which shop do you want to tell about a payment?",
    "notice_shop_button": "{shop}: {balance}",
    "notice_nothing_owed": "You have no debt at the shop “{shop}”.",
    "ask_notice_amount": (
        "Your debt at the shop “{shop}”: {balance}\nHow much did you pay? Write the amount only, for example: 50000"
    ),
    "notice_amount_invalid": (
        "Write the amount only, for example: 50000 or 50 000. The amount is in whole soum, from 100 soum to 100 "
        "000 000 soum."
    ),
    "notice_amount_exceeds": "The amount cannot be larger than your debt. Your debt: {balance}",
    "ask_notice_receipt": (
        "Payment: {amount}\nIf you have a receipt, send a photo or a PDF file of it here. If there is no "
        "receipt, press the button."
    ),
    "notice_without_receipt": "Send without a receipt",
    "notice_receipt_hint": "Send a photo or a PDF file of the receipt, or press one of the buttons.",
    "notice_receipt_invalid": (
        "I could not accept this file. A receipt must be a JPEG, PNG or WebP image or a PDF, and not larger than "
        "5 MB. Send it again or press one of the buttons."
    ),
    "notice_sent": (
        "✅ Your notice about a payment of {amount} was sent to the shop “{shop}”. Your debt goes down once the "
        "shop accepts it."
    ),
    "s_notice": "💵 {shop}\n{name} says they paid {amount}.\nCurrent debt: {balance}",
    "s_notice_receipt": (
        "💵 {shop}\n{name} says they paid {amount}.\nCurrent debt: {balance}\n📎 A receipt is attached: you can "
        "see it in the app."
    ),
    "s_receipt_seen_before": "⚠️ This exact receipt was sent to this shop before.",
    "notice_accept_button": "✅ Accept",
    "notice_accepted_staff": "✅ {shop}\n{name}: payment {amount} taken.\nDebt left: {balance}",
    "notice_declined_staff": "Payment notice declined. The customer was sent a message with the reason.",
    "n_notice_accepted": "{shop}\nYour notice about a payment of {amount} was accepted.",
    "n_notice_corrected": (
        "{shop}\nYour notice about a payment of {amount} was accepted, but the payment was recorded as {recorded}."
    ),
    "n_notice_declined": "{shop}\nYour notice about a payment of {amount} was declined.\nReason: {reason}",
    "PAYMENT_NOTICE_NOT_ALLOWED": (
        "You have too many payment notices not yet reviewed. Wait for the shop to answer them first."
    ),
    "PAYMENT_NOTICE_NOT_OPEN": "This payment notice was already reviewed or has expired.",
    "FILE_STORE_UNAVAILABLE": "File storage is not working right now. Try again a little later.",
    "export_ready": (
        "✅ The export of the shop “{shop}” is ready. Download it in the export section of the app; the file is "
        "kept for 7 days."
    ),
    "export_failed": "The export of the shop “{shop}” could not be prepared. Request it again a little later.",
    "EXPORT_NOT_ALLOWED": "An export is already being prepared, or there are no exports left for today.",
    "EXPORT_NOT_READY": "This export file is not ready yet or has expired.",
    "SUBSCRIPTION_RECEIPT_NOT_ALLOWED": (
        "You have too many receipts not yet reviewed. Wait for the administrator's answer."
    ),
    "currency": "soum",
    # The operations alerts (DEC-078).
    "ops_title": "⚠️ Qarz Daftari: system monitoring",
    "ops_firing": "🔴 Started:",
    "ops_reminder": "🟠 Still going on:",
    "ops_resolved": "🟢 Fixed:",
    "ops_line": "• {name}: {text}{value} (since {since})",
    "ops_line_resolved": "• {name}: {text} ({since} — {until})",
    "ops_more": "…more alerts in the next message: {count}.",
    "ops_footer": "What to do: runbook 16 (docs/10-operations/runbooks.md).",
    "ops_test": (
        "✅ Qarz Daftari: TEST alert ({at}).\nThis is not a real fault. It was sent to check that system "
        "monitoring messages reach this chat."
    ),
    "ops_db_down": (
        "🔴 Qarz Daftari: the worker cannot connect to the database (since {since}).\nThe monitoring state is not "
        "being saved and the other checks have stopped. Runbook 16."
    ),
    "ops_db_up": "🟢 Qarz Daftari: the database is working again ({since} — {until}).",
    "ops_rule_ErrorRateHigh": "more than 2% of requests end with a server error",
    "ops_rule_OutboxOld": "messages are not going out: the next message has waited more than 10 minutes",
    "ops_rule_RemindersNotRunning": "the reminders job has not finished for an hour",
    "ops_rule_SmsRefused": "an SMS was refused by the provider or dropped after a day",
    "ops_rule_SmsNotGoingOut": "SMS are stuck in the queue, the provider is not taking them",
    "ops_rule_ReceiptsWaiting": "a subscription receipt has waited more than a day for a decision",
    "ops_rule_CrossTenantAttempt": "a signed-in user asked for a shop that is not theirs",
    "ops_rule_InvalidSignaturesRepeated": "repeated attempts with a wrong signature",
    "ops_rule_AdminSecondFactorRepeated": "an administrator's second factor code was refused again and again",
    "ops_rule_SupportAccessOpened": "an administrator opened support access to a shop",
    "ops_rule_AdminWithoutSupportAccess": "an administrator asked for shop data without support access",
    "ops_rule_ShopOwnerReassigned": "an administrator replaced a shop owner",
    "ops_rule_MetricsMissing": "the API metrics cannot be read",
    "ops_rule_BackupFailed": "the latest backup failed",
    "ops_rule_BackupMissing": "no fresh backup in the store (none in 26 hours, or no full backup in 8 days)",
    "ops_rule_WalArchiveStale": "the WAL archive is older than 5 minutes, or the check itself is not working",
    "ops_rule_RestoreTestNotPassed": "the restore test has not passed in 8 days, or has never passed",
    "ops_rule_RestoreTestFailed": "the latest restore test failed",
    "ops_rule_FilesCopyStale": "stored files are not being copied to the store",
    "ops_rule_DiskAlmostFull": "the disk is more than 80% full",
    "ops_rule_JobNotRunning": "a scheduled job has not finished its cycle",
    "ops_rule_LedgerMismatch": "the stored open debts differ from the ledger entries",
    "ops_rule_StockMismatch": "the stored stock on hand or the debt to a supplier differs from its own entries",
    "ops_rule_ApiDown": "the API does not answer the /healthz request",
    "ops_rule_TelegramRefusesBot": "Telegram refuses the bot token",
    "ops_rule_TelegramUnreachable": "no connection to Telegram",
    "ops_rule_DispatcherFailing": "the message sender has not finished a single cycle for 5 minutes",
}

EXPORT: dict[str, str | tuple[str, ...]] = {
    "sheet_summary": "Summary",
    "sheet_customers": "Customers",
    "sheet_ledger": "Ledger",
    "sheet_promises": "Due date history",
    "sheet_goods": "Items",
    # Only in the workbook of a shop that has a cash book.
    "sheet_cash": "Cash book",
    "cash": (
        "Day",
        "Recorded at",
        "Direction",
        "Payment method",
        "Currency",
        "Amount",
        "Category",
        "Note",
        "Cancelled",
        "Reason for cancelling",
        "Recorded by",
        "Staff ID",
        "Ledger payment (ID)",
        "Entry ID",
    ),
    "cash_income": "Income",
    "cash_expense": "Expense",
    "cash_cash": "Cash",
    "cash_card": "Card",
    "cash_transfer": "Transfer",
    "customers": ("Customer", "Phone", "Status", "Credit limit", "Debt", "Date added", "Customer ID"),
    "ledger": (
        "Date and time",
        "Customer",
        "Kind",
        "Amount",
        "Effect on debt",
        "Note",
        "Due date",
        "Reversed",
        "Reverses entry (ID)",
        "Recorded by",
        "Staff ID",
        "Number in customer's entries",
        "Entry ID",
        "Customer ID",
    ),
    "promises": ("Entry ID", "Customer", "Due date", "Set at", "Set by", "Reason"),
    "goods": ("Entry ID", "Date and time", "Customer", "Line", "Item", "Quantity", "Unit", "Price", "Total"),
    "months": (
        "Month",
        "Credit sales amount",
        "Credit sales count",
        "Opening debt amount",
        "Payments amount",
        "Payments count",
        "Reversed entries count",
    ),
    "summary_shop": "Shop",
    "summary_made": "Export time (Tashkent)",
    "summary_customers": "Number of customers",
    "summary_debtors": "Number of customers in debt",
    "summary_outstanding": "Total debt",
    # Only in the workbook of a shop that has dollar entries.
    "currency": "Currency",
    "limit_usd": "Credit limit ($)",
    "owed_usd": "Debt ($)",
    "summary_debtors_usd": "Number of customers in debt in dollars",
    "summary_outstanding_usd": "Total debt ($)",
    "summary_months_usd": "By month, in dollars (reversed entries and reversal entries are left out)",
    "summary_entries": "Number of entries in the ledger",
    "summary_months": "By month (reversed entries and reversal entries are left out)",
    "kind_credit": "Credit sale",
    "kind_opening": "Opening debt",
    "kind_payment": "Payment",
    "kind_reversal": "Reversal",
    "status_active": "Active",
    "status_archived": "Archived",
    "status_anonymized": "Data deleted",
    "role_seller": "Seller",
    "role_manager": "Manager",
    "role_owner": "Shop owner",
    "actor_default": "Shop's usual due date",
    "actor_staff": "Staff member",
    "actor_customer_request": "At the customer's request",
    "yes": "Yes",
    "no": "No",
    "sheet_stock": "Stock",
    "sheet_stock_movements": "Stock movements",
    "sheet_stock_documents": "Stock documents",
    "sheet_suppliers": "Suppliers",
    "sheet_supplier_entries": "Supplier accounts",
    "stock": (
        "Item",
        "Unit",
        "Selling price",
        "In stock",
        "Low stock level",
        "Cost price currency",
        "Average cost price",
        "Stock value (at cost price)",
        "Barcodes",
        "Item ID",
    ),
    "stock_movements": (
        "Date and time",
        "Item",
        "Kind",
        "Quantity",
        "Unit",
        "In stock after the movement",
        "Cost price (total)",
        "Currency",
        "Sale amount",
        "Reason",
        "Document",
        "Reversed",
        "Movement ID",
        "Item ID",
    ),
    "stock_documents": (
        "Number",
        "Kind",
        "Status",
        "Date",
        "Supplier",
        "Customer",
        "Currency",
        "Total",
        "Paid",
        "Reason",
        "Note",
        "Reason for cancelling",
        "Document ID",
    ),
    "suppliers": ("Supplier", "Phone", "Note", "Status", "Our debt (soum)", "Our debt ($)", "Supplier ID"),
    "supplier_entries": (
        "Date and time",
        "Supplier",
        "Kind",
        "Amount",
        "Currency",
        "Effect on debt",
        "Note",
        "Cancelled",
        "Document",
        "Entry ID",
        "Supplier ID",
    ),
    "movement_receipt": "Goods receipt",
    "movement_sale": "Sale",
    "movement_customer_return": "Returned by customer",
    "movement_supplier_return": "Returned to supplier",
    "movement_write_off": "Written off",
    "movement_correction": "Stocktake correction",
    "movement_reversal": "Reversal",
    "document_receipt": "Goods receipt",
    "document_customer_return": "Return from customer",
    "document_supplier_return": "Return to supplier",
    "document_write_off": "Write-off",
    "document_stocktake": "Stocktake",
    "document_draft": "Draft",
    "document_posted": "Posted",
    "document_cancelled": "Cancelled",
    "reason_damaged": "Damaged",
    "reason_expired": "Expired",
    "reason_lost": "Lost",
    "reason_own_use": "For own use",
    "supplier_purchase": "Goods received",
    "supplier_opening": "Opening debt",
    "supplier_payment": "Payment",
    "supplier_return": "Goods returned",
    "supplier_reversal": "Reversal",
}

ERRORS: dict[str, str] = {
    "UNAUTHENTICATED": "Sign in first.",
    "NOT_FOUND": "Not found.",
    "FORBIDDEN_ROLE": "Your role does not allow this action.",
    "FORBIDDEN_PERMISSION": "You have no permission for this action. Contact the shop owner.",
    "BEYOND_OWN_PERMISSIONS": "This is above your own permissions: only the shop owner can do it.",
    "VALIDATION": "The data was entered wrongly.",
    "IDEMPOTENCY_KEY_REUSED": "This request key was used for another action.",
    "ALREADY_MEMBER": "You are already on the staff of this shop.",
    "OWNER_MEMBERSHIP_FIXED": "The shop owner's membership changes only by transferring ownership.",
    "TRANSFER_PENDING": "An offer to transfer ownership is already waiting for an answer.",
    "TRANSFER_TARGET_INVALID": "Ownership can be transferred only to an active manager of this shop.",
    "NOT_TRANSFER_TARGET": "Only the manager who got the offer can answer it.",
    "SUBSCRIPTION_LIMITED": (
        "The subscription has ended: no new credit sales can be recorded. Taking payments and viewing keep working."
    ),
    "FREE_PLAN_FULL": (
        "The free plan covers up to {limit} customers; the new customer was not added. Pay for a subscription to "
        "have more customers: /obuna in the bot or the “Subscription” page."
    ),
    "SHOP_SUSPENDED": "The shop is suspended. Only the shop owner can see and export the data.",
    "CUSTOMER_ARCHIVED": "This customer is in the archive. Unarchive them first.",
    "CUSTOMER_HAS_BALANCE": "A customer with a debt cannot be archived.",
    "USD_BALANCE_OPEN": (
        "Dollars cannot be turned off: customers have debts in dollars. All debts in dollars must be closed first."
    ),
    "EXCEEDS_BALANCE": "A payment cannot be larger than the customer's debt.",
    "ALREADY_REVERSED": "This entry is already reversed.",
    "CANNOT_REVERSE_REVERSAL": "A reversal entry cannot be reversed.",
    "WOULD_GO_NEGATIVE": "Reversing this would make the debt negative. Reverse the later payment first.",
    "PROMISE_ALREADY_SET": "The due date is already set. Now a manager or the shop owner changes it.",
    "CATALOG_NAME_TAKEN": "The catalog already has a product with this name (it may be hidden).",
    "CATALOG_ITEM_NOT_LEARNED": "This product was already reviewed.",
    "CATALOG_MERGE_TARGET_INVALID": "You can merge only into a reviewed product that is visible in the catalog.",
    "CUSTOMER_ALREADY_LINKED": "This customer is already connected to a Telegram account.",
    "DISPUTE_NOT_ALLOWED": "This entry cannot be disputed, or the dispute was already reviewed.",
    "REQUEST_ALREADY_OPEN": "A request to move the due date of this entry is already being looked at.",
    "DATE_REQUEST_NOT_ALLOWED": "A request to move the due date of this entry cannot be accepted right now.",
    "PROMISE_NOT_CHANGEABLE": "This entry has no due date, or the entry was reversed.",
    "LINES_ALREADY_ADDED": "Items were already added to this entry.",
    "LINES_SUM_MISMATCH": "The items do not add up to the entry amount.",
    "LINES_WINDOW_CLOSED": (
        "The time to add items has passed: it is possible only until the end of the day after the sale."
    ),
    "REMINDERS_OFF": "Reminders are turned off for the shop or for this customer.",
    "REMINDER_NOT_DUE": "This customer has no debt that is overdue or due today.",
    "REMINDER_LIMIT_REACHED": "A reminder was already sent to this customer today. One a day is allowed.",
    "CUSTOMER_UNREACHABLE": (
        "This customer cannot be reached: Telegram is not connected, and SMS is off or there is no number."
    ),
    "LIMIT_REACHED": "This sale goes over the customer's credit limit. A manager or the shop owner can record it.",
    "DELETION_ALREADY_REQUESTED": "Shop deletion was already requested.",
    "DELETION_NOT_REQUESTED": "Shop deletion was not requested.",
    "SECOND_FACTOR_INVALID": "The code is wrong, out of date or already used. Enter a new code.",
    "SECOND_FACTOR_LOCKED": "Too many wrong codes were entered. Try again a little later.",
    "ADMIN_ALREADY_ENROLLED": "The second factor is already set up. Contact the operator to replace it.",
    "ADMIN_NOT_ENROLLED": "Set up the second factor (an authenticator app) first.",
    "SUBSCRIPTION_CHANGE_REFUSED": "This change cannot be made in the current state of the subscription.",
    "OWNER_REASSIGNMENT_REFUSED": "The shop cannot be given to this person.",
    "SUPPORT_ACCESS_REQUIRED": "To see the shop's data, first open access and give a reason.",
    "SUPPORT_ACCESS_ALREADY_OPEN": "You already have open access to this shop.",
    "SUPPORT_ACCESS_NOT_OPEN": "There is no open access: it has ended or was already closed.",
    "PAYMENT_NOTICE_NOT_ALLOWED": "You have too many payment notices not yet reviewed. Wait for the shop's answer.",
    "PAYMENT_NOTICE_NOT_OPEN": "This payment notice was already reviewed or has expired.",
    "EXPORT_NOT_ALLOWED": "An export is already being prepared, or there are no exports left for today.",
    "EXPORT_NOT_READY": "This export file is not ready yet or has expired.",
    "FILE_STORE_UNAVAILABLE": "File storage is not working right now. Try again a little later.",
    "IMPORT_NOT_APPLICABLE": "This import cannot be applied in its current state. Refresh the preview.",
    "IMPORT_UNDO_REFUSED": "This import cannot be undone: the time has passed or payments were made on its entries.",
    "SUBSCRIPTION_RECEIPT_NOT_ALLOWED": (
        "You have too many receipts not yet reviewed. Wait for the administrator's answer."
    ),
    "RECEIPT_ALREADY_DECIDED": "A decision on this receipt has already been made.",
    "CASH_ENTRY_OF_LEDGER": (
        "This entry is a customer's payment. To cancel it, reverse that payment on the customer page."
    ),
    "CASH_ENTRY_OF_STOCK": (
        "This entry was made by the stock (a payment to a supplier, a purchase of goods or a return). "
        "To cancel it, cancel that payment or document."
    ),
    "CASH_ENTRY_CANCELLED": "This cash book entry is already cancelled.",
    "CASH_CATEGORY_ARCHIVED": "This category is in the archive. Choose another one or unarchive it.",
    "CASH_CATEGORY_NAME_TAKEN": "There is already a category with this name (it may be in the archive).",
    "CASH_CATEGORY_FIXED": "The category that customers' payments go to cannot be archived or deleted.",
    "CASH_CATEGORY_IN_USE": "This category has entries and cannot be deleted. It can be archived.",
    "ONLINE_PAY_OFF": "Online payment is not turned on yet. To pay by card: /obuna",
    "STOCK_INSUFFICIENT": (
        "There is not enough of this item in stock. Record a goods receipt first or lower the quantity."
    ),
    "STOCK_ALREADY_USED": (
        "It cannot be cancelled: these goods have already left the stock. Record a return to the supplier or "
        "a write-off."
    ),
    "COST_CURRENCY_MISMATCH": (
        "The cost price of this item is kept in another currency. A goods receipt in another currency is "
        "possible once the stock runs out."
    ),
    "ENTRY_OF_DOCUMENT": "This entry was created by a document. To cancel it, cancel the document itself.",
    "BARCODE_TAKEN": "This barcode is attached to another item.",
    "ITEM_NOT_COUNTABLE": ("This item cannot be tracked in stock: review it first and choose its stock unit."),
    "DOCUMENT_NOT_DRAFT": "This document is already posted or cancelled.",
    "DOCUMENT_CANCELLED": "This document is already cancelled.",
    "SUPPLIER_NAME_TAKEN": "There is already a supplier with this name (it may be in the archive).",
    "SUPPLIER_ARCHIVED": "This supplier is in the archive. Unarchive it first.",
    "SUPPLIER_HAS_BALANCE": "A supplier whose account is not settled cannot be archived.",
    "RATE_LIMITED": "Too many requests. Wait a little and try again.",
    "TIMEOUT": "The request took too long and was stopped. Nothing was saved. Try again.",
    "ERROR": "Something went wrong.",
}

PERMISSIONS: dict[str, str] = {
    "group.customers": "Customers",
    "group.ledger": "Debts and payments",
    "group.goods": "Items",
    "group.stock": "Stock",
    "group.suppliers": "Suppliers",
    "group.reminders": "Reminders",
    "group.reports": "Reports and files",
    "group.cash": "Cash book",
    "group.shop": "Shop and staff",
    "group.owner": "Owner only",
    "ledger.view": "View the ledger: customers, debts, item list",
    "customers.create": "Add a customer and connect them to Telegram",
    "customers.edit": "Edit a customer, set a limit, archive",
    "customers.share": "Give a customer a read-only link and QR code, revoke it",
    "credits.record": "Record a credit sale",
    "payments.record": "Take a payment",
    "payment_notices.decide": "Accept or decline a payment notice sent by a customer",
    "entries.others": "Add a due date and items to a sale recorded by another staff member",
    "entries.over_limit": "Record a credit sale over the limit",
    "entries.cancel": "Reverse an entry",
    "promises.change": "Change a due date and decide due date requests",
    "disputes.decide": "View and decide disputes",
    "goods.edit": "Edit the item list",
    "stock.view": "View the stock: stock on hand, items running low, movements",
    "stock.receive": "Goods receipt and return to supplier",
    "stock.adjust": "Write-off, stocktake and taking goods back from a customer",
    "stock.costs.view": "See cost price, profit and the stock report",
    "suppliers.view": "See suppliers and the debt to them",
    "suppliers.manage": "Add, edit and archive suppliers and enter an opening debt",
    "suppliers.pay": "Record a payment to a supplier and cancel it",
    "reminders.send": "Send a reminder to a customer",
    "reports.view": "View reports",
    "reports.export": "Download data to a file",
    "imports.run": "Upload data from a file",
    "cash.view": "View the cash book: day book, balances, period report",
    "cash.record_income": "Record income in the cash book",
    "cash.record_expense": "Record an expense from the cash book",
    "cash.cancel": "Cancel a cash book entry",
    "cash.categories": "Add, rename and archive cash book categories",
    "cash.backfill": "Copy earlier payments to the cash book",
    "settings.view": "View shop settings",
    "settings.edit": "Change the credit limit, reminder and counter code settings",
    "shop.edit": "Change the shop's name, language and usual due date",
    "staff.manage": "Invite, suspend and remove sellers",
    "activity.view": "View the activity log",
    "membership.own": "See your own permissions (every staff member has it)",
    "permissions.manage": "Manage staff permissions",
    "ownership.transfer": "Transfer the shop to another owner",
    "ownership.receive": "Accept ownership (when it is offered to a manager)",
    "subscription.manage": "Subscription and payments",
    "support.manage": "See and close support access to the shop",
    "shop.delete": "Delete the shop",
}
