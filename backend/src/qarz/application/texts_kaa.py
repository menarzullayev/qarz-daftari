"""Karakalpak texts of the chat, the exports, the API's refusals and the permission names.

Written by a model and not yet reviewed by a native speaker (docs/10-operations/translation-review.md).
A key that is absent here reads Uzbek at run time.
"""

CHAT: dict[str, str] = {
    "welcome_new": (
        "Assalawma áleykum! Qarz Daftari — dúkandaǵı nesiye esabı.\n"
        "Baslaw ushın dúkan ashıń. Xızmetker bolsańız, dúkan iyesi jibergen silteme arqalı kiriń."
    ),
    "welcome_staff": (
        "Aktiv dúkan: {shop}\n\n"
        "Nesiye jazıw: Ali 45000\n"
        "Tólem jazıw: Ali -20000 yamasa Ali 20000 berdi\n"
        "Túsindirme qosıw: Ali 45000 non, sut"
    ),
    "help": (
        "Nesiye jazıw: Ali 45000\n"
        "Tólem jazıw: Ali -20000 yamasa Ali 20000 berdi\n"
        "Summanı 45 000, 45.000 yamasa 45k kórinisinde jazıw múmkin.\n\n"
        "/dokon — aktiv dúkandı almastırıw\n"
        "/til — tildi almastırıw\n"
        "/yordam — usı járdem"
    ),
    "soon": "Bul buyrıq ele tayın emes.",
    "ombor_low": "📦 {shop}: az qalǵan tovarlar",
    "ombor_line": "• {name}: {qty} {unit} (shegara {low})",
    "ombor_more": "…hám jáne basqaları. Tolıq dizim qosımshadaǵı «Sklad» bóliminde.",
    "ombor_none": "📦 {shop}: az qalǵan tovar joq.",
    "cash_help": "/kassa — búgingi kassa: kiris, shıǵıs hám qaldıq",
    "cash_today": "💰 {shop}\nKassa, {date}",
    "cash_line": "{method}: kiris {income}, shıǵıs {expense}, qaldıq {closing}",
    "cash_total": "Jámi: kiris {income}, shıǵıs {expense}, qaldıq {closing}",
    "cash_method_cash": "Naq",
    "cash_method_card": "Karta",
    "cash_method_transfer": "Ótkerme",
    "cash_forbidden": "Kassanı kóriw ushın sizde ruqsat joq. Dúkan iyesine múrájat etiń.",
    "only_text": "Házirshe tek tekstli xabarlardı túsinemen. Úlgi: Ali 45000",
    "open_shop": "🏪 Dúkan ashıw",
    "new_shop": "➕ Jańa dúkan",
    "ask_shop_name": "Dúkanıńızdıń atın jazıń (80 belgige shekem).",
    "shop_name_invalid": "Dúkan atı 1–80 belgi aralıǵında bolıwı kerek. Qaytadan jazıń.",
    "shop_created_limited": (
        "✅ «{shop}» dúkanı ashıldı.\n\nBiypul sınaq múddeti tek birinshi dúkanǵa beriledi. "
        "Bul dúkanda nesiye jazıw ushın jazılıw tólemin isleń."
    ),
    "shop_created_free": (
        "✅ «{shop}» dúkanı ashıldı.\n\nBiypul sınaq múddeti tek birinshi dúkanǵa beriledi. Bul dúkan "
        "biypul tarifte isleydi: nesiye jazıwıńız múmkin, mısalı: Ali 45000. Tarif haqqında: /obuna"
    ),
    "shop_created": "✅ «{shop}» dúkanı ashıldı.\n\nEndi nesiye jazıwıńız múmkin, mısalı: Ali 45000",
    "lang_prompt": "Tildi tańlań:",
    "lang_set": "Til ózgertildi: qaraqalpaqsha.",
    "no_shops": "Siz ele hesh bir dúkanǵa aǵza emessiz.",
    "choose_shop": "Qaysı dúkan menen isleysiz?",
    "shop_switched": "Aktiv dúkan: {shop}",
    "joined_shop": "✅ Siz «{shop}» dúkanına qosıldıńız.\n\nNesiye jazıw: Ali 45000",
    "invitation_invalid": "Bul mirát siltemesi jaramsız yamasa múddeti ótken. Dúkan iyesinen jańasın sorań.",
    "already_member": "Siz álleqashan usı dúkan xızmetkerisiz.",
    "credit_saved": "✅ {shop}\n{name}: +{amount}\nJámi qarızı: {balance}\nTólew múddeti: {date}",
    "payment_saved": "✅ {shop}\n{name}: tólem {amount}\nQalǵan qarızı: {balance}",
    "unknown_customer_credit": (
        "{shop}\n«{name}» degen qarıydar tabılmadı.\nJańa qarıydar qosıp, {amount} nesiye jazılsın ba?"
    ),
    "unknown_customer_payment": "{shop}\n«{name}» degen qarıydar tabılmadı. Tólem jazılmadı.",
    "pick_customer": "{shop}\n«{name}» — qaysı qarıydar? ({amount})",
    "add_and_record": "➕ Qosıw hám jazıw",
    "new_customer_option": "➕ Jańa qarıydar: {name}",
    "cancel": "Biykar",
    "cancelled": "Biykarlandı. Hesh nárse jazılmadı.",
    "expired": "Bul túyme eskirgen. Xabardı qaytadan jazıń.",
    "tomorrow": "Erteń",
    "end_of_week": "Hápte aqırı",
    "in_two_weeks": "2 hápte",
    "in_a_month": "1 ay",
    "other_date": "📅 Basqa sáne",
    "reverse": "↩️ Biykarlaw",
    "reverse_yes": "Awa, biykarlansın",
    "reverse_no": "Yaq",
    "ask_date": "Tólew sánesin jazıń: kún.ay, mısalı 25.10",
    "promise_closed": ("Bul jazbanıń múddeti álleqashan belgilengen. Onı endi menedjer yamasa dúkan iyesi ózgertedi."),
    "reversed": "↩️ {shop}\n{name}: {amount} jazbası biykarlandı.\nQarızı: {balance}",
    "forbidden": "Bul háreket ushın sizdiń rolińiz jetkiliksiz.",
    "forbidden_permission": "Bul háreket ushın sizde ruqsat joq. Dúkan iyesine múrájat etiń.",
    "not_found": "Jazba tabılmadı.",
    "parse_hint": "Túsinbedim. Úlgi:\nAli 45000\nAli -20000\nAli 20000 berdi",
    "parse_amount_not_whole": "Summa pútin swmda jazıladı, tiyınsız. Úlgi: Ali 45000",
    "parse_ambiguous": "Qaysı san summa ekenin ajırata almadım. Aldın atın, keyin bir summa jazıń: Ali 45000",
    "parse_too_long": "Xabar júdá uzın. Qısqaraq jazıń: Ali 45000 izoh",
    "amount_range": "Summa 100 swm – 100 000 000 swm aralıǵında bolıwı kerek.",
    # Only a shop that works in dollars is ever told these.
    "amount_range_usd": "Dollardaǵı summa 0.01 $ – 10 000 $ aralıǵında bolıwı kerek.",
    "parse_amount_too_precise": ("Dollar summasında noqattan keyin kóp degende eki cifr jazıladı. Úlgi: Ali 50.25$"),
    "parse_ambiguous_usd": (
        "Dollar summasın anıq túsine almadım. Bir summa hám bir valyuta jazıń: Ali 50$ yamasa Ali 1250.50$"
    ),
    "notice_amount_invalid_usd": (
        "Tek summanı jazıń. Swmda: 50000. Dollarda summadan keyin $ belgisin qoyıń: 50$ yamasa 50.25$"
    ),
    "two_amounts": "{first} hám {second}",
    "PROMISE_BEFORE_SALE": "Múddet sawda kúninen aldın bolıwı múmkin emes.",
    "PROMISE_TOO_FAR": "Múddet sawda kúninen kóp degende 365 kún keyin bolıwı múmkin.",
    "SUBSCRIPTION_LIMITED": "Jazılıw tamamlanǵan: jańa nesiye jazılmaydı. Tólem qabıllaw isley beredi. /obuna",
    "FREE_PLAN_FULL": (
        "Biypul tarif {limit} qarıydarǵa shekem óz ishine aladı, jańa qarıydar qosılmadı. "
        "Kóbirek qarıydar ushın jazılıwdı tóleń: /obuna"
    ),
    "SHOP_SUSPENDED": "Dúkan waqıtsha toqtatılǵan.",
    "EXCEEDS_BALANCE": "Tólem qarıydardıń qarızınan úlken bolıwı múmkin emes.",
    "CUSTOMER_ARCHIVED": "Bul qarıydar arxivte. Aldın arxivten shıǵarıń.",
    "ALREADY_REVERSED": "Bul jazba álleqashan biykarlanǵan.",
    "ENTRY_OF_DOCUMENT": (
        "Bul jazbanı tovar qaytarıw hújjeti jaratqan. Onı biykarlaw ushın qosımshadaǵı «Sklad» bóliminde "
        "hújjettiń ózin biykarlań."
    ),
    "CANNOT_REVERSE_REVERSAL": "Biykarlaw jazbasın biykarlap bolmaydı.",
    "WOULD_GO_NEGATIVE": "Biykarlansa qarız teris bolıp qaladı. Aldın keyingi tólemdi biykarlań.",
    "error": "Qátelik júz berdi. Qaytadan urınıp kóriń.",
    "TIMEOUT": "Júdá uzaq dawam etti hám toqtatıldı. Hesh nárse jazılmadı. Qaytadan urınıp kóriń.",
    "consent_v2": (
        "{shop} dúkanı sizdiń nesiye sawdalarıńızdı hám tólemlerińizdi Qarz Daftari xızmeti arqalı "
        "júrgizedi. Saqlanatuǵın maǵlıwmatlar: dúkan sizdi qalay ataǵanı, telefon nomerińiz (eger bergen "
        "bolsańız), Telegram akkauntıńız identifikatorı, nesiye hám tólem jazbaları, alınǵan ónimler. "
        "Maqset: qarız esabın siz de kórip turıwıńız hám eskertiwler jiberiw. Maǵlıwmatlar tek sizge "
        "hám usı dúkan xızmetkerlerine kórinedi, basqa dúkanlarǵa berilmeydi. Qálegen waqıtta /uzish arqalı "
        "ajıralıwıńız yamasa /ochirish arqalı maǵlıwmatlarıńızdı óshiriwdi sorawıńız múmkin. Razısız ba?"
    ),
    "consent_yes": "✅ Razıman",
    "consent_no": "Yaq",
    "consent_declined": "Razılıq berilmedi. Siz haqqıńızda hesh nárse saqlanbadı.",
    "linked": (
        "✅ Siz «{shop}» dúkanındaǵı esabıńızǵa jalǵandıńız. Endi hár bir jazba haqqında usı jerde xabar "
        "alasız.\n"
        "Qarızıńızdı kóriw: /qarzim"
    ),
    "waiting_ok": ("✅ Sorawıńız «{shop}» dúkanına jiberildi. Satıwshı sizdi dápterdegi jazbańızǵa jalǵaydı."),
    "waiting_full": (
        "«{shop}» dúkanında jalǵanıwdı kútip turǵanlar dizimi házir tolı. "
        "Satıwshıdan dizimdi kórip shıǵıwdı sorań yamasa keyinirek qayta urınıp kóriń."
    ),
    "link_invalid": "Bul silteme jaramsız yamasa múddeti ótken. Dúkannan jańasın sorań.",
    "link_taken": "Bul esap basqa Telegram akkauntına jalǵanǵan. Dúkanǵa múrájat etiń.",
    "link_already": "Siz bul dúkanǵa álleqashan jalǵanǵansız yamasa jalǵanıwdı kútip tursız. Qarızıńız: /qarzim",
    "accounts_header": "Sizdiń qarızlarıńız:",
    "account_line": "{shop}: {balance}",
    "no_accounts": ("Siz ele hesh bir dúkandaǵı esapqa jalǵanbaǵansız. Dúkannan silteme yamasa QR kod sorań."),
    "unlink_choose": "Qaysı dúkannan ajıralasız? Xabarlar toqtaydı; dúkan dápterindegi jazbalar qaladı.",
    "unlink_button": "Ajıralıw: {shop}",
    "unlinked": "Siz «{shop}» dúkanınan ajıraldıńız. Endi bul dúkannan xabar kelmeydi.",
    "n_credit": (
        "{shop}\n{name}, sizge nesiye jazıldı: {amount}\n{goods}Tólew múddeti: {date}\nJámi qarızıńız: {balance}"
    ),
    "n_payment": "{shop}\n{name}, tólemińiz qabıllandı: {amount}\nQalǵan qarızıńız: {balance}",
    "n_reversed_credit": "{shop}\n{name}, {amount} nesiye jazbası biykarlandı.\nJámi qarızıńız: {balance}",
    "n_reversed_payment": "{shop}\n{name}, {amount} tólem jazbası biykarlandı.\nJámi qarızıńız: {balance}",
    "n_promise": "{shop}\n{name}, {amount} nesiyeniń tólew múddeti: {date}\nJámi qarızıńız: {balance}",
    "n_line": "• {name} — {qty} {unit}: {total}",
    "n_more_lines": "… hám jáne {count} ónim",
    "removal_choose": "Qaysı dúkandaǵı maǵlıwmatlarıńız óshirilsin?",
    "removal_button": "Óshiriw: {shop}",
    "removal_confirm": (
        "«{shop}» dúkanındaǵı atıńız, telefon nomerińiz hám Telegram akkauntıńız benen baylanıs "
        "óshiriledi. Qarız hám tólem summaları dúkan dápterinde atsız qaladı. Qarızıńız bolsa, ol tolıq "
        "tólengende óshiriledi. Bunı artqa qaytarıp bolmaydı. Dawam etilsin be?"
    ),
    "removal_yes": "Awa, óshirilsin",
    "removal_done": "«{shop}» dúkanındaǵı maǵlıwmatlarıńız óshirildi.",
    "removal_waiting": (
        "Sorawıńız qabıllandı. «{shop}» dúkanındaǵı qarızıńız ({balance}) tolıq tólengennen keyin "
        "maǵlıwmatlarıńız óshiriledi."
    ),
    "dispute_button": "⚠️ Narazılıq bildiriw",
    "ask_dispute_reason": "Narazılıǵıńızdıń sebebin qısqasha jazıń (3–300 belgi).",
    "reason_invalid": "Sebep 3–300 belgi bolıwı kerek. Túymeni qayta basıp, jáne jazıń.",
    "dispute_sent": ("Narazılıǵıńız «{shop}» dúkanına jiberildi. Jazba kórip shıǵılǵanǵa shekem qarızıńızda turadı."),
    "DISPUTE_NOT_ALLOWED": ("Bul jazba boyınsha narazılıq bildirip bolmaydı yamasa ol álleqashan kórip shıǵılǵan."),
    "s_dispute": "⚠️ {shop}\n{name} {amount} jazbasına narazılıq bildirdi:\n«{reason}»",
    "decline_button": "Ret etiw",
    "ask_decline_reason": "Ret etiw sebebin jazıń. Ol qarıydarǵa jiberiledi.",
    "dispute_declined_staff": "Narazılıq ret etildi. Qarıydarǵa sebebi menen xabar jiberildi.",
    "n_dispute_declined": "{shop}\n{amount} jazbası boyınsha narazılıǵıńız ret etildi.\nSebep: {reason}",
    "s_dispute_withdrawn": "{shop}\n{name} {amount} jazbası boyınsha narazılıǵın qaytarıp aldı.",
    "r1_due_today": ("Assalawma áleykum, {name}! «{shop}» dúkanınan eskertiw: búgin {amount} tólew kúni. Raxmet!"),
    "r1_overdue": (
        "Assalawma áleykum, {name}! «{shop}» dúkanındaǵı {amount} qarızıńızdıń tólew múddeti ótken. "
        "Múmkinshilik tawıp tólep qoysańız, minnetdar bolamız."
    ),
    "r2_due_today": "«{shop}»: {name}, búgin {amount} tólew kúni.",
    "r2_overdue": "«{shop}»: {name}, {amount} qarızdıń tólew múddeti ótken. Iltimas, tólep qoyıń.",
    "r3_due_today": ("Húrmetli {name}, «{shop}» dúkanı sizdi qádirleydi. Búgin {amount} tólew kúni ekenin eskertemiz."),
    "r3_overdue": (
        "Húrmetli {name}, «{shop}» dúkanındaǵı {amount} qarızıńızdıń múddeti ótkenin eskertemiz. Qolaylı "
        "waqıtta tólep qoysańız, quwanıshlı bolamız."
    ),
    "shop_suspended": (
        "«{shop}» dúkanı xızmet administraciyası tárepinen toqtatıldı. Sebep: {reason}\n"
        "Endi tek dúkan iyesi maǵlıwmatlardı kóre aladı hám eksport ete aladı."
    ),
    "shop_unsuspended": "«{shop}» dúkanı jáne islep tur: toqtatıw biykarlandı. Túsindirme: {reason}",
    # To an account that may be in someone else's hands: the bare fact and nothing else.
    "owner_reassigned_old": "«{shop}» dúkanınıń iyeligi xızmet administraciyası tárepinen ózgertildi.",
    "owner_reassigned_new": (
        "Xızmet administraciyası sheshimine bola endi siz «{shop}» dúkanınıń iyesisiz. Dúkan panelde ashıladı."
    ),
    "owner_reassigned_new_deletion": (
        "Xızmet administraciyası sheshimine bola endi siz «{shop}» dúkanınıń iyesisiz. Dúkan panelde ashıladı.\n"
        "Dıqqat: dúkan óshiriwdi kútpekte, {due} kúni pútkilley óshiriledi. "
        "Bunı panelde biykarlawıńız múmkin."
    ),
    "support_opened": (
        "«{shop}»: xızmet administratorı járdem beriw ushın dúkan maǵlıwmatların kóriw ruqsatın ashtı. "
        "Sebep: {reason}\n"
        "Ruqsat {until} waqtına shekem isleydi hám ózi tamamlanadı. Ol tek kóriw ushın: hesh nárse "
        "ózgertilmeydi. Qálegen waqıtta panelde jabıwıńız múmkin."
    ),
    "support_closed": "«{shop}»: xızmet administratorı dúkan maǵlıwmatların kóriw ruqsatın japtı.",
    "LIMIT_REACHED": ("Bul sawda qarıydardıń nesiye limitinen asadı. Onı menedjer yamasa dúkan iyesi jaza aladı."),
    "USD_BALANCE_OPEN": (
        "Dollardı óshirip bolmaydı: qarıydarlarda dollarda qarız bar. Aldın dollardaǵı barlıq qarızlar jabılsın."
    ),
    "limit_warning": "⚠️ Qarız limitten astı: limit {limit}, qarız {balance}.",
    "sub_header": "«{shop}» — jazılıw",
    "sub_state_trial": "Sınaq múddeti: {date} kúnine shekem ({days} kún qaldı).",
    "sub_state_active": "Tólengen: {date} kúnine shekem ({days} kún qaldı).",
    "sub_state_limited": (
        "Múddet tamamlanǵan: jańa nesiye jazılmaydı. Tólem qabıllaw, kóriw hám qarıydarlarǵa xabarlar isley beredi."
    ),
    "sub_state_suspended": "Dúkan waqıtsha toqtatılǵan. Qollap-quwatlaw xızmetine múrájat etiń.",
    "sub_state_free": "Biypul tarif: múddeti joq, dúkan tolıq isleydi.",
    "sub_customers": "Qarıydarlar: {used}. Biypul tarif {limit} qarıydarǵa shekem óz ishine aladı.",
    "sub_then_free": ("Tólem islenbese, dúkan biypul tarifte tolıq isley beredi ({limit} qarıydarǵa shekem)."),
    "sub_then_limited": (
        "Tólem islenbese, jańa nesiye jazılmaydı: sizde {used} qarıydar bar, al biypul tarif {limit} qarıydarǵa shekem."
    ),
    "sub_quota_left": "SMS eskertiwler jazılıwǵa kiredi: usı ayda {left} qaldı (ayına {quota}).",
    "sub_paying_adds": "Tólem isleseńiz, qarıydarlar sanı sheklenbeydi.",
    "sub_paying_adds_quota": (
        "Tólem isleseńiz, qarıydarlar sanı sheklenbeydi hám ayına {quota} SMS eskertiwge shekem jiberiledi."
    ),
    "sub_price": "Bahası: ayına {price}.",
    "sub_pay_to": "Tólem ushın karta — {label}: {card}. Ótkermeden keyin chekti usı jerge jiberiń.",
    "sub_other_cards_button": "Basqa karta ({count})",
    "sub_choose_card": "Qaysı kartaǵa tóleysiz? Tańlań.",
    "sub_cards_back": "⬅️ Artqa",
    "receipt_card": "Karta: {card}",
    "sub_no_card": "Tólem rekvizitleri ele kiritilmegen.",
    "sub_paid_online": "«{shop}»: {amount} tólem qabıllandı. Jazılıw {date} kúnine shekem tólengen.",
    "sub_choose_months": "Tólemnen keyin neshe ay ushın tólegenińizdi tańlań hám chekti jiberiń.",
    "sub_months_button": "{months} ay — {amount}",
    "ask_sub_receipt": (
        "«{shop}»: {months} ay ushın {amount}. Endi chektiń súwretin yamasa PDF faylın usı jerge jiberiń."
    ),
    "sub_receipt_invalid": (
        "Bul fayldı qabıllay almadım. Chek JPEG, PNG yamasa WebP súwret yamasa PDF bolıwı hám 5 MB tan "
        "aspawı kerek. Qaytadan jiberiń yamasa biykarlań."
    ),
    "sub_receipt_sent": (
        "✅ «{shop}»: chek qabıllandı ({months} ay, {amount}). Administrator kórip shıqqannan keyin sizge "
        "xabar beremiz."
    ),
    "sub_receipt_approved": ("✅ «{shop}»: tólem tastıyıqlandı ({months} ay). Jazılıw {date} kúnine shekem tólengen."),
    "sub_receipt_rejected": "«{shop}»: {amount} tólem chegi ret etildi. Sebep: {reason}",
    "a_receipt_new": "Jańa jazılıw chegi: «{shop}», {amount}, {months} ay. Admin panelde kórip shıǵıń.",
    "a_receipt_copies": "⚠️ Tap usı fayl aldın da jiberilgen: {count} chekte.",
    "a_receipt_no_file": "⚠️ Chek faylın xabarǵa qosıp bolmadı. Onı admin panelde kóriń.",
    "receipt_approve_button": "✅ Tastıyıqlaw",
    "receipt_reject_button": "Ret etiw",
    "a_sign_in_first": ("Aldın admin panelge kirip, kodıńızdı tastıyıqlań. Sonnan keyin bul túymeler isleydi."),
    "a_receipt_approved": "✅ «{shop}»: chek tastıyıqlandı ({months} ay). Jazılıw {date} kúnine shekem tólengen.",
    "a_receipt_rejected": "«{shop}»: chek ret etildi. Sebep: {reason}",
    "a_receipt_decided": "Bul chek boyınsha sheshim álleqashan qabıllanǵan.",
    "a_receipt_use_panel": "Bul chekti admin panelde kórip shıǵıń: aylar sanın kiritiw kerek.",
    # Shown over the button to a Telegram administrator of the review group (at most 200 characters).
    "g_reason_in_private": (
        "Ret etiw sebebin botqa jeke xabarda jazıń. Bot penen ele jazıspaǵan bolsańız, "
        "aldın bottı ashıp «Start»tı basıń hám «Ret etiw»di qayta basıń."
    ),
    "g_receipt_needs_panel": ("Bul chekte aylar sanı kórsetilmegen. Onı platforma administratorı panelde sheshedi."),
    "ask_receipt_reject_reason": "Ret etiw sebebin jazıń (3–500 belgi). Ol dúkan iyesine jiberiledi.",
    "a_reason_invalid": "Sebep 3–500 belgi bolıwı kerek. Qaytadan jazıń yamasa biykarlań.",
    "sub_trial_ending": ("«{shop}»: sınaq múddeti {days} kúnnen keyin, {date} kúni tamamlanadı. Dawam ettiriw: /obuna"),
    "sub_paid_ending": ("«{shop}»: tólengen múddet {days} kúnnen keyin, {date} kúni tamamlanadı. Uzaytıw: /obuna"),
    "sub_limited": (
        "«{shop}»: jazılıw múddeti tamamlandı. Endi jańa nesiye jazılmaydı; tólem qabıllaw, kóriw hám "
        "qarıydarlarǵa xabarlar isley beredi. Tólew: /obuna"
    ),
    "sub_free_now": (
        "«{shop}»: múddet tamamlandı. Dúkan biypul tarifke ótti hám tolıq isley beredi: {limit} "
        "qarıydarǵa shekem. Kóbirek qarıydar kerek bolsa: /obuna"
    ),
    "move_date_button": "📅 Múddetti kóshiriw",
    "ask_move_date": "Tólew múddeti qaysı sánege kóshirilsin? kún.ay kórinisinde jazıń, mısalı 25.10",
    "move_date_invalid": "Sáneni túsinbedim. kún.ay kórinisinde jazıń, mısalı 25.10",
    "date_request_sent": (
        "Sorawıńız «{shop}» dúkanına jiberildi: tólew múddetin {date} kúnine kóshiriw. Juwabı usı jerge keledi."
    ),
    "REQUEST_ALREADY_OPEN": "Bul jazba boyınsha múddetti kóshiriw sorawı álleqashan kórip shıǵılmaqta.",
    "DATE_REQUEST_NOT_ALLOWED": "Bul jazba múddetin kóshiriw sorawın házir qabıllap bolmaydı.",
    "PROMISE_NOT_CHANGEABLE": "Bul jazbanıń tólew múddeti joq yamasa jazba biykarlanǵan.",
    "date_not_later": "Jańa sáne házirgi tólew múddetinen keyin bolıwı kerek.",
    "date_fully_paid": "Bul jazba tolıq tólengen yamasa biykarlanǵan: múddetin kóshiriwge hájet joq.",
    "date_declined_recently": (
        "Bul jazba boyınsha sorawıńız jaqında ret etilgen. Ret etilgennen 7 kún ótkennen keyin qayta soraw múmkin."
    ),
    "date_request_closed": "Bul soraw álleqashan kórip shıǵılǵan yamasa óz kúshin joǵaltqan.",
    "s_date_request": (
        "📅 {shop}\n{name} {amount} nesiyeniń tólew múddetin {old} kúninen {date} kúnine kóshiriwdi sorap atır."
    ),
    "accept_button": "✅ Qabıllaw",
    "reason_line": "Sebep: {reason}",
    "date_accepted_staff": "✅ {name}: {amount} nesiyeniń tólew múddeti {date} kúnine kóshirildi.",
    "date_declined_staff": "Soraw ret etildi: {name}, {amount}. Tólew múddeti ózgermedi.",
    "n_date_accepted": ("{shop}\n{amount} nesiye múddetin kóshiriw sorawıńız qabıllandı.\nJańa tólew múddeti: {date}"),
    "n_date_declined": "{shop}\n{amount} nesiye múddetin {date} kúnine kóshiriw sorawıńız ret etildi.",
    "n_date_changed": "{shop}\n{name}, {amount} nesiyeniń tólew múddeti ózgertildi: {old} → {date}",
    "n_opening": (
        "{shop}\n{name}, dápterge aldıńǵı qarızıńız kiritildi: {amount}\n"
        "Tólew múddeti: {date}\nJámi qarızıńız: {balance}"
    ),
    "s_import_applied": (
        "📥 {shop}\nImport qollanıldı: {entries} qarız jazbası, jámi {amount}. Jańa qarıydarlar: {customers}.\n"
        "24 saat ishinde pútkilley biykarlaw múmkin."
    ),
    "s_import_undone": ("↩️ {shop}\nImport biykarlandı: {entries} jazba qaytarıldı. Import summası: {amount}."),
    "import_checked": (
        "📥 {shop}\nImport faylı tekserildi: {rows} qatar, qátesiz. Qosımshada kórip shıǵıp, qollanıwıńız múmkin."
    ),
    "import_rejected": (
        "📥 {shop}\nImport faylınıń {errors} qatarında qáte tabıldı. Qosımshada dizimin kórip, "
        "dúzetilgen fayldı qayta júkleń."
    ),
    "import_unreadable": ("📥 {shop}\nImport faylın keste sıpatında oqıp bolmadı. Sebebi qosımshada kórsetilgen."),
    "import_refused": (
        "📥 {shop}\nImport qollanılmadı: tekseriwden keyin maǵlıwmatlar ózgergen. Qosımshada qayta kórip shıǵıń."
    ),
    "import_refused_free_plan": (
        "📥 {shop}\nImport qollanılmadı: biypul tarif {limit} qarıydarǵa shekem óz ishine aladı. "
        "Kóbirek qarıydar ushın jazılıwdı tóleń: /obuna"
    ),
    "import_undo_refused": (
        "↩️ {shop}\nImporttı biykarlap bolmadı: onıń jazbalarına tólem islengen. Hesh nárse ózgermedi."
    ),
    "import_failed": (
        "📥 {shop}\nImport boyınsha soralǵan háreket orınlanbadı. Hesh nárse ózgermedi; qayta urınıp kóriń."
    ),
    "shop_deletion_requested": (
        "«{shop}» dúkanın óshiriw soraldı. Maǵlıwmatlar {date} kúni pútkilley óshiriledi. Usı kúnge shekem "
        "eksport etiwińiz yamasa biykarlawıńız múmkin."
    ),
    "shop_deletion_cancelled": "«{shop}» dúkanın óshiriw biykarlandı. Dúkan aldıńǵıday isleydi.",
    "shop_erased": "«{shop}» dúkanı hám onıń barlıq maǵlıwmatları óshirildi.",
    # --- payment notices (REQ-060, REQ-061) ---
    "notice_choose_shop": "Qaysı dúkanǵa tólem islegenińizdi bildiresiz?",
    "notice_shop_button": "{shop}: {balance}",
    "notice_nothing_owed": "«{shop}» dúkanında qarızıńız joq.",
    "ask_notice_amount": (
        "«{shop}» dúkanındaǵı qarızıńız: {balance}\nQansha tóledińiz? Tek summanı jazıń, mısalı: 50000"
    ),
    "notice_amount_invalid": (
        "Tek summanı jazıń, mısalı: 50000 yamasa 50 000. Summa pútin swmda, 100 swm – 100 000 000 swm aralıǵında."
    ),
    "notice_amount_exceeds": "Summa qarızıńızdan úlken bolıwı múmkin emes. Qarızıńız: {balance}",
    "ask_notice_receipt": (
        "Tólem: {amount}\nChegińiz bolsa, onıń súwretin yamasa PDF faylın usı jerge jiberiń. "
        "Chek bolmasa, túymeni basıń."
    ),
    "notice_without_receipt": "Cheksiz jiberiw",
    "notice_receipt_hint": "Chek súwretin yamasa PDF faylın jiberiń yamasa túymelerdiń birewin basıń.",
    "notice_receipt_invalid": (
        "Bul fayldı qabıllay almadım. Chek JPEG, PNG yamasa WebP súwret yamasa PDF bolıwı hám 5 MB tan "
        "aspawı kerek. Qaytadan jiberiń yamasa túymelerdiń birewin basıń."
    ),
    "notice_sent": (
        "✅ {amount} tólem haqqındaǵı xabarıńız «{shop}» dúkanına jiberildi. Dúkan qabıllaǵannan keyin "
        "qarızıńız azayadı."
    ),
    "s_notice": "💵 {shop}\n{name} {amount} tólegenin bildirdi.\nHázirgi qarızı: {balance}",
    "s_notice_receipt": (
        "💵 {shop}\n{name} {amount} tólegenin bildirdi.\nHázirgi qarızı: {balance}\n"
        "📎 Chek qosa berilgen: onı qosımshada kóriwińiz múmkin."
    ),
    "s_receipt_seen_before": "⚠️ Tap usı chek bul dúkanǵa aldın da jiberilgen.",
    "notice_accept_button": "✅ Qabıllaw",
    "notice_accepted_staff": "✅ {shop}\n{name}: tólem {amount} qabıllandı.\nQalǵan qarızı: {balance}",
    "notice_declined_staff": "Tólem xabarı ret etildi. Qarıydarǵa sebebi menen xabar jiberildi.",
    "n_notice_accepted": "{shop}\n{amount} tólem haqqındaǵı xabarıńız qabıllandı.",
    "n_notice_corrected": (
        "{shop}\n{amount} tólem haqqındaǵı xabarıńız qabıllandı, biraq tólem {recorded} dep jazıldı."
    ),
    "n_notice_declined": "{shop}\n{amount} tólem haqqındaǵı xabarıńız ret etildi.\nSebep: {reason}",
    "PAYMENT_NOTICE_NOT_ALLOWED": (
        "Kórip shıǵılmaǵan tólem xabarlarıńız júdá kóp. Aldın dúkan olarǵa juwap beriwin kútiń."
    ),
    "PAYMENT_NOTICE_NOT_OPEN": "Bul tólem xabarı álleqashan kórip shıǵılǵan yamasa múddeti ótken.",
    "FILE_STORE_UNAVAILABLE": "Fayllardı saqlaw házir islemey tur. Birazdan keyin qayta urınıp kóriń.",
    "export_ready": (
        "✅ «{shop}» dúkanınıń eksportı tayın. Onı qosımshanıń eksport bóliminen júklep alıń; fayl 7 kún saqlanadı."
    ),
    "export_failed": "«{shop}» dúkanınıń eksportın tayarlap bolmadı. Birazdan keyin qaytadan sorań.",
    "EXPORT_NOT_ALLOWED": "Eksport álleqashan tayarlanbaqta yamasa búgingi eksportlar sanı tawsılǵan.",
    "EXPORT_NOT_READY": "Bul eksport faylı ele tayın emes yamasa múddeti ótken.",
    "SUBSCRIPTION_RECEIPT_NOT_ALLOWED": ("Kórip shıǵılmaǵan cheklerińiz júdá kóp. Administrator juwabın kútiń."),
    "currency": "swm",
    # The operations alerts (DEC-078).
    "ops_title": "⚠️ Qarz Daftari: sistema qadaǵalawı",
    "ops_firing": "🔴 Baslandı:",
    "ops_reminder": "🟠 Ele de dawam etpekte:",
    "ops_resolved": "🟢 Dúzeldi:",
    "ops_line": "• {name}: {text}{value} ({since} waqtınan berli)",
    "ops_line_resolved": "• {name}: {text} ({since} — {until})",
    "ops_more": "…jáne {count} eskertiw keyingi xabarda.",
    "ops_footer": "Ne islew kerek: runbook 16 (docs/10-operations/runbooks.md).",
    "ops_test": (
        "✅ Qarz Daftari: SÍNAQ eskertiwi ({at}).\n"
        "Bul haqıyqıy nasazlıq emes. Sistema qadaǵalawı xabarları usı chatqa jetip keliwin tekseriw ushın jiberildi."
    ),
    "ops_db_down": (
        "🔴 Qarz Daftari: jumısshı process maǵlıwmatlar bazasına jalǵana almay atır ({since} waqtınan berli).\n"
        "Qadaǵalaw jaǵdayı saqlanbay atır, basqa tekseriwler toqtaǵan. Runbook 16."
    ),
    "ops_db_up": "🟢 Qarz Daftari: maǵlıwmatlar bazası jáne islep tur ({since} — {until}).",
    "ops_rule_ErrorRateHigh": "sorawlardıń 2% ten kóbi server qátesi menen tamamlanıp atır",
    "ops_rule_OutboxOld": "xabarlar jiberilmey atır: gezektegi xabar 10 minuttan kóp kútpekte",
    "ops_rule_RemindersNotRunning": "eskertiwler jumısı bir saattan berli tamamlanbaǵan",
    "ops_rule_SmsRefused": "SMS provayder tárepinen ret etildi yamasa bir kúnnen keyin taslap jiberildi",
    "ops_rule_SmsNotGoingOut": "SMS gezekte tur, provayder qabıllamay atır",
    "ops_rule_ReceiptsWaiting": "jazılıw chegi bir kúnnen artıq sheshim kútpekte",
    "ops_rule_CrossTenantAttempt": "sistemaǵa kirgen paydalanıwshı ózine tiyisli bolmaǵan dúkandı soradı",
    "ops_rule_InvalidSignaturesRepeated": "nadurıs qoltańba menen tákirar urınıwlar",
    "ops_rule_AdminSecondFactorRepeated": "administratordıń ekinshi faktor kodı qayta-qayta ret etildi",
    "ops_rule_SupportAccessOpened": "administrator dúkanǵa járdem ushın kiriw ashtı",
    "ops_rule_AdminWithoutSupportAccess": "administrator járdem kiriwisiz dúkan maǵlıwmatın soradı",
    "ops_rule_ShopOwnerReassigned": "administrator dúkan iyesin almastırdı",
    "ops_rule_MetricsMissing": "API kórsetkishlerin oqıp bolmay atır",
    "ops_rule_BackupFailed": "sońǵı rezerv nusqa alıw sátsiz tamamlandı",
    "ops_rule_BackupMissing": ("saqlaǵıshta jańa rezerv nusqa joq (26 saatta birewi de, yamasa 8 kúnde tolıq nusqa)"),
    "ops_rule_WalArchiveStale": "WAL arxivi 5 minuttan eski yamasa tekseriwdiń ózi islemey atır",
    "ops_rule_RestoreTestNotPassed": "tiklew sınaǵı 8 kún ishinde ótpegen yamasa hesh qashan ótpegen",
    "ops_rule_RestoreTestFailed": "sońǵı tiklew sınaǵı sátsiz tamamlandı",
    "ops_rule_FilesCopyStale": "saqlanǵan fayllar saqlaǵıshqa nusqalanbay atır",
    "ops_rule_DiskAlmostFull": "disk 80% ten kóp tolǵan",
    "ops_rule_JobNotRunning": "rejeli jumıs óz dáwirin tamamlamaǵan",
    "ops_rule_LedgerMismatch": "saqlanǵan ashıq qarızlar dápter jazbalarınan parıq qılmaqta",
    "ops_rule_StockMismatch": "skladtaǵı saqlanǵan qaldıq yamasa támiyinlewshige qarız óz jazbalarınan parıq qılmaqta",
    "ops_rule_ApiDown": "API /healthz sorawına juwap bermey atır",
    "ops_rule_TelegramRefusesBot": "Telegram bot tokenin ret etpekte",
    "ops_rule_TelegramUnreachable": "Telegram menen baylanıs joq",
    "ops_rule_DispatcherFailing": "xabar jiberiwshi 5 minuttan berli hesh bir aylanıstı tamamlamaǵan",
}

EXPORT: dict[str, str | tuple[str, ...]] = {
    "sheet_summary": "Esabat",
    "sheet_customers": "Qarıydarlar",
    "sheet_ledger": "Dápter",
    "sheet_promises": "Múddetler tariyxı",
    "sheet_goods": "Ónimler",
    # Only in the workbook of a shop that has a cash book.
    "sheet_cash": "Kassa",
    "cash": (
        "Kún",
        "Jazılǵan waqıt",
        "Baǵıt",
        "Tólem usılı",
        "Valyuta",
        "Summa",
        "Kategoriya",
        "Túsindirme",
        "Biykarlanǵan ba",
        "Biykarlaw sebebi",
        "Kim jazǵan",
        "Xızmetker ID",
        "Dápterdegi tólem (ID)",
        "Jazba ID",
    ),
    "cash_income": "Kiris",
    "cash_expense": "Shıǵıs",
    "cash_cash": "Naq",
    "cash_card": "Karta",
    "cash_transfer": "Ótkerme",
    # An export of one period of the cash book (`qarz.application.cash_export`).
    "cash_period": (
        "Kún",
        "Jazılǵan waqıt",
        "Baǵıt",
        "Tólem usılı",
        "Valyuta",
        "Summa",
        "Kategoriya",
        "Túsindirme",
        "Derek",
        "Biykarlanǵan ba",
        "Biykarlaw sebebi",
        "Biykarlanǵan waqıt",
        "Xızmetker ID",
        "Jazba ID",
    ),
    "cash_customer": "Qarıydar",
    "cash_source_manual": "Qolda jazılǵan",
    "cash_source_ledger": "Qarıydar tólemi",
    "cash_source_stock": "Sklad",
    "cash_reversed": "Qarıydardıń tólemi biykarlandı",
    "cash_period_from": "Dáwir bası",
    "cash_period_to": "Dáwir aqırı",
    "cash_entries_count": "Jazbalar sanı (biykarlanǵanları menen)",
    "cash_balances_title": "Qaldıqlar",
    "cash_balances": (
        "Valyuta",
        "Tólem usılı",
        "Dáwir basında",
        "Kiris",
        "Shıǵıs",
        "Dáwir aqırında",
        "Jazbalar",
    ),
    "cash_all_methods": "Jámi",
    "cash_categories_title": "Kategoriyalar boyınsha",
    "cash_categories": (
        "Valyuta",
        "Baǵıt",
        "Kategoriya",
        "Summa",
        "Jazbalar",
    ),
    "cash_summary_note": (
        "Biykarlanǵan jazbalar esapqa kirmeydi. Swm hám dollar bólek esaplanadı: olar hesh qashan qosılmaydı."
    ),
    "customers": ("Qarıydar", "Telefon", "Jaǵdayı", "Nesiye limiti", "Qarızı", "Qosılǵan sáne", "Qarıydar ID"),
    "ledger": (
        "Sáne hám waqıt",
        "Qarıydar",
        "Túri",
        "Summa",
        "Qarızǵa tásiri",
        "Túsindirme",
        "Tólew múddeti",
        "Biykarlanǵan ba",
        "Biykarlaǵan jazbası (ID)",
        "Kim jazǵan",
        "Xızmetker ID",
        "Qarıydardaǵı tártip nomeri",
        "Jazba ID",
        "Qarıydar ID",
    ),
    "promises": ("Jazba ID", "Qarıydar", "Tólew múddeti", "Belgilengen waqıt", "Kim belgilegen", "Sebep"),
    "goods": ("Jazba ID", "Sáne hám waqıt", "Qarıydar", "Qatar", "Ónim", "Muǵdarı", "Birlik", "Bahası", "Jámi"),
    "months": (
        "Ay",
        "Nesiye summası",
        "Nesiye sanı",
        "Baslanǵısh qarız summası",
        "Tólemler summası",
        "Tólemler sanı",
        "Biykarlanǵan jazbalar sanı",
    ),
    "summary_shop": "Dúkan",
    "summary_made": "Eksport waqtı (Tashkent)",
    "summary_customers": "Qarıydarlar sanı",
    "summary_debtors": "Qarızdar qarıydarlar sanı",
    "summary_outstanding": "Jámi qarız",
    "currency": "Valyuta",
    "limit_usd": "Nesiye limiti ($)",
    "owed_usd": "Qarızı ($)",
    "summary_debtors_usd": "Dollarda qarızdar qarıydarlar sanı",
    "summary_outstanding_usd": "Jámi qarız ($)",
    "summary_months_usd": ("Aylar boyınsha, dollarda (biykarlanǵan jazbalar hám biykarlaw jazbaları esapqa alınbaǵan)"),
    "summary_entries": "Dápterdegi jazbalar sanı",
    "summary_months": "Aylar boyınsha (biykarlanǵan jazbalar hám biykarlaw jazbaları esapqa alınbaǵan)",
    "kind_credit": "Nesiye",
    "kind_opening": "Baslanǵısh qarız",
    "kind_payment": "Tólem",
    "kind_reversal": "Biykarlaw",
    "status_active": "Aktiv",
    "status_archived": "Arxivte",
    "status_anonymized": "Maǵlıwmatları óshirilgen",
    "role_seller": "Satıwshı",
    "role_manager": "Menedjer",
    "role_owner": "Dúkan iyesi",
    "actor_default": "Dúkan boyınsha ádettegi múddet",
    "actor_staff": "Xızmetker",
    "actor_customer_request": "Qarıydar sorawı menen",
    "yes": "Awa",
    "no": "Yaq",
    # Only in the workbook of a shop that keeps stock.
    "sheet_stock": "Sklad",
    "sheet_stock_movements": "Sklad háreketleri",
    "sheet_stock_documents": "Sklad hújjetleri",
    "sheet_suppliers": "Támiyinlewshiler",
    "sheet_supplier_entries": "Támiyinlewshi esabı",
    "stock": (
        "Tovar",
        "Birlik",
        "Satıw bahası",
        "Qaldıq",
        "Az qaldıq shegarası",
        "Ózine túser baha valyutası",
        "Ortasha ózine túser baha",
        "Qaldıq qunı (ózine túser bahada)",
        "Shtrix-kodlar",
        "Tovar ID",
    ),
    "stock_movements": (
        "Sáne hám waqıt",
        "Tovar",
        "Túri",
        "Muǵdarı",
        "Birlik",
        "Háreketten keyingi qaldıq",
        "Ózine túser baha (jámi)",
        "Valyuta",
        "Satıw summası",
        "Sebep",
        "Hújjet",
        "Biykarlanǵan ba",
        "Háreket ID",
        "Tovar ID",
    ),
    "stock_documents": (
        "Nomer",
        "Túri",
        "Jaǵdayı",
        "Sáne",
        "Támiyinlewshi",
        "Qarıydar",
        "Valyuta",
        "Jámi",
        "Tólengen",
        "Sebep",
        "Túsindirme",
        "Biykarlaw sebebi",
        "Hújjet ID",
    ),
    "suppliers": (
        "Támiyinlewshi",
        "Telefon",
        "Túsindirme",
        "Jaǵdayı",
        "Qarızımız (swm)",
        "Qarızımız ($)",
        "Támiyinlewshi ID",
    ),
    "supplier_entries": (
        "Sáne hám waqıt",
        "Támiyinlewshi",
        "Túri",
        "Summa",
        "Valyuta",
        "Qarızǵa tásiri",
        "Túsindirme",
        "Biykarlanǵan ba",
        "Hújjet",
        "Jazba ID",
        "Támiyinlewshi ID",
    ),
    "movement_receipt": "Kiris",
    "movement_sale": "Satıw",
    "movement_customer_return": "Qarıydardan qayttı",
    "movement_supplier_return": "Támiyinlewshige qaytarıldı",
    "movement_write_off": "Esaptan shıǵarıldı",
    "movement_correction": "Inventarizaciya dúzetiwi",
    "movement_reversal": "Biykarlaw",
    "document_receipt": "Kiris",
    "document_customer_return": "Qarıydardan qaytarıw",
    "document_supplier_return": "Támiyinlewshige qaytarıw",
    "document_write_off": "Esaptan shıǵarıw",
    "document_stocktake": "Inventarizaciya",
    "document_draft": "Qaralama",
    "document_posted": "Ótkerilgen",
    "document_cancelled": "Biykarlanǵan",
    "reason_damaged": "Zaqımlanǵan",
    "reason_expired": "Múddeti ótken",
    "reason_lost": "Joǵalǵan",
    "reason_own_use": "Óz mútájligine",
    "supplier_purchase": "Tovar alındı",
    "supplier_opening": "Baslanǵısh qarız",
    "supplier_payment": "Tólem",
    "supplier_return": "Tovar qaytarıldı",
    "supplier_reversal": "Biykarlaw",
}

ERRORS: dict[str, str] = {
    "UNAUTHENTICATED": "Aldın sistemaǵa kiriń.",
    "NOT_FOUND": "Tabılmadı.",
    "FORBIDDEN_ROLE": "Bul háreket ushın sizdiń rolińiz jetkiliksiz.",
    "FORBIDDEN_PERMISSION": "Bul háreket ushın sizde ruqsat joq. Dúkan iyesine múrájat etiń.",
    "BEYOND_OWN_PERMISSIONS": "Bul sizdiń ruqsatlarıńızdan joqarı: bunı tek dúkan iyesi isley aladı.",
    "VALIDATION": "Maǵlıwmatlar nadurıs kiritilgen.",
    "IDEMPOTENCY_KEY_REUSED": "Bul soraw gilti basqa háreket ushın paydalanılǵan.",
    "ALREADY_MEMBER": "Siz álleqashan usı dúkan xızmetkerisiz.",
    "OWNER_MEMBERSHIP_FIXED": "Dúkan iyesiniń aǵzalıǵı tek iyelikti ótkeriw arqalı ózgeredi.",
    "TRANSFER_PENDING": "Iyelikti ótkeriw usınısı álleqashan juwap kútpekte.",
    "TRANSFER_TARGET_INVALID": "Iyelikti tek usı dúkannıń aktiv menedjerine ótkeriw múmkin.",
    "NOT_TRANSFER_TARGET": "Bul usınısqa tek usınıs alǵan menedjer juwap bere aladı.",
    "SUBSCRIPTION_LIMITED": ("Jazılıw tamamlanǵan: jańa nesiye jazılmaydı. Tólem qabıllaw hám kóriw isley beredi."),
    "FREE_PLAN_FULL": (
        "Biypul tarif {limit} qarıydarǵa shekem óz ishine aladı, jańa qarıydar qosılmadı. "
        "Kóbirek qarıydar ushın jazılıwdı tóleń: botta /obuna yamasa «Jazılıw» beti."
    ),
    "SHOP_SUSPENDED": ("Dúkan toqtatılǵan. Tek dúkan iyesi maǵlıwmatlardı kóre aladı hám eksport ete aladı."),
    "CUSTOMER_ARCHIVED": "Bul qarıydar arxivte. Aldın arxivten shıǵarıń.",
    "CUSTOMER_HAS_BALANCE": "Qarızı bar qarıydardı arxivlep bolmaydı.",
    "USD_BALANCE_OPEN": (
        "Dollardı óshirip bolmaydı: qarıydarlarda dollarda qarız bar. Aldın dollardaǵı barlıq qarızlar jabılsın."
    ),
    "USD_SUPPLIER_BALANCE_OPEN": (
        "Dollardı óshirip bolmaydı: támiyinlewshiler menen dollarda esap-kitap jabılmaǵan. "
        "Aldın támiyinlewshiler menen dollardaǵı esap nolge keltirilsin."
    ),
    "USD_STOCK_OPEN": (
        "Dollardı óshirip bolmaydı: skladta ózine túser bahası dollarda júrgizilgen tovar bar. "
        "Aldın bul tovarlar satılsın, qaytarılsın yamasa esaptan shıǵarılsın."
    ),
    "EXCEEDS_BALANCE": "Tólem qarıydardıń qarızınan úlken bolıwı múmkin emes.",
    "ALREADY_REVERSED": "Bul jazba álleqashan biykarlanǵan.",
    "CANNOT_REVERSE_REVERSAL": "Biykarlaw jazbasın biykarlap bolmaydı.",
    "WOULD_GO_NEGATIVE": "Biykarlansa qarız teris bolıp qaladı. Aldın keyingi tólemdi biykarlań.",
    "PROMISE_ALREADY_SET": "Múddet álleqashan belgilengen. Endi onı menedjer yamasa dúkan iyesi ózgertedi.",
    "CATALOG_NAME_TAKEN": "Katalogta usı atlı ónim bar (jasırılǵan bolıwı da múmkin).",
    "CATALOG_ITEM_NOT_LEARNED": "Bul ónim álleqashan kórip shıǵılǵan.",
    "CATALOG_MERGE_TARGET_INVALID": "Tek katalogta kórinetuǵın, kórip shıǵılǵan ónimge birlestiriledi.",
    "CUSTOMER_ALREADY_LINKED": "Bul qarıydar álleqashan Telegram akkauntına jalǵanǵan.",
    "DISPUTE_NOT_ALLOWED": ("Bul jazba boyınsha narazılıq bildirip bolmaydı yamasa ol álleqashan kórip shıǵılǵan."),
    "REQUEST_ALREADY_OPEN": "Bul jazba boyınsha múddetti kóshiriw sorawı álleqashan kórip shıǵılmaqta.",
    "DATE_REQUEST_NOT_ALLOWED": "Bul jazba múddetin kóshiriw sorawın házir qabıllap bolmaydı.",
    "PROMISE_NOT_CHANGEABLE": "Bul jazbanıń tólew múddeti joq yamasa jazba biykarlanǵan.",
    "LINES_ALREADY_ADDED": "Bul jazbaǵa ónimler álleqashan qosılǵan.",
    "LINES_SUM_MISMATCH": "Ónimler jıyındısı jazba summasına teń emes.",
    "LINES_WINDOW_CLOSED": ("Ónim qosıw múddeti ótken: bul tek sawdadan keyingi kúnniń aqırına shekem múmkin."),
    "REMINDERS_OFF": "Eskertiwler dúkan yamasa usı qarıydar ushın óshirilgen.",
    "REMINDER_NOT_DUE": "Bul qarıydarda múddeti ótken yamasa búgin tólenetuǵın qarız joq.",
    "REMINDER_LIMIT_REACHED": "Bul qarıydarǵa búgin eskertiw álleqashan jiberilgen. Kúnine birew múmkin.",
    "CUSTOMER_UNREACHABLE": (
        "Bul qarıydarǵa xabar jetpeydi: Telegram jalǵanbaǵan, al SMS óshirilgen yamasa nomer joq."
    ),
    "LIMIT_REACHED": "Bul sawda qarıydardıń nesiye limitinen asadı. Menedjer yamasa dúkan iyesi jaza aladı.",
    "DELETION_ALREADY_REQUESTED": "Dúkandı óshiriw álleqashan soralǵan.",
    "DELETION_NOT_REQUESTED": "Dúkandı óshiriw soralmaǵan.",
    "SECOND_FACTOR_INVALID": "Kod nadurıs, eskirgen yamasa álleqashan paydalanılǵan. Jańa kodtı kiritiń.",
    "SECOND_FACTOR_LOCKED": "Júdá kóp nadurıs kod kiritildi. Birazdan keyin qayta urınıp kóriń.",
    "ADMIN_ALREADY_ENROLLED": ("Ekinshi faktor álleqashan jalǵanǵan. Almastırıw ushın operatorǵa múrájat etiń."),
    "ADMIN_NOT_ENROLLED": "Aldın ekinshi faktordı (autentifikator qosımshasın) jalǵań.",
    "SUBSCRIPTION_CHANGE_REFUSED": "Jazılıwdıń házirgi jaǵdayında bul ózgeristi islep bolmaydı.",
    "OWNER_REASSIGNMENT_REFUSED": "Dúkandı bul adamǵa berip bolmaydı.",
    "SUPPORT_ACCESS_REQUIRED": "Dúkan maǵlıwmatların kóriw ushın aldın sebep kórsetip ruqsat ashıń.",
    "SUPPORT_ACCESS_ALREADY_OPEN": "Bul dúkan ushın ashıq ruqsatıńız álleqashan bar.",
    "SUPPORT_ACCESS_NOT_OPEN": "Ashıq ruqsat joq: ol tamamlanǵan yamasa álleqashan jabılǵan.",
    "PAYMENT_NOTICE_NOT_ALLOWED": ("Kórip shıǵılmaǵan tólem xabarlarıńız júdá kóp. Dúkan juwabın kútiń."),
    "PAYMENT_NOTICE_NOT_OPEN": "Bul tólem xabarı álleqashan kórip shıǵılǵan yamasa múddeti ótken.",
    "EXPORT_NOT_ALLOWED": "Eksport álleqashan tayarlanbaqta yamasa búgingi eksportlar sanı tawsılǵan.",
    "EXPORT_NOT_READY": "Bul eksport faylı ele tayın emes yamasa múddeti ótken.",
    "FILE_STORE_UNAVAILABLE": "Fayllardı saqlaw házir islemey tur. Birazdan keyin qayta urınıp kóriń.",
    "IMPORT_NOT_APPLICABLE": "Bul importtı házirgi jaǵdayında qollanıp bolmaydı. Kórip shıǵıwdı jańalań.",
    "IMPORT_UNDO_REFUSED": ("Bul importtı biykarlap bolmaydı: múddet ótken yamasa jazbalarǵa tólem islengen."),
    "SUBSCRIPTION_RECEIPT_NOT_ALLOWED": ("Kórip shıǵılmaǵan cheklerińiz júdá kóp. Administrator juwabın kútiń."),
    "RECEIPT_ALREADY_DECIDED": "Bul chek boyınsha sheshim álleqashan qabıllanǵan.",
    "CASH_ENTRY_OF_LEDGER": (
        "Bul jazba — qarıydardıń tólemi. Onı biykarlaw ushın qarıydar betinde sol tólemdi biykarlań."
    ),
    "CASH_ENTRY_OF_STOCK": (
        "Bul jazbanı sklad jazǵan (támiyinlewshige tólem, tovar satıp alıw yamasa qaytarıw). Onı biykarlaw "
        "ushın sol tólemdi yamasa hújjetti biykarlań."
    ),
    "CASH_ENTRY_CANCELLED": "Bul kassa jazbası álleqashan biykarlanǵan.",
    "CASH_CATEGORY_ARCHIVED": "Bul kategoriya arxivte. Basqa kategoriyanı tańlań yamasa onı arxivten shıǵarıń.",
    "CASH_CATEGORY_NAME_TAKEN": "Usı atlı kategoriya álleqashan bar (arxivte bolıwı da múmkin).",
    "CASH_CATEGORY_FIXED": "Qarıydarlar tólemi túsetuǵın kategoriyanı arxivlep yamasa óshirip bolmaydı.",
    "CASH_CATEGORY_IN_USE": "Bul kategoriyada jazbalar bar, onı óshirip bolmaydı. Arxivlew múmkin.",
    "ONLINE_PAY_OFF": "Onlayn tólem házirshe qosılmaǵan. Karta arqalı tólew: /obuna",
    "STOCK_INSUFFICIENT": "Skladta bul tovar jetkilikli emes. Aldın kiris jazıń yamasa muǵdardı azaytıń.",
    "STOCK_ALREADY_USED": (
        "Biykarlap bolmaydı: bul tovarlar skladtan álleqashan shıǵıp ketken. Támiyinlewshige qaytarıw yamasa "
        "esaptan shıǵarıw jazıń."
    ),
    "COST_CURRENCY_MISMATCH": (
        "Bul tovardıń ózine túser bahası basqa valyutada júrgiziledi. Qaldıq tawsılǵannan keyin basqa "
        "valyutada kiris islew múmkin."
    ),
    "ENTRY_OF_DOCUMENT": "Bul jazbanı hújjet jaratqan. Onı biykarlaw ushın hújjettiń ózin biykarlań.",
    "BARCODE_TAKEN": "Bul shtrix-kod basqa tovarǵa biriktirilgen.",
    "ITEM_NOT_COUNTABLE": (
        "Bul tovardı skladta esaplap bolmaydı: aldın onı kórip shıǵıń hám sklad ólshem birligin tańlań."
    ),
    "DOCUMENT_NOT_DRAFT": "Bul hújjet álleqashan ótkerilgen yamasa biykarlanǵan.",
    "DOCUMENT_CANCELLED": "Bul hújjet álleqashan biykarlanǵan.",
    "SUPPLIER_NAME_TAKEN": "Usı atlı támiyinlewshi bar (arxivte bolıwı da múmkin).",
    "SUPPLIER_ARCHIVED": "Bul támiyinlewshi arxivte. Aldın arxivten shıǵarıń.",
    "SUPPLIER_HAS_BALANCE": "Esap-kitabı jabılmaǵan támiyinlewshini arxivlep bolmaydı.",
    "RATE_LIMITED": "Sorawlar júdá kóp. Biraz kútip, qayta urınıp kóriń.",
    "TIMEOUT": "Soraw júdá uzaq dawam etti hám toqtatıldı. Hesh nárse saqlanbadı. Qayta urınıp kóriń.",
    "ERROR": "Qátelik júz berdi.",
}

PERMISSIONS: dict[str, str] = {
    "group.customers": "Qarıydarlar",
    "group.ledger": "Qarız hám tólemler",
    "group.goods": "Tovarlar",
    "group.stock": "Sklad",
    "group.suppliers": "Támiyinlewshiler",
    "group.reminders": "Eskertiwler",
    "group.reports": "Esabat hám fayllar",
    "group.cash": "Kassa",
    "group.shop": "Dúkan hám xızmetkerler",
    "group.owner": "Tek iyesi",
    "ledger.view": "Dápterdi kóriw: qarıydarlar, qarızlar, tovarlar dizimi",
    "customers.create": "Qarıydar qosıw hám onı Telegramǵa jalǵaw",
    "customers.edit": "Qarıydardı ózgertiw, limit qoyıw, arxivlew",
    "customers.share": "Qarıydarǵa oqıw siltemesin hám QR kod beriw, onı biykarlaw",
    "credits.record": "Qarızǵa sawda jazıw",
    "payments.record": "Tólem qabıllaw",
    "payment_notices.decide": "Qarıydar jibergen tólem xabarın tastıyıqlaw yamasa ret etiw",
    "entries.others": "Basqa xızmetker jazǵan sawdaǵa múddet hám tovar qosıw",
    "entries.over_limit": "Limitten asırıp qarız jazıw",
    "entries.cancel": "Jazbanı biykarlaw",
    "promises.change": "Tólew múddetin ózgertiw hám múddet sorawların sheshiw",
    "disputes.decide": "Narazılıqlardı kóriw hám sheshiw",
    "goods.edit": "Tovarlar dizimin ózgertiw",
    "stock.view": "Skladtı kóriw: qaldıq, az qalǵan tovarlar, háreketler",
    "stock.receive": "Tovar kirisi hám támiyinlewshige qaytarıw",
    "stock.adjust": "Esaptan shıǵarıw, inventarizaciya hám qarıydardan tovar qaytarıp alıw",
    "stock.costs.view": "Ózine túser bahanı, paydanı hám sklad esabatın kóriw",
    "suppliers.view": "Támiyinlewshilerdi hám olarǵa qarızdı kóriw",
    "suppliers.manage": "Támiyinlewshi qosıw, ózgertiw, arxivlew hám baslanǵısh qarızdı kiritiw",
    "suppliers.pay": "Támiyinlewshige tólem jazıw hám onı biykarlaw",
    "reminders.send": "Qarıydarǵa eskertiw jiberiw",
    "reports.view": "Esabatlardı kóriw",
    "reports.export": "Maǵlıwmattı faylǵa júklep alıw",
    "imports.run": "Fayldan maǵlıwmat júklew",
    "cash.view": "Kassanı kóriw: kúnlik dápter, qaldıqlar, dáwir esabatı",
    "cash.record_income": "Kassaǵa kiris jazıw",
    "cash.record_expense": "Kassadan shıǵıs jazıw",
    "cash.cancel": "Kassa jazbasın biykarlaw",
    "cash.categories": "Kassa kategoriyaların qosıw, ataw, arxivlew",
    "cash.backfill": "Aldıńǵı tólemlerdi kassaǵa kóshiriw",
    "settings.view": "Dúkan sazlawların kóriw",
    "settings.edit": "Qarız limiti, eskertiwler hám kassa kodı sazlawların ózgertiw",
    "shop.edit": "Dúkan atın, tilin hám ádettegi múddetin ózgertiw",
    "staff.manage": "Satıwshılardı mirát etiw, toqtatıp turıw hám shıǵarıw",
    "activity.view": "Háreketler jurnalın kóriw",
    "membership.own": "Óz ruqsatların kóriw (hár bir xızmetkerde bar)",
    "permissions.manage": "Xızmetkerlerdiń ruqsatların basqarıw",
    "ownership.transfer": "Dúkandı basqa iyege ótkeriw",
    "ownership.receive": "Iyelikti qabıllaw (menedjerge usınılǵanda)",
    "subscription.manage": "Jazılıw hám tólemler",
    "support.manage": "Qollap-quwatlaw xızmetiniń dúkanǵa kiriwin kóriw hám jabıw",
    "shop.delete": "Dúkandı óshiriw",
}
