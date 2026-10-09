import { BRAND_MARK } from "./brand";

/**
 * The brand's mark, drawn from its definition: decoration beside the product's name, which stays text.
 * Hidden from a screen reader, and colored by the tokens through the style sheet (shell.css).
 */
export function BrandMark() {
  const { canvas, corner, blocks } = BRAND_MARK;
  return (
    <svg className="shell__mark" viewBox={`0 0 ${canvas} ${canvas}`} aria-hidden="true">
      <rect className="shell__mark-ground" width={canvas} height={canvas} rx={corner} />
      {blocks.map((block) => (
        <rect key={`${block.x}-${block.y}`} className="shell__mark-block" x={block.x} y={block.y} width={block.w} height={block.h} rx={block.r} />
      ))}
    </svg>
  );
}
