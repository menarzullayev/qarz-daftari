import type { PartialCatalog } from "../types";
import type { uzSupport } from "./uz";

/** Karakalpak text of the owner's view of support access. A key that is absent here reads Uzbek at run time. */
export const kaaSupport: PartialCatalog<typeof uzSupport> = {
  "support.title": "Járdem ushın kiriw",
  "support.explain":
    "Platforma administratorı mashqalanı sheshiw ushın dúkan qarıydarların hám jazbaların waqıtsha, tek oqıw ushın kóre aladı. Ol sebep kórsetedi, kiriw kóp degende 24 saat dawam etedi hám hár bir kóriwi háreketler jurnalına jazıladı. Kiriwdi qálegen waqıtta tamamlawıńız múmkin.",
  "support.none.open": "Házir hesh bir administrator dúkan maǵlıwmatların kóre almaydı.",
  "support.open": "Házir administrator ({code}) dúkan maǵlıwmatların kóre aladı. Kiriw {date} kúni tamamlanadı.",
  "support.open.reason": "Kórsetilgen sebep: {reason}",
  "support.end": "Kiriwdi tamamlaw",
  "support.end.confirm":
    "Administratordıń ({code}) kiriwi házir tamamlansın ba? Ol dúkan maǵlıwmatların endi kóre almaydı.",
  "support.end.yes": "Awa, tamamlansın",
  "support.end.no": "Yaq",
  "support.ended": "Kiriw tamamlandı.",
  "support.history": "Kiriwler tariyxı",
  "support.history.none": "Administratorlar bul dúkanǵa ele kirmegen.",
  "support.col.admin": "Administrator",
  "support.col.reason": "Sebep",
  "support.col.from": "Baslanǵan",
  "support.col.to": "Tamamlanıw waqtı",
  "support.col.outcome": "Qalay tamamlanǵan",
  "support.outcome.active": "Házir ashıq",
  "support.outcome.expired": "Múddeti tamamlanǵan",
  "support.outcome.owner": "Iyesi tamamlaǵan · {date}",
  "support.outcome.admin": "Administrator japqan · {date}",
  "support.outcome.closed": "Jabılǵan · {date}",
  "support.banner": "Administrator házir dúkan maǵlıwmatların kóre aladı. Kiriw {date} kúni tamamlanadı.",
  "support.banner.reason": "Sebep: {reason}",
  "support.banner.open": "Kóriw hám tamamlaw",
};
