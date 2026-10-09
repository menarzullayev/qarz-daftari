import type { StockView } from "../workspace/routes";
import { DocumentScreen, DocumentsScreen, NewDocumentScreen, ReceiptScreen } from "./DocumentScreens";
import "./messages";
import "./stock.css";
import { StockItemScreen } from "./StockItemScreen";
import { StockReportScreen } from "./StockReportScreen";
import { StockScreen } from "./StockScreen";
import { SupplierScreen, SuppliersScreen } from "./SupplierScreens";

/**
 * The screens of the stock, its documents and the suppliers (the expansion's module I): one module,
 * fetched when the first of them is opened, with its own text and styles, so that none of it is part
 * of the first load. Which of them a member reaches is decided before this: by the navigation section
 * the address belongs to, which exists only while the stock is switched on.
 */
export default function StockScreens({ view, office = false }: { view: StockView; office?: boolean }) {
  // `office`: the web panel, which has the documents' own pages to link to.
  switch (view.name) {
    case "items":
      return <StockScreen />;
    case "item":
      return <StockItemScreen key={view.itemId} itemId={view.itemId} />;
    case "receipt":
      return <ReceiptScreen />;
    case "report":
      return <StockReportScreen />;
    case "documents":
      return <DocumentsScreen />;
    case "newDocument":
      return <NewDocumentScreen key={view.kind} kind={view.kind} />;
    case "document":
      return <DocumentScreen key={view.documentId} documentId={view.documentId} />;
    case "suppliers":
      return <SuppliersScreen />;
    case "supplier":
      return <SupplierScreen key={view.supplierId} supplierId={view.supplierId} office={office} />;
  }
}
