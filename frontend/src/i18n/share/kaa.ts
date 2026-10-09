import type { PartialCatalog } from "../types";
import type { uzShare } from "./uz";

/** Karakalpak text of a customer's read-only link, as staff see it. An absent key reads Uzbek at run time. */
export const kaaShare: PartialCatalog<typeof uzShare> = {
  "share.title": "Qarıydar ushın silteme (Telegramsız)",
  "share.explain":
    "Silteme yamasa QR kod arqalı qarıydar óz qarızın kóredi: sistemaǵa kirmesten, tek oqıw ushın. Silteme kimde bolsa, sol kóredi — onı tek qarıydardıń ózine beriń.",
  "share.none": "Bul qarıydar ushın silteme joq.",
  "share.active": "Silteme islep tur: {date} kúnine shekem.",
  "share.expired": "Siltemeniń múddeti {date} kúni tamamlanǵan. Ol endi ashılmaydı.",
  "share.opened": "Sońǵı ret ashılǵan: {date}.",
  "share.neverOpened": "Ele ashılmaǵan.",
  "share.create": "Silteme jaratıw",
  "share.replace": "Jańa silteme jaratıw",
  "share.replace.question":
    "Jańa silteme jaratılsa, aldıńǵısı sol zamatta islemey qaladı. Basıp shıǵarılǵan QR kod ta. Dawam etesiz be?",
  "share.replace.yes": "Awa, jańasın jaratıw",
  "share.revoke": "Siltemeni biykarlaw",
  "share.revoke.question": "Silteme sol zamatta islemey qaladı. Qarıydar onı asha almaydı. Biykarlaysız ba?",
  "share.revoke.yes": "Awa, biykarlaw",
  "share.revoked": "Silteme biykarlandı.",
  "share.once":
    "Silteme tek házir kórsetiledi: ol saqlanbaydı. Nusqasın alıń yamasa basıp shıǵarıń. Joǵalsa, jańasın jaratıń.",
  "share.lost": "Silteme jaratıldı, biraq onıń teksti bul ekranǵa jetip kelmedi. Jańasın jaratıń.",
  "share.caption": "Qarızıńızdı kóriw ushın usı kodtı skanerleń",
  "share.hide": "Siltemeni jasırıw",
  "share.archived": "Arxivtegi qarıydar ushın silteme jaratılmaydı.",
  "share.contact.title": "Qarıydar siltemesindegi telefon",
  "share.contact.explain":
    "Qarıydar siltemeni ashqanda dúkan atı janında usı telefon kórinedi. Bos qaldırsańız, telefon kórsetilmeydi.",
  "share.contact.label": "Dúkan telefonı",
  "share.contact.none": "Telefon kórsetilmegen.",
  "share.contact.saved": "Saqlandı.",
  "share.contact.invalid": "Telefon nomeri nadurıs. Úlgi: +998 90 123 45 67",
};
