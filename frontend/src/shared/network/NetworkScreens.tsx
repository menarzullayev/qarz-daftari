import type { NetworkView } from "../workspace/routes";
import { HomeScreen, LinkScreen } from "./LinkScreens";
import "./messages";
import { MiniScreen } from "./MiniScreen";
import "./network.css";
import { NoteScreen, NotesScreen } from "./NoteScreens";
import { ComposeScreen } from "./OrderComposer";
import { OrderScreen, OrdersScreen } from "./OrderScreens";
import { PaymentsScreen } from "./PaymentScreens";

/**
 * The screens of the network between shops (the expansion's module J): one module, fetched when the
 * first of them is opened, with its own text and styles, so that none of it is part of the first load.
 * Who reaches them is decided before this: by the navigation section every address here belongs to,
 * which exists only while the network is switched on and the member may see it.
 */
export default function NetworkScreens({ view, office = false }: { view: NetworkView; office?: boolean }) {
  // `office`: the web panel, whose first screen is the overview with its tables; the Mini App's is
  // the short one, of what awaits an answer.
  switch (view.name) {
    case "home":
      return office ? <HomeScreen /> : <MiniScreen />;
    case "link":
      return <LinkScreen key={view.linkId} linkId={view.linkId} />;
    case "orders":
      return <OrdersScreen key={view.role} role={view.role} />;
    case "compose":
      return <ComposeScreen key={view.draftId ?? ""} draftId={view.draftId} />;
    case "order":
      return <OrderScreen key={view.orderId} orderId={view.orderId} />;
    case "notes":
      return <NotesScreen key={String(view.waiting)} waiting={view.waiting} />;
    case "note":
      return <NoteScreen key={view.noteId} noteId={view.noteId} office={office} />;
    case "payments":
      return <PaymentsScreen />;
  }
}
